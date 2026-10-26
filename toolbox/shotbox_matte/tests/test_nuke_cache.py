import json
from pathlib import Path
import shutil
import sys
import uuid
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke.cache import (allocate_attempt, atomic_write_json, file_record, read_manifest,
                                relink_cache, resolve_owned, sha256_file, validate_cache)
from shotbox_nuke.preferences import load, resolve_interpreter, save, suggested_cache_root
from shotbox_nuke.protocol import FrameMap, ProtocolError


@pytest.fixture
def completed(tmp_path):
    root = allocate_attempt(tmp_path / "cache with 漢字 spaces", str(uuid.uuid4()))
    worker = root / "worker.json"
    worker.write_text('{"input_path":"original absolute capture path"}')
    capture = root / "capture_v001/capture.0001.exr"
    capture.write_bytes(b"recorded capture bytes")
    output = root / "capture_matte_v002"
    output.mkdir()
    first = output / "capture_matte_v002.0001.exr"
    first.write_bytes(b"recorded output bytes")
    backend = root / "backend_jobs/job/_shotbox_job.json"
    atomic_write_json(backend, {"status": "complete", "frame_count": 1, "frames": [first.name]})
    geometry = {"width": 32, "height": 16}
    manifest = {"schema_version": 1, "integration_version": "0.1.0", "node_id": root.parent.name,
                "attempt_id": root.name, "state": "complete", "created_at": "2026-10-23T00:00:00Z",
                "original_attempt_root": str(root), "worker_path": "worker.json", "worker_sha256": sha256_file(worker),
                "capture": {"start": -2, "end": -2, "count": 1, "view": "main", "fps": 24,
                            "width": 32, "height": 16, "pixel_aspect": 1.0,
                            "files": [file_record(capture, root, geometry=geometry)]},
                "frame_map": FrameMap(-2, -2).as_dict(), "fingerprint": {"digest": "graph", "unresolved": []},
                "settings": {"source_color": "linear-srgb"}, "host": {}, "worker_exit": 0,
                "outputs": [file_record(first, root, geometry=geometry)], "validation": {"valid": True},
                "backend_manifest": backend.relative_to(root).as_posix(),
                "result": {"status": "complete", "frame_count": 1, "output_dir": str(output),
                           "first_source": str(capture), "first_output": str(first), "manifest": str(backend),
                           "job_dir": str(backend.parent), "source_preview": str(backend.parent / "source.png"),
                           "matte_preview": str(backend.parent / "matte.png")}}
    atomic_write_json(root / "integration.json", manifest)
    return root / "integration.json"


def update_manifest(path, mutate):
    record = json.loads(path.read_text())
    mutate(record)
    atomic_write_json(path, record)


def test_complete_and_moved_cache_use_relative_owned_paths(completed, tmp_path):
    original_bytes = (completed.parent / "worker.json").read_bytes()
    assert validate_cache(completed)["valid"]
    moved = tmp_path / "moved complete"
    shutil.move(str(completed.parent), moved)
    result = relink_cache(moved / "integration.json")
    assert result["valid"]
    assert result["output_dir"] == str(moved / "capture_matte_v002")
    assert (moved / "worker.json").read_bytes() == original_bytes
    assert not completed.parent.exists()


@pytest.mark.parametrize("kind", ["missing", "truncated", "same_size_corrupt", "extra_frame", "capture_gap"])
def test_bad_frames_rejected(completed, kind):
    root = completed.parent
    output = root / "capture_matte_v002/capture_matte_v002.0001.exr"
    if kind == "missing":
        output.unlink()
    elif kind == "truncated":
        output.write_bytes(b"short")
    elif kind == "same_size_corrupt":
        output.write_bytes(b"x" * output.stat().st_size)
    elif kind == "extra_frame":
        output.with_name("capture_matte_v002.0002.exr").write_bytes(b"extra")
    else:
        (root / "capture_v001/capture.0001.exr").rename(root / "capture_v001/capture.0002.exr")
    with pytest.raises(ProtocolError):
        validate_cache(completed)


@pytest.mark.parametrize("mutation", [lambda m: m.update(state="running"),
                                      lambda m: m.update(worker_exit=1),
                                      lambda m: m["result"].update(status="committing"),
                                      lambda m: m["capture"].update(count=2),
                                      lambda m: m["frame_map"].update(start=1001),
                                      lambda m: m["outputs"][0].update(path="../outside.exr"),
                                      lambda m: m["outputs"][0]["geometry"].update(width=64),
                                      lambda m: m["result"].update(output_dir="/outside/attempt"),
                                      lambda m: m.update(schema_version=2)])
