"""Publication/cleanup transaction tests with synthetic bytes and temporary files."""
from copy import deepcopy
import errno
import hashlib
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke import cache, preferences, storage
from shotbox_nuke.protocol import ProtocolError
import test_nuke_precomp_cache as full_cache_tests

completed = full_cache_tests.completed
project_precomp = full_cache_tests.project_precomp


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _hashes(directory):
    return {path.name: cache.sha256_file(path) for path in directory.glob("*.exr")}


@pytest.fixture
def staged(project_precomp, tmp_path):
    previous = project_precomp.parent
    manifest = _read(project_precomp)
    root = tmp_path / "working" / manifest["node_id"] / manifest["attempt_id"]
    root.parent.mkdir(parents=True)
    shutil.move(str(previous), root)
    manifest["original_attempt_root"] = str(root)
    for key, value in manifest["result"].items():
        if isinstance(value, str) and value.startswith(str(previous)):
            manifest["result"][key] = str(root) + value[len(str(previous)):]
    output = tmp_path / "renders/precomp/hero_v01"
    output.mkdir(parents=True)
    manifest["precomp_request"] = {"output_directory": str(output), "pattern": "hero_v01.%04d.exr"}
    cache.atomic_write_json(root / "integration.json", manifest)
    return SimpleNamespace(root=root, output=output, manifest=manifest, receipt=root / "integration.json")


def test_commit_leaves_only_final_exrs_in_output_and_preserves_provenance(staged):
    staged_hashes = _hashes(staged.root)
    previous = deepcopy(staged.manifest)
    returned = storage.commit(staged.root, staged.manifest)
    assert returned is staged.manifest
    assert _hashes(staged.output) == staged_hashes
    assert {path.name for path in staged.output.iterdir()} == set(staged_hashes)
    assert staged.manifest["capture"] == previous["capture"]
    assert staged.manifest["result"] == previous["result"]
    assert staged.manifest["storage"] == {"mode": "published", "output_directory": str(staged.output)}
    assert cache.validate_cache(staged.receipt)["precomp_root"] == str(staged.output)


def test_commit_handles_cross_filesystem_copy(staged, monkeypatch):
    def cross_device(*args):
        raise OSError(errno.EXDEV, "separate volume")
    monkeypatch.setattr(storage.os, "link", cross_device)
    before = _hashes(staged.root)
    storage.commit(staged.root, staged.manifest)
    assert _hashes(staged.output) == before
    assert all((staged.output / name).stat().st_ino != (staged.root / name).stat().st_ino for name in before)


def test_commit_refuses_existing_artist_file_without_overwrite(staged):
    artist = staged.output / "hero_v01.-002.exr"
    artist.write_bytes(b"keep existing artist output")
    original = staged.receipt.read_bytes()
    with pytest.raises(RuntimeError, match="empty"):
        storage.commit(staged.root, staged.manifest)
    assert artist.read_bytes() == b"keep existing artist output"
    assert staged.receipt.read_bytes() == original
    assert "storage" not in staged.manifest


def test_mid_copy_failure_removes_only_files_created_by_this_commit(staged, monkeypatch):
    original_copy = storage._copy_exclusive
    original_receipt = staged.receipt.read_bytes()
    count = 0
    def collision(source, destination):
        nonlocal count
        count += 1
        if count == 2:
            destination.write_bytes(b"artist won the race")
        original_copy(source, destination)
    monkeypatch.setattr(storage, "_copy_exclusive", collision)
    with pytest.raises(FileExistsError):
        storage.commit(staged.root, staged.manifest)
    assert list(staged.output.iterdir()) == [staged.output / "hero_v01.-001.exr"]
    assert (staged.output / "hero_v01.-001.exr").read_bytes() == b"artist won the race"
    assert staged.receipt.read_bytes() == original_receipt


def test_late_validation_failure_rolls_back_receipt_and_output(staged, monkeypatch):
    original_receipt = staged.receipt.read_bytes()
    original_manifest = deepcopy(staged.manifest)
    def reject(*args, **kwargs):
        raise ProtocolError("injected final receipt validation failure")
    monkeypatch.setattr(cache, "validate_cache", reject)
    with pytest.raises(ProtocolError, match="injected"):
        storage.commit(staged.root, staged.manifest)
    assert list(staged.output.iterdir()) == []
    assert staged.receipt.read_bytes() == original_receipt
    assert staged.manifest == original_manifest


