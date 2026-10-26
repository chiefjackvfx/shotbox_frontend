"""Alpha publication contracts using synthetic bytes, not native image evidence."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke import cache, storage
from shotbox_nuke.protocol import ProtocolError, validate_manifest
import test_nuke_storage as storage_tests

completed = storage_tests.completed
project_precomp = storage_tests.project_precomp
staged = storage_tests.staged


@pytest.fixture
def backend(staged):
    """A verified three-frame backend result with no original-channel snapshot."""
    staged.manifest.pop("precomp")
    staged.manifest["capture"].pop("source_files")
    shutil.rmtree(staged.root / "source_v001")
    for legacy_rgba in staged.root.glob("*.exr"):
        legacy_rgba.unlink()
    staged.manifest["settings"]["precision"] = "half"
    cache.atomic_write_json(staged.receipt, staged.manifest)
    assert cache.validate_cache(staged.receipt)["valid"]
    return staged


def _alpha_descriptor(manifest):
    width, height = manifest["capture"]["width"], manifest["capture"]["height"]
    records = deepcopy(manifest["outputs"])
    for record in records:
        record.update(geometry={"width": width, "height": height}, box=[0, 0, width, height],
                      pixel_aspect=1.0, channels=["rgba.alpha"])
    return {"kind": "alpha", "precision": manifest["settings"]["precision"], "state": "complete",
            "pattern": "matte.%04d.exr", "first": manifest["capture"]["start"],
            "last": manifest["capture"]["end"], "channels": ["rgba.alpha"], "files": records}


@pytest.mark.parametrize("precision", ["half", "float"])
def test_promote_alpha_preserves_backend_bytes_records_and_precision(backend, precision):
    backend.manifest["settings"]["precision"] = precision
    original = deepcopy(backend.manifest)
    contents = {record["path"]: (backend.root / record["path"]).read_bytes() for record in original["outputs"]}
    descriptor = storage.promote_alpha(backend.root, backend.manifest)
    assert descriptor["kind"] == "alpha" and descriptor["channels"] == ["rgba.alpha"]
    assert descriptor["precision"] == precision
    assert (descriptor["first"], descriptor["last"]) == (-2, 0)
    assert len(descriptor["files"]) == 3
    assert backend.manifest == original
    for output, record in zip(original["outputs"], descriptor["files"]):
        assert record["path"] == output["path"]
        assert record["sha256"] == output["sha256"] and record["size"] == output["size"]
        assert (backend.root / output["path"]).read_bytes() == contents[output["path"]]
        assert record["geometry"] == {"width": 32, "height": 16}
        assert record["box"] == [0, 0, 32, 16] and record["channels"] == ["rgba.alpha"]
    descriptor["files"][0]["geometry"]["width"] = 1
    assert backend.manifest["outputs"] == original["outputs"]


@pytest.mark.parametrize("precision", ["half", "float"])
def test_alpha_commit_maps_original_timeline_without_reencoding_and_cleans_work(backend, precision):
    backend.manifest["settings"]["precision"] = precision
    backend_hashes = [record["sha256"] for record in backend.manifest["outputs"]]
    backend.manifest["precomp"] = storage.promote_alpha(backend.root, backend.manifest)
    storage.commit(backend.root, backend.manifest)
    expected = [backend.output / name for name in ("hero_v01.-002.exr", "hero_v01.-001.exr", "hero_v01.0000.exr")]
    assert {path.name for path in backend.output.iterdir()} == {path.name for path in expected}
    assert [cache.sha256_file(path) for path in expected] == backend_hashes
    assert backend.manifest["precomp"]["kind"] == "alpha"
    assert backend.manifest["precomp"]["precision"] == precision
    storage.cleanup(backend.root, backend.manifest)
    assert list(backend.root.iterdir()) == [backend.receipt]
    report = cache.validate_cache(backend.receipt)
    assert report["valid"] and report["precomp_files"] == [str(path) for path in expected]
    assert [cache.sha256_file(path) for path in expected] == backend_hashes
    assert report["outputs"] == []


def test_alpha_publication_preserves_existing_output_on_collision(backend):
    backend.manifest["precomp"] = storage.promote_alpha(backend.root, backend.manifest)
    existing = backend.output / "hero_v01.-002.exr"
    existing.write_bytes(b"existing artist alpha")
    source_hashes = [cache.sha256_file(backend.root / item["path"]) for item in backend.manifest["outputs"]]
    with pytest.raises(RuntimeError, match="empty"):
        storage.commit(backend.root, backend.manifest)
    assert existing.read_bytes() == b"existing artist alpha"
    assert [cache.sha256_file(backend.root / item["path"]) for item in backend.manifest["outputs"]] == source_hashes
    assert "storage" not in backend.manifest


@pytest.mark.parametrize("damage", ["missing", "truncated", "same_size_corrupt"])
def test_promote_alpha_rejects_changed_backend_files(backend, damage):
    record = backend.manifest["outputs"][0]
    path = backend.root / record["path"]
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"x" * (record["size"] if damage == "same_size_corrupt" else 1))
    with pytest.raises((ValueError, RuntimeError, OSError)):
        storage.promote_alpha(backend.root, backend.manifest)
    assert list(backend.output.iterdir()) == []


@pytest.mark.parametrize("mutate", [
    lambda m: m["outputs"].pop(),
    lambda m: m["outputs"].reverse(),
    lambda m: m["outputs"][1].update(path=m["outputs"][0]["path"]),
    lambda m: m["outputs"][0].update(path="../outside.exr"),
    lambda m: m["settings"].update(precision="uint16"),
])
def test_promote_alpha_rejects_incomplete_or_invalid_backend_mapping(backend, mutate):
    mutate(backend.manifest)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        storage.promote_alpha(backend.root, backend.manifest)


def test_promote_alpha_rejects_symlink_escape(backend, tmp_path):
    record = backend.manifest["outputs"][0]
    source = backend.root / record["path"]
    outside = tmp_path / "outside_matte.exr"
    outside.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(ProtocolError, match="escapes"):
        storage.promote_alpha(backend.root, backend.manifest)


@pytest.mark.parametrize("precision", ["half", "float"])
def test_alpha_protocol_accepts_no_rgb_snapshot_and_single_a(backend, precision):
    backend.manifest["settings"]["precision"] = precision
    backend.manifest["precomp"] = _alpha_descriptor(backend.manifest)
    assert "source_files" not in backend.manifest["capture"]
    assert validate_manifest(backend.manifest, require_complete=True)["precomp"]["precision"] == precision


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(kind="unknown"),
    lambda p: p.update(precision="uint16"),
    lambda p: p.update(precision=None),
    lambda p: p.update(channels=["rgba.red", "rgba.alpha"]),
    lambda p: p["files"][0].update(channels=["rgba.red", "rgba.alpha"]),
    lambda p: p["files"][0].update(channels=["rgba.alpha", "rgba.alpha"]),
    lambda p: p["files"][0].update(channels=["rgba.red"]),
    lambda p: p["files"][0].update(channels=[]),
    lambda p: p["files"][0].update(box=[-1, 0, 32, 16]),
    lambda p: p["files"][0].update(box=[0, 0, 33, 16]),
    lambda p: p["files"][0].update(geometry={"width": 31, "height": 16}),
])
def test_alpha_protocol_rejects_wrong_kind_precision_channels_or_format(backend, mutate):
    descriptor = _alpha_descriptor(backend.manifest)
    mutate(descriptor)
    backend.manifest["precomp"] = descriptor
    with pytest.raises(ProtocolError):
        validate_manifest(backend.manifest, require_complete=True)


def test_legacy_rgba_contract_remains_float_with_immutable_source_snapshot(staged):
    assert "kind" not in staged.manifest["precomp"]
    assert validate_manifest(staged.manifest, require_complete=True)
    staged.manifest["capture"].pop("source_files")
    with pytest.raises(ProtocolError):
        validate_manifest(staged.manifest, require_complete=True)


def test_legacy_rgba_contract_still_rejects_half(staged):
    staged.manifest["precomp"]["precision"] = "half"
    with pytest.raises(ProtocolError):
        validate_manifest(staged.manifest, require_complete=True)
