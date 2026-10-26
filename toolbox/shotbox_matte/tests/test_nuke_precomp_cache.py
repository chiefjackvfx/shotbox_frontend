"""Disk/manifest contract tests using synthetic bytes, not rendered EXR evidence."""
import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke.cache import atomic_write_json, file_record, relink_cache, validate_cache
from shotbox_nuke.protocol import FrameMap, ProtocolError, validate_manifest
import test_nuke_cache as legacy_cache_tests

# Reuse the existing complete legacy cache fixture without duplicating its setup.
completed = legacy_cache_tests.completed


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _update(path, mutate):
    manifest = _read(path)
    mutate(manifest)
    atomic_write_json(path, manifest)


@pytest.fixture
def project_precomp(completed, tmp_path):
    """Expand the existing valid legacy manifest into a project RGBA precomp."""
    original = completed.parent
    root = tmp_path / "bam030/renders/precomp/hero_v01"
    root.parent.mkdir(parents=True)
    shutil.move(str(original), root)
    path = root / "integration.json"
    manifest = _read(path)
    manifest["original_attempt_root"] = str(root)
    for key, value in manifest["result"].items():
        if isinstance(value, str) and value.startswith(str(original)):
            manifest["result"][key] = str(root) + value[len(str(original)):]
    capture = manifest["capture"]
    capture.update(start=-2, end=0, count=3)
    manifest["frame_map"] = FrameMap(-2, 0).as_dict()
    manifest["result"]["frame_count"] = 3
    geometry = {"width": 32, "height": 16}
    channels = ["rgba.red", "rgba.green", "rgba.blue", "rgba.alpha", "depth.Z"]
    capture["files"], capture["source_files"], manifest["outputs"] = [], [], []
    (root / "source_v001").mkdir()
    precomp_files = []
    for index, frame in enumerate(range(-2, 1), 1):
        # Real decode/channel validation belongs to the native Nuke host tests.
        captured = root / ("capture_v001/capture.%04d.exr" % index)
        source = root / ("source_v001/source.%04d.exr" % index)
        matte = root / ("capture_matte_v002/capture_matte_v002.%04d.exr" % index)
        precomp = root / ("hero_v01.%04d.exr" % frame)
        for output, content in ((captured, b"inference RGB snapshot"), (source, b"all original channel snapshot"),
                                (matte, b"generated alpha only"), (precomp, b"synthetic original RGB plus alpha")):
            output.write_bytes(content + str(index).encode("ascii"))
        capture["files"].append(file_record(captured, root, geometry=geometry))
        capture["source_files"].append(file_record(source, root, geometry=geometry))
        manifest["outputs"].append(file_record(matte, root, geometry=geometry))
        precomp_files.append(file_record(precomp, root, geometry=geometry, box=[-4, -2, 36, 18],
                                         pixel_aspect=1.0, channels=channels))
    manifest["precomp"] = {"state": "complete", "precision": "float", "first": -2, "last": 0,
                          "pattern": "hero_v01.%04d.exr", "channels": channels, "files": precomp_files}
    atomic_write_json(root / manifest["backend_manifest"],
                      {"status": "complete", "frame_count": 3,
                       "frames": [Path(record["path"]).name for record in manifest["outputs"]]})
    atomic_write_json(path, manifest)
    return path


def test_complete_project_precomp_uses_timeline_frames_and_preserves_matte_records(project_precomp):
    result = validate_cache(project_precomp)
    root = project_precomp.parent
    assert result["valid"] and result["checksums_verified"]
    assert root.name == "hero_v01" and root.parent.name == "precomp"
    assert result["output_dir"] == str(root)
    assert result["matte_output_dir"] == str(root / "capture_matte_v002")
    assert [Path(path).name for path in result["precomp_files"]] == [
        "hero_v01.-002.exr", "hero_v01.-001.exr", "hero_v01.0000.exr"]
    assert [Path(path).name for path in result["outputs"]] == [
        "capture_matte_v002.0001.exr", "capture_matte_v002.0002.exr", "capture_matte_v002.0003.exr"]
    assert "depth.Z" in result["manifest"]["precomp"]["files"][0]["channels"]


def test_precomp_relink_uses_owned_relative_paths(project_precomp, tmp_path):
    original_worker = (project_precomp.parent / "worker.json").read_bytes()
    moved = tmp_path / "moved_project/precomp/hero_v01"
    moved.parent.mkdir(parents=True)
    shutil.move(str(project_precomp.parent), moved)
    result = relink_cache(moved / "integration.json")
    assert result["valid"]
    assert result["precomp_files"][0] == str(moved / "hero_v01.-002.exr")
    assert result["matte_output_dir"] == str(moved / "capture_matte_v002")
    assert (moved / "worker.json").read_bytes() == original_worker