def test_failed_first_publication_removes_new_receipt(staged, monkeypatch):
    staged.receipt.unlink()
    def reject(*args, **kwargs):
        raise ProtocolError("injected validation failure")
    monkeypatch.setattr(cache, "validate_cache", reject)
    with pytest.raises(ProtocolError, match="injected"):
        storage.commit(staged.root, staged.manifest)
    assert not staged.receipt.exists()
    assert list(staged.output.iterdir()) == []
    assert "storage" not in staged.manifest


def test_changed_staged_precomp_is_rejected_before_publication(staged):
    record = staged.manifest["precomp"]["files"][0]
    (staged.root / record["path"]).write_bytes(b"x" * record["size"])
    with pytest.raises(RuntimeError, match="changed"):
        storage.commit(staged.root, staged.manifest)
    assert list(staged.output.iterdir()) == []
    assert "storage" not in _read(staged.receipt)


def test_cleanup_published_owned_uuid_job_retains_only_receipt_and_final_images(staged):
    storage.commit(staged.root, staged.manifest)
    final_hashes = _hashes(staged.output)
    removed = storage.cleanup(staged.root, staged.manifest)
    assert "capture_v001" in removed and "source_v001" in removed and "backend_jobs" in removed
    assert list(staged.root.iterdir()) == [staged.receipt]
    assert _hashes(staged.output) == final_hashes
    assert _read(staged.receipt)["working_files_removed"] is True
    assert cache.validate_cache(staged.receipt)["valid"]


@pytest.mark.parametrize("state", ["failed", "cancelled", "interrupted"])
def test_cleanup_failed_owned_job_removes_working_files_and_empty_reservation(staged, state):
    staged.manifest["state"] = state
    cache.atomic_write_json(staged.receipt, staged.manifest)
    storage.cleanup(staged.root, staged.manifest)
    assert list(staged.root.iterdir()) == [staged.receipt]
    assert not staged.output.exists()
    assert _read(staged.receipt)["state"] == state


def test_failure_cleanup_preserves_nonempty_output_folder(staged):
    staged.manifest["state"] = "failed"
    artist = staged.output / "artist_note.txt"
    artist.write_text("keep me")
    storage.cleanup(staged.root, staged.manifest)
    assert artist.read_text() == "keep me"


def test_cleanup_refuses_live_job_without_removing_files(staged):
    staged.manifest["state"] = "generating"
    before = sorted(path.name for path in staged.root.iterdir())
    with pytest.raises(ValueError, match="finished"):
        storage.cleanup(staged.root, staged.manifest)
    assert sorted(path.name for path in staged.root.iterdir()) == before


def test_cleanup_refuses_complete_unpublished_result(staged):
    before = sorted(path.name for path in staged.root.iterdir())
    with pytest.raises(ValueError, match="published precomp"):
        storage.cleanup(staged.root, staged.manifest)
    assert sorted(path.name for path in staged.root.iterdir()) == before
    assert cache.validate_cache(staged.receipt)["valid"]


def test_cleanup_refuses_symlink_root_and_preserves_target(staged, tmp_path):
    link = tmp_path / "job symlink"
    link.symlink_to(staged.root, target_is_directory=True)
    with pytest.raises(ValueError, match="owned UUID"):
        storage.cleanup(link, staged.manifest)
    assert (staged.root / "worker.json").exists()


def test_cleanup_unlinks_nested_symlink_without_touching_outside_files(staged, tmp_path):
    staged.manifest["state"] = "failed"
    outside = tmp_path / "artist source"
    outside.mkdir()
    preserved = outside / "source.exr"
    preserved.write_bytes(b"must not be removed")
    (staged.root / "outside_link").symlink_to(outside, target_is_directory=True)
    storage.cleanup(staged.root, staged.manifest)
    assert preserved.read_bytes() == b"must not be removed"
    assert not (staged.root / "outside_link").exists()


