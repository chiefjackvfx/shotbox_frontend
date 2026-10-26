"""Real EXR fixtures exercise the external boundary without inference."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import numpy as np
import pytest

from shotbox_matte.core import JobOptions, run_job
from shotbox_matte.imaging import ImageWindow, write_exr
from shotbox_matte.models import MODEL_FILES
from shotbox_matte import nuke_bridge as bridge


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_job(root, count=3, **changes):
    source = root / "capture_v001"
    source.mkdir()
    for index in range(1, count + 1):
        write_exr(source / f"capture.{index:04d}.exr", np.full((3, 5, 3), index / 10, np.float32), "float")
    options = asdict(JobOptions(input_path=str(source), source_color="linear-srgb", source_start=1,
                               source_end=count, first_frame=1, suffix="_matte",
                               models={name: str(root / f"{name}.safetensors") for name in MODEL_FILES}))
    options.update(changes)
    return write_json(root / "worker.json", options)


def make_pack(root):
    for role, relative in MODEL_FILES.items():
        configurations = {"transformer": {"transformer": {}},
                          "video_vae": {"vae": {"_class_name": "CausalDiffusionVAE"}},
                          "audio_vae": {"audio_vae": {}}}
        metadata = {"model_version": "2.5.0", "config": json.dumps(configurations.get(role, {"component": True}))}
        header = {"weights": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 2]}}
        if role == "text_encoder":
            metadata["gemma_config"] = '{"gemma_version":"gemma4-12b-ltx-v1"}'
            for key in ("tokenizer_json", "hf_asset__tokenizer_config.json", "hf_asset__processor_config.json"):
                header[key] = {"dtype": "U8", "shape": [0], "data_offsets": [2, 2]}
        if role == "alpha_lora":
            header["block.lora_A.weight"] = header.pop("weights")
        header["__metadata__"] = metadata
        payload = json.dumps(header).encode()
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(struct.pack("<Q", len(payload)) + payload + b"\0\0")


def controlled_attempt(root, monkeypatch):
    job = make_job(root)
    scan = bridge.preflight(job)

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            for frame in frames:
                yield np.full_like(frame, -0.125)

    monkeypatch.setattr("shotbox_matte.native.NativeAlphaEngine", Engine)
    monkeypatch.setenv("SHOTBOX_JOB_DIR", str(root / "backend_jobs"))
    result = run_job(bridge.load_job(job))
    capture = scan["capture"]
    capture.update(start=-2, end=0, view="main", fps=24.0, pixel_aspect=1.0)
    data = dict(schema_version=1, integration_version="0.1.0", node_id="node", attempt_id="attempt",
                state="validating", created_at="2026-10-23T12:00:00Z", worker_path="worker.json",
                worker_sha256=hashlib.sha256(job.read_bytes()).hexdigest(), capture=capture,
                fingerprint={}, settings={"source_color": "linear-srgb"}, host={}, probe={}, result=result, worker_exit=0,
                outputs=[], validation={}, original_attempt_root=str(root))
    return write_json(root / "integration.json", data)


def test_preflight_real_exrs_reads_every_frame_and_never_imports_inference(tmp_path):
    job = make_job(tmp_path)
    code = """