@pytest.mark.parametrize("kind", ["precomp", "source"])
@pytest.mark.parametrize("damage", ["missing", "truncated", "same_size_corrupt"])
def test_missing_or_changed_precomp_and_source_cannot_activate(project_precomp, kind, damage):
    manifest = _read(project_precomp)
    record = (manifest["precomp"]["files"] if kind == "precomp" else manifest["capture"]["source_files"])[0]
    path = project_precomp.parent / record["path"]
    if damage == "missing":
        path.unlink()
    elif damage == "truncated":
        path.write_bytes(b"x")
    else:
        path.write_bytes(b"x" * record["size"])
    with pytest.raises(ProtocolError, match="[Pp]recomp/source"):
        validate_cache(project_precomp)


@pytest.mark.parametrize("mutate", [
    lambda m: m["precomp"]["files"].pop(),
    lambda m: m["capture"]["source_files"].pop(),
    lambda m: m["precomp"].update(first=-1),
    lambda m: m["precomp"].update(last=1),
    lambda m: m["precomp"]["files"].reverse(),
    lambda m: m["capture"]["source_files"].reverse(),
    lambda m: m["precomp"]["files"][0].update(path="hero_v01.0001.exr"),
    lambda m: m["capture"]["source_files"][0].update(path="source_v001/source.0000.exr"),
    lambda m: m["precomp"]["files"][1].update(path=m["precomp"]["files"][0]["path"]),
    lambda m: m["capture"]["source_files"][1].update(path=m["capture"]["source_files"][0]["path"]),
])
def test_incorrect_precomp_and_source_mapping_is_rejected(project_precomp, mutate):
    _update(project_precomp, mutate)
    with pytest.raises(ProtocolError):
        validate_cache(project_precomp)


@pytest.mark.parametrize("field", ["precomp_path", "precomp_pattern", "source_path"])
@pytest.mark.parametrize("escape", ["../outside.exr", "/tmp/outside.exr", "C:/outside.exr", "a\\outside.exr"])
def test_owned_precomp_and_source_paths_cannot_escape(project_precomp, field, escape):
    def mutate(manifest):
        if field == "precomp_pattern":
            manifest["precomp"]["pattern"] = escape.replace(".exr", ".%04d.exr")
        elif field == "precomp_path":
            manifest["precomp"]["files"][0]["path"] = escape
        else:
            manifest["capture"]["source_files"][0]["path"] = escape
    _update(project_precomp, mutate)
    with pytest.raises(ProtocolError):
        validate_cache(project_precomp)


@pytest.mark.parametrize("kind", ["precomp", "source"])
def test_precomp_and_source_symlink_escapes_are_rejected(project_precomp, tmp_path, kind):
    manifest = _read(project_precomp)
    record = (manifest["precomp"]["files"] if kind == "precomp" else manifest["capture"]["source_files"])[0]
    path = project_precomp.parent / record["path"]
    outside = tmp_path / "outside.exr"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(ProtocolError, match="escapes"):
        validate_cache(project_precomp)


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(state="validating"),
    lambda m: m["precomp"].update(state="rendering"),
    lambda m: m["precomp"].update(state="failed"),
    lambda m: m["precomp"].pop("state"),
    lambda m: m["precomp"].pop("files"),
    lambda m: m["capture"].pop("source_files"),
    lambda m: m["validation"].update(valid=False),
])
def test_partial_publications_are_rejected(project_precomp, mutate):
    _update(project_precomp, mutate)
    with pytest.raises(ProtocolError):
        validate_cache(project_precomp)


@pytest.mark.parametrize("mutate", [
    lambda m: m["precomp"].update(precision="half"),
    lambda m: m["precomp"].update(pattern="hero_v01.####.exr"),
    lambda m: m["precomp"].update(pattern="hero_v01.%04d.%04d.exr"),
    lambda m: m["precomp"].update(pattern="hero_v01.%04d.png"),
    lambda m: m["precomp"]["files"][0].update(geometry={"width": 31, "height": 16}),
    lambda m: m["precomp"]["files"][0].update(box=[0, 0, 0, 16]),
    lambda m: m["precomp"]["files"][0].update(box=[0, 0, 32]),
    lambda m: m["precomp"]["files"][0].update(box=[0, 0, 32.0, 16]),
    lambda m: m["precomp"]["files"][0].update(pixel_aspect=2.0),
    lambda m: m["precomp"]["files"][0].pop("channels"),
    lambda m: m["precomp"]["files"][0].update(channels=["rgba.red", "rgba.green", "rgba.blue"]),
    lambda m: m["precomp"]["files"][0].update(channels=["rgba.alpha"]),
    lambda m: m["precomp"]["files"][0].update(channels="rgba.red rgba.green rgba.blue rgba.alpha"),
])
def test_protocol_rejects_incomplete_rgba_and_invalid_precomp_format(project_precomp, mutate):
    manifest = _read(project_precomp)
    mutate(manifest)
    with pytest.raises(ProtocolError):
        validate_manifest(manifest, require_complete=True)


def test_legacy_cache_without_precomp_still_validates(completed):
    result = validate_cache(completed)
    assert result["valid"]
    assert result["precomp_files"] == []
    assert result["output_dir"] == str(completed.parent / "capture_matte_v002")