def test_cleanup_refuses_matching_names_that_are_not_uuids(tmp_path):
    root = tmp_path / "artist" / "work"
    root.mkdir(parents=True)
    preserved = root / "important.exr"
    preserved.write_bytes(b"artist file")
    manifest = {"node_id": "artist", "attempt_id": "work", "original_attempt_root": str(root), "state": "failed"}
    with pytest.raises(ValueError, match="UUID"):
        storage.cleanup(root, manifest)
    assert preserved.read_bytes() == b"artist file"


def test_cleanup_refuses_changed_ownership_without_removing_files(staged):
    staged.manifest["attempt_id"] = str(uuid.uuid4())
    with pytest.raises(ValueError, match="owned UUID"):
        storage.cleanup(staged.root, staged.manifest)
    assert (staged.root / "worker.json").exists()


def test_legacy_migration_preserves_hashes_and_old_script_reference(project_precomp, tmp_path, monkeypatch):
    root = project_precomp.parent
    before = _hashes(root)
    artist = root / "artist_notes.txt"
    artist.write_text("preserve artist notes")
    cache_root = tmp_path / "local_cache"
    monkeypatch.setattr(preferences, "load", lambda: {"cache_root": str(cache_root), "application_root": ""})
    result = storage.migrate_legacy(project_precomp, cache_root)
    assert result["exrs_unchanged"] is True and result["exr_sha256"] == before
    assert _hashes(root) == before
    assert artist.read_text() == "preserve artist notes"
    assert not project_precomp.exists()
    assert {path.name for path in root.iterdir()} == set(before) | {"artist_notes.txt"}
    receipt = Path(result["receipt"])
    alias = cache_root / "aliases" / (hashlib.sha256(str(project_precomp).encode("utf-8")).hexdigest() + ".json")
    assert _read(alias) == {"schema_version": 1, "original": str(project_precomp), "manifest": str(receipt)}
    assert cache.resolve_manifest_path(project_precomp) == receipt
    assert cache.validate_cache(project_precomp)["valid"]
    assert storage.migrate_legacy(project_precomp, cache_root) == {"receipt": str(receipt), "already_clean": True}


def test_legacy_migration_keeps_original_files_when_alias_commit_fails(project_precomp, tmp_path, monkeypatch):
    cache_root = tmp_path / "local_cache"
    before = _hashes(project_precomp.parent)
    original = cache.atomic_write_json
    def fail_alias(path, value):
        if Path(path).parent.name == "aliases":
            raise OSError("injected alias write failure")
        original(path, value)
    monkeypatch.setattr(cache, "atomic_write_json", fail_alias)
    with pytest.raises(OSError, match="alias write"):
        storage.migrate_legacy(project_precomp, cache_root)
    assert project_precomp.exists()
    assert (project_precomp.parent / "source_v001").exists()
    assert _hashes(project_precomp.parent) == before
    assert cache.validate_cache(project_precomp)["valid"]


def test_legacy_migration_refuses_alpha_only_complete_cache(completed, tmp_path):
    with pytest.raises(ValueError, match="Alpha-only"):
        storage.migrate_legacy(completed, tmp_path / "local_cache")
    assert completed.exists()
    assert cache.validate_cache(completed)["valid"]


def test_legacy_migration_refuses_private_receipts_inside_public_folder(project_precomp):
    root = project_precomp.parent
    before = sorted(path.name for path in root.iterdir())
    with pytest.raises(ValueError, match="outside"):
        storage.migrate_legacy(project_precomp, root / "private_cache")
    assert sorted(path.name for path in root.iterdir()) == before
    assert cache.validate_cache(project_precomp)["valid"]


def test_legacy_migration_retains_working_files_if_receipt_validation_fails(project_precomp, tmp_path, monkeypatch):
    original = project_precomp.read_bytes()
    def reject(*args, **kwargs):
        raise ProtocolError("injected invalid publication receipt")
    monkeypatch.setattr(cache, "validate_cache", reject)
    cache_root = tmp_path / "private_cache"
    with pytest.raises(ProtocolError, match="injected"):
        storage.migrate_legacy(project_precomp, cache_root)
    assert project_precomp.read_bytes() == original
    assert (project_precomp.parent / "source_v001").is_dir()
    assert not (cache_root / "aliases").exists()