def test_wrong_contract_cannot_activate(completed, mutation):
    update_manifest(completed, mutation)
    with pytest.raises(ProtocolError):
        validate_cache(completed)


def test_backend_must_be_complete(completed):
    record = read_manifest(completed)
    backend = resolve_owned(completed.parent, record["backend_manifest"])
    update_manifest(backend, lambda m: m.update(status="committing"))
    with pytest.raises(ProtocolError, match="complete"):
        validate_cache(completed)


@pytest.mark.parametrize("relative", ["../outside", "/tmp/outside", "C:/outside", "C:outside", "//server/share", "a/../../b", "a\\b", "."])
def test_owned_paths_reject_platform_traversal(tmp_path, relative):
    with pytest.raises(ProtocolError):
        resolve_owned(tmp_path, relative)


def test_symlink_escape_rejected(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProtocolError):
        resolve_owned(root, "escape/frame.exr")


def test_interrupted_manifest_replace_retains_old_and_ignores_unowned_temp(completed):
    original = completed.read_bytes()
    interrupted = completed.with_name(".integration.json.interrupted.tmp")
    interrupted.write_text('{"state":')
    with patch("shotbox_nuke.cache.os.replace", side_effect=OSError("interrupted")):
        with pytest.raises(OSError):
            atomic_write_json(completed, {"broken": True})
    assert completed.read_bytes() == original
    assert validate_cache(completed)["valid"]
    assert interrupted.exists()


def test_attempt_allocation_is_exclusive(tmp_path):
    node, attempt = str(uuid.uuid4()), str(uuid.uuid4())
    allocate_attempt(tmp_path, node, attempt)
    with pytest.raises(FileExistsError):
        allocate_attempt(tmp_path, node, attempt)


def test_preferences_preserve_venv_symlink_and_explicit_override(tmp_path):
    root = tmp_path / "frontend" / "toolbox" / "shotbox_matte"
    executable = tmp_path / "frontend" / "venv/bin/python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    assert resolve_interpreter(root) == executable
    assert resolve_interpreter(root, override=executable) == executable
    preferences = tmp_path / "preferences.json"
    save({"application_root": str(root), "model_root": "模型"}, preferences)
    assert load(preferences)["model_root"] == "模型"
    assert suggested_cache_root(None) is None
    assert suggested_cache_root(tmp_path / "comp.nk") == tmp_path / ".shotbox-cache"


@pytest.mark.parametrize("windows,suffix", [(False, "bin/python"), (True, "Scripts/python.exe")])
def test_nuke_uses_main_environment_and_ignores_old_private_runtime(tmp_path, windows, suffix):
    root = tmp_path / "frontend" / "toolbox" / "shotbox_matte"
    main = tmp_path / "frontend" / "venv" / suffix
    main.parent.mkdir(parents=True)
    main.touch()
    private = root / ".venv" / suffix
    private.parent.mkdir(parents=True)
    private.write_text("old private runtime")
    assert resolve_interpreter(root, windows=windows) == main
    assert resolve_interpreter(root, override=private, windows=windows) == main
    assert private.read_text() == "old private runtime"
    external = tmp_path / "custom" / suffix
    external.parent.mkdir(parents=True)
    external.touch()
    assert resolve_interpreter(root, override=external, windows=windows) == external


def test_nuke_preferences_migrate_managed_runtime_and_preserve_other_fields(tmp_path):
    preferences = tmp_path / "preferences.json"
    preferences.write_text(json.dumps({
        "schema_version": 1, "application_root": "/old/shotbox/toolbox/shotbox_matte",
        "interpreter": "/old/shotbox/toolbox/shotbox_matte/.venv/bin/python",
        "model_root": "/external/models", "studio_note": "keep"}))
    assert load(preferences)["interpreter"] == ""
    save({"cache_root": "/cache"}, preferences)
    saved = json.loads(preferences.read_text())
    assert saved["interpreter"] == ""
    assert saved["model_root"] == "/external/models"
    assert saved["studio_note"] == "keep"


def test_nuke_missing_shared_runtime_does_not_fall_back_to_private_environment(tmp_path):
    root = tmp_path / "frontend" / "toolbox" / "shotbox_matte"
    private = root / ".venv/bin/python"
    private.parent.mkdir(parents=True)
    private.touch()
    with pytest.raises(ProtocolError, match="main Shotbox installer"):
        resolve_interpreter(root, windows=False)