import json, sys
from shotbox_matte.nuke_bridge import preflight
result = preflight(sys.argv[1])
assert 'shotbox_matte.native' not in sys.modules
assert 'torch' not in sys.modules
print(json.dumps(result))
"""
    result = subprocess.run([sys.executable, "-c", code, str(job)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    capture = json.loads(result.stdout)["capture"]
    assert len(capture["files"]) == 3
    assert all(record["precision"] == "float" and len(record["sha256"]) == 64 for record in capture["files"])
    assert sorted(path.name for path in tmp_path.iterdir()) == ["capture_v001", "worker.json"]


@pytest.mark.parametrize("change", [{"source_color": "auto"}, {"fps": True}, {"seed": True},
                                    {"source_end": 251}, {"offload": "bad"}, {"low_vram": 1},
                                    {"generation_width": True}, {"source_start": 0},
                                    {"frame_skip": 1}, {"first_frame": 1001}, {"models": {}},
                                    {"provenance": {}}, {"temporal_mode": "bad"}])
def test_strict_flat_options(tmp_path, change):
    job = make_job(tmp_path, **change)
    with pytest.raises(ValueError):
        bridge.preflight(job)


@pytest.mark.parametrize("defect", ["gap", "extra", "duplicate", "size", "window", "par", "truncated", "nonfinite"])
def test_bad_capture_rejected(tmp_path, defect):
    job = make_job(tmp_path)
    frame = tmp_path / "capture_v001/capture.0002.exr"
    if defect == "gap":
        frame.unlink()
    elif defect == "extra":
        (frame.parent / "notes.txt").touch()
    elif defect == "duplicate":
        shutil.copyfile(frame, frame.parent / "capture.2.exr")
    elif defect == "size":
        write_exr(frame, np.ones((3, 6, 3), np.float32), "float")
    elif defect in ("window", "par"):
        window = ImageWindow(1 if defect == "window" else 0, 0, 5, 3, 0, 0, 5, 3,
                             2.0 if defect == "par" else 1.0)
        write_exr(frame, np.ones((3, 5, 3), np.float32), "float", window=window)
    elif defect == "truncated":
        frame.write_bytes(frame.read_bytes()[:20])
    else:
        import OpenImageIO as oiio
        output = oiio.ImageOutput.create(str(frame))
        spec = oiio.ImageSpec(5, 3, 3, oiio.FLOAT)
        spec.channelnames = ["R", "G", "B"]
        output.open(str(frame), spec)
        output.write_image(np.full((3, 5, 3), np.nan, np.float32))
        output.close()
    with pytest.raises((ValueError, RuntimeError)):
        bridge.preflight(job)


def test_capture_symlink_escape_is_rejected_before_decode(tmp_path, monkeypatch):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    job = make_job(attempt)
    outside = tmp_path / "outside.exr"
    frame = attempt / "capture_v001/capture.0002.exr"
    frame.rename(outside)
    frame.symlink_to(outside)
    opened = []
    original = bridge.read_exr

    def read(path):
        opened.append(Path(path).resolve())
        return original(path)

    monkeypatch.setattr(bridge, "read_exr", read)
    with pytest.raises(ValueError, match="outside owned attempt"):
        bridge.preflight(job)
    assert outside not in opened


@pytest.mark.parametrize("mode,expected", [("segmented", [121, 121, 49]), ("full-range", [257])])
def test_250_frame_temporal_bounds(tmp_path, mode, expected):
    job = make_job(tmp_path, count=250, temporal_mode=mode)
    plan = bridge.preflight(job)["plan"]
    assert [chunk["padded_count"] for chunk in plan["temporal_chunks"]] == expected
    assert plan["selected_count"] == 250


@pytest.mark.parametrize("mode,expected", [("segmented", [121, 121, 73]), ("full-range", [281])])
def test_278_frame_preflight_has_no_shot_length_ceiling(tmp_path, mode, expected):
    job = make_job(tmp_path, count=278, temporal_mode=mode)
    result = bridge.preflight(job)
    assert result["plan"]["selected_count"] == result["capture"]["count"] == 278
    assert [chunk["padded_count"] for chunk in result["plan"]["temporal_chunks"]] == expected


def test_single_frame_preflight_and_cli(tmp_path):
    job = make_job(tmp_path, count=1)
    result = subprocess.run([sys.executable, "-m", "shotbox_matte.nuke_bridge", "preflight", "--job", str(job)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events[-1]["type"] == "plan" and events[-1]["protocol_version"] == 1
    assert events[-1]["capture"]["count"] == 1


def test_validate_real_worker_export_and_relocation(tmp_path, monkeypatch):
    root = tmp_path / "original with spaces α"
    root.mkdir()
    manifest = controlled_attempt(root, monkeypatch)
    report = bridge.validate(manifest)
    assert report["valid"] and len(report["outputs"]) == 3
    data = json.loads(manifest.read_text())
    data.update(outputs=report["outputs"], backend_manifest=report["backend_manifest"], state="complete", validation=report)
    write_json(manifest, data)
    moved = tmp_path / "moved cache"
    root.rename(moved)
    assert bridge.validate(moved / "integration.json")["valid"]
    assert not root.exists()


@pytest.mark.parametrize("defect", ["worker_digest", "range", "exit", "status", "backend_status", "outside",
                                    "missing", "non_gray", "precision", "capture_changed", "output_changed",
                                    "backend_map", "extra_output", "frame_map", "outside_preview"])
def test_validate_rejects_corruption(tmp_path, monkeypatch, defect):
    manifest = controlled_attempt(tmp_path, monkeypatch)
    data = json.loads(manifest.read_text())
    report = bridge.validate(manifest)
    data.update(outputs=report["outputs"], state="complete")
    output = Path(data["result"]["first_output"])
    backend_path = Path(data["result"]["manifest"])
    if defect == "worker_digest":
        data["worker_sha256"] = "0" * 64
    elif defect == "range":
        data["capture"]["end"] += 1
    elif defect == "exit":
        data["worker_exit"] = 1
    elif defect == "status":
        data["result"]["status"] = "running"
    elif defect == "outside":
        data["worker_path"] = "../worker.json"
    elif defect == "frame_map":
        data["frame_map"] = {"start": 1001, "end": 1003, "count": 3}
    elif defect == "outside_preview":
        data["result"]["source_preview"] = "/unowned/preview.png"
    elif defect in ("backend_status", "backend_map"):
        backend = json.loads(backend_path.read_text())
        backend["status" if defect == "backend_status" else "source_numbers"] = "ready" if defect == "backend_status" else [0, 1, 2]
        write_json(backend_path, backend)
    elif defect == "missing":
        output.unlink()
    elif defect == "capture_changed":
        write_exr(tmp_path / "capture_v001/capture.0002.exr", np.ones((3, 5, 3), np.float32), "float")
    elif defect == "extra_output":
        shutil.copyfile(output, output.parent / "extra.exr")
    else:
        pixels = np.ones((3, 5, 3), np.float32)
        if defect == "non_gray":
            pixels[..., 1] = 0.5
        write_exr(output, pixels, "float" if defect == "precision" else "half")
    write_json(manifest, data)
    with pytest.raises((ValueError, RuntimeError)):
        bridge.validate(manifest)


def test_probe_resolves_existing_precedence_headers_without_loading_weights(tmp_path, monkeypatch):
    make_pack(tmp_path / "models")
    request = write_json(tmp_path / "runtime.json", dict(schema_version=1, model_root=str(tmp_path / "models"),
                         cache_root=str(tmp_path), capture={"width": 5, "height": 3, "count": 3}))
    monkeypatch.setattr(bridge, "_dependencies", lambda: ({"torch": {"version": "controlled", "available": True}}, []))
    monkeypatch.setattr(bridge, "_cuda_report", lambda: {"available": True, "bf16": True})
    report = bridge.probe(request)
    assert report["ready"] and report["model_validation"]["valid"]
    assert set(report["models"]) == set(MODEL_FILES)
    assert report["disk"]["estimated_required_bytes"] > 0
    assert report["ltx_revision"] == "e98f6d927d83c92ef2be6aec76c8440b19306e39"
    assert not list(tmp_path.glob(".shotbox-probe-*"))
    config = write_json(tmp_path / "config.json", {"transformer": "models/" + MODEL_FILES["transformer"]})
    data = json.loads(request.read_text())
    data.update(model_root=str(tmp_path / "absent"), model_config=str(config), models={
        name: str(tmp_path / "models" / relative) for name, relative in MODEL_FILES.items() if name != "transformer"})
    write_json(request, data)
    assert bridge.probe(request)["ready"]


def test_probe_missing_models_dependencies_cuda_and_disk_are_actionable(tmp_path, monkeypatch):
    request = write_json(tmp_path / "runtime.json", {"schema_version": 1, "model_root": str(tmp_path),
                         "cache_root": str(tmp_path), "capture": {"width": 5, "height": 3, "count": 3}})
    monkeypatch.setattr(bridge, "_dependencies", lambda: ({}, ["Missing dependency: controlled"]))
    monkeypatch.setattr(bridge, "_cuda_report", lambda: {"available": False, "bf16": False})
    monkeypatch.setattr(bridge.shutil, "disk_usage", lambda path: shutil._ntuple_diskusage(100, 99, 1))
    report = bridge.probe(request)
    assert not report["ready"]
    assert any("dependency" in error for error in report["errors"])
    assert any("CUDA" in error for error in report["errors"])
    assert any("Missing transformer" in error for error in report["errors"])
    assert any("Insufficient" in error for error in report["errors"])


@pytest.mark.parametrize("version", [(3, 11), (3, 14)])
def test_probe_rejects_unsupported_external_python(tmp_path, monkeypatch, version):
    make_pack(tmp_path / "models")
    request = write_json(tmp_path / "runtime.json", {"schema_version": 1, "model_root": str(tmp_path / "models")})
    monkeypatch.setattr(bridge, "_dependencies", lambda: ({}, []))
    monkeypatch.setattr(bridge, "_cuda_report", lambda: {"available": True, "bf16": True})
    monkeypatch.setattr(bridge.sys, "version_info", version)
    report = bridge.probe(request)
    assert not report["ready"]
    assert any("Python" in error and "3.12" in error for error in report["errors"])


@pytest.mark.parametrize("version", [(3, 12), (3, 13)])
def test_probe_accepts_supported_external_python(tmp_path, monkeypatch, version):
    make_pack(tmp_path / "models")
    request = write_json(tmp_path / "runtime.json", {"schema_version": 1, "model_root": str(tmp_path / "models")})
    monkeypatch.setattr(bridge, "_dependencies", lambda: ({}, []))
    monkeypatch.setattr(bridge, "_cuda_report", lambda: {"available": True, "bf16": True})
    monkeypatch.setattr(bridge.sys, "version_info", version)
    report = bridge.probe(request)
    assert report["ready"], report["errors"]


def test_cli_usage_and_operation_failure(tmp_path):
    assert bridge.main(["preflight", "--job", str(tmp_path / "missing.json")]) == 1
    with pytest.raises(SystemExit) as error:
        bridge.main(["preflight"])
    assert error.value.code == 2
