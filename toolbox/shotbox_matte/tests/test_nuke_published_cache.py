"""Compact receipt validation using synthetic files, not decoded image evidence."""
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke.cache import atomic_write_json, resolve_precomp_root, validate_cache
from shotbox_nuke.protocol import ProtocolError, validate_manifest
import test_nuke_precomp_cache as full_cache_tests

completed = full_cache_tests.completed
project_precomp = full_cache_tests.project_precomp


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _update(path, mutate):
    manifest = _read(path)
    mutate(manifest)
    atomic_write_json(path, manifest)


@pytest.fixture
def published_receipt(project_precomp, tmp_path):
    manifest = _read(project_precomp)
    output = project_precomp.parent
    manifest["storage"] = {"mode": "published", "output_directory": str(output), "cleaned": True}
    receipt = tmp_path / "ShotBox receipts" / manifest["attempt_id"] / "integration.json"
    atomic_write_json(receipt, manifest)
    final_names = {record["path"] for record in manifest["precomp"]["files"]}
    # These files are created by the fixture in a pytest-owned temporary folder.
    for item in output.iterdir():
        if item.name not in final_names:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
    return receipt


def test_published_receipt_validates_only_final_exrs_after_bulk_is_removed(published_receipt):
    result = validate_cache(published_receipt)
    manifest = result["manifest"]
    root = Path(manifest["storage"]["output_directory"])
    assert result["valid"] and result["checksums_verified"]
    assert result["attempt_root"] == str(published_receipt.parent)
    assert result["precomp_root"] == result["output_dir"] == str(root)
    assert result["matte_output_dir"] == "" and result["outputs"] == []
    assert result["precomp_files"] == [str(root / record["path"]) for record in manifest["precomp"]["files"]]
    assert {item.name for item in root.iterdir()} == {record["path"] for record in manifest["precomp"]["files"]}
    assert not (root / manifest["worker_path"]).exists()
    assert not (root / manifest["backend_manifest"]).exists()
    assert all(not (root / record["path"]).exists() for record in manifest["capture"]["source_files"])
    assert result["source_snapshot"] == "validated before publication; no longer retained"


def test_published_mode_can_validate_before_cleanup_finishes(project_precomp, tmp_path):
    manifest = _read(project_precomp)
    manifest["storage"] = {"mode": "published", "output_directory": str(project_precomp.parent), "cleaned": False}
    receipt = tmp_path / "receipt/integration.json"
    atomic_write_json(receipt, manifest)
    assert validate_cache(receipt)["valid"]


def test_receipt_may_move_without_moving_or_rewriting_final_images(published_receipt, tmp_path):
    manifest = _read(published_receipt)
    original = Path(manifest["storage"]["output_directory"])
    moved = tmp_path / "moved receipt.json"
    shutil.move(str(published_receipt), moved)
    assert validate_cache(moved)["output_dir"] == str(original)
    assert resolve_precomp_root(manifest, moved.parent) == original


def test_legacy_precomp_root_remains_manifest_directory(project_precomp):
    assert resolve_precomp_root(_read(project_precomp), project_precomp.parent) == project_precomp.parent


@pytest.mark.parametrize("damage", ["missing", "truncated", "tampered", "extra_frame", "all_removed"])
def test_missing_tampered_or_extra_published_frames_rejected(published_receipt, damage):
    manifest = _read(published_receipt)
    output = Path(manifest["storage"]["output_directory"])
    record = manifest["precomp"]["files"][0]
    first = output / record["path"]
    if damage == "missing":
        first.unlink()
    elif damage == "truncated":
        first.write_bytes(b"x")
    elif damage == "tampered":
        first.write_bytes(b"x" * record["size"])
    elif damage == "extra_frame":
        (output / "hero_v01.0001.exr").write_bytes(b"unexpected frame")
    else:
        shutil.rmtree(output)
    with pytest.raises(ProtocolError):
        validate_cache(published_receipt)


def test_published_symlink_cannot_point_outside_output_directory(published_receipt, tmp_path):
    manifest = _read(published_receipt)
    first = Path(manifest["storage"]["output_directory"]) / manifest["precomp"]["files"][0]["path"]
    outside = tmp_path / "outside.exr"
    outside.write_bytes(first.read_bytes())
    first.unlink()
    first.symlink_to(outside)
    with pytest.raises(ProtocolError, match="escapes"):
        validate_cache(published_receipt)


@pytest.mark.parametrize("storage", [None, {}, {"mode": "cache"},
    {"mode": "published", "output_directory": ""},
    {"mode": "published", "output_directory": "Root"},
    {"mode": "published", "output_directory": "relative/precomp"},
    {"mode": "published", "output_directory": "/"},
    {"mode": "published", "output_directory": "C:/"},
    {"mode": "published", "output_directory": "C:relative"},
    {"mode": "published", "output_directory": "/tmp/../"},
    {"mode": "published", "output_directory": "/tmp/[value root.name]"},
])
def test_invalid_published_storage_rejected_by_protocol(published_receipt, storage):
    manifest = _read(published_receipt)
    manifest["storage"] = storage
    with pytest.raises(ProtocolError):
        validate_manifest(manifest)


@pytest.mark.parametrize("directory", ["C:/projects/bam030/precomp/hero_v01", "//studio/jobs/bam030/precomp/hero_v01"])
def test_windows_and_unc_absolute_output_directory_protocol_support(published_receipt, directory):
    manifest = _read(published_receipt)
    manifest["storage"]["output_directory"] = directory
    assert validate_manifest(manifest)["storage"]["output_directory"] == directory


@pytest.mark.parametrize("mutate", [
    lambda m: m.pop("precomp"),
    lambda m: m.update(state="validating"),
    lambda m: m.update(worker_exit=1),
    lambda m: m["validation"].update(valid=False),
    lambda m: m["precomp"].update(state="writing"),
    lambda m: m["precomp"]["files"].pop(),
    lambda m: m["precomp"]["files"].reverse(),
    lambda m: m["precomp"]["files"][0].update(path="../hero_v01.-002.exr"),
    lambda m: m["precomp"]["files"][0].update(path="nested/hero_v01.-002.exr"),
    lambda m: m["precomp"]["files"][0].update(path="C:/hero_v01.-002.exr"),
    lambda m: m["precomp"]["files"][0].update(path="[value root.name].exr"),
    lambda m: m["precomp"].update(pattern="../hero_v01.%04d.exr"),
    lambda m: m["precomp"].update(pattern="nested/hero_v01.%04d.exr"),
])
def test_published_receipt_requires_complete_and_owned_final_sequence(published_receipt, mutate):
    manifest = _read(published_receipt)
    mutate(manifest)
    # A published receipt is complete even when the caller did not explicitly
    # request require_complete; it must never represent a partial publication.
    with pytest.raises(ProtocolError):
        validate_manifest(manifest)


def test_explicit_checksum_skip_does_not_claim_verification(published_receipt):
    manifest = _read(published_receipt)
    record = manifest["precomp"]["files"][0]
    first = Path(manifest["storage"]["output_directory"]) / record["path"]
    first.write_bytes(b"x" * record["size"])
    result = validate_cache(published_receipt, checksums=False)
    assert result["valid"] and result["checksums_verified"] is False
