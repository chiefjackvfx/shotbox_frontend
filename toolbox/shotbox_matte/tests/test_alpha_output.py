"""Real single-A EXRs and controlled worker export; no native GPU claims."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import pytest

from shotbox_matte import cli, nuke_bridge as bridge
from shotbox_matte.core import JobOptions, plan_job
from shotbox_matte.imaging import ImageWindow, read_alpha_exr, read_image_window, write_exr
from shotbox_matte.models import MODEL_FILES


@pytest.mark.parametrize("precision", ["half", "float"])
def test_single_alpha_encode_preserves_raw_matte_and_window(tmp_path, precision):
    oiio = pytest.importorskip("OpenImageIO")
    red = np.array([[-0.125, 1.25, 0.375], [0, 1, 0.5]], dtype=np.float32)
    matte = np.repeat(red[..., None], 3, axis=-1)
    window = ImageWindow(-4, 3, 3, 2, -8, -2, 20, 12, 1.25)
    path = tmp_path / "alpha.exr"
    write_exr(path, matte, precision, window=window, channels="alpha")
    reader = oiio.ImageInput.open(str(path))
    try:
        spec = reader.spec()
        assert list(spec.channelnames) == ["A"] and spec.nchannels == 1
        assert spec.alpha_channel == 0
        assert str(spec.format) == precision
        assert spec.get_string_attribute("compression") == "zip"
    finally:
        reader.close()
    assert read_image_window(path) == window
    np.testing.assert_array_equal(read_alpha_exr(path), red)


def test_alpha_export_refuses_coloured_matte_without_averaging_or_writing(tmp_path):
    path = tmp_path / "must-not-exist.exr"
    with pytest.raises(ValueError, match="equal RGB"):
        write_exr(path, np.array([[[0.1, 0.2, 0.3]]], np.float32), channels="alpha")
    assert not path.exists()


def test_alpha_full_float_never_silently_overflows_to_half(tmp_path):
    values = np.full((2, 3, 3), 70000, np.float32)
    with pytest.raises(ValueError, match="half-float range"):
        write_exr(tmp_path / "overflow.exr", values, "half", channels="alpha")
    assert not (tmp_path / "overflow.exr").exists()
    write_exr(tmp_path / "float.exr", values, "float", channels="alpha")
    np.testing.assert_array_equal(read_alpha_exr(tmp_path / "float.exr"), values[..., 0])


@pytest.mark.parametrize("channels", ["rgba", "A", "", None])
def test_unknown_channel_option_rejected_before_output(tmp_path, channels):
    with pytest.raises(ValueError, match="channels"):
        write_exr(tmp_path / "bad.exr", np.ones((2, 3, 3), np.float32), channels=channels)
    assert not (tmp_path / "bad.exr").exists()


def make_worker(root, *, channels="alpha", legacy=False):
    source = root / "capture_v001"
    source.mkdir(parents=True)
    for index in range(1, 4):
        write_exr(source / f"capture.{index:04d}.exr", np.full((3, 5, 3), index/10, np.float32), "float")
    options = JobOptions(str(source), source_color="acescg", source_start=1, source_end=3,
                         first_frame=1, suffix="_matte", generation_width=32, generation_height=32,
                         models={key: str(root / f"{key}.safetensors") for key in MODEL_FILES},
                         output_channels=channels)
    payload = asdict(options)
    if legacy:
        payload.pop("output_channels")
    path = root / "worker.json"
    path.write_text(json.dumps(payload))
    return path


def run_controlled_worker(root, monkeypatch, capsys, *, channels="alpha", legacy=False):
    pytest.importorskip("OpenImageIO")
    job = make_worker(root, channels=channels, legacy=legacy)
    scan = bridge.preflight(job)

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            for index, frame in enumerate(frames):
                yield np.full_like(frame, (-0.125, 1.25, 0.375)[index % 3])

    monkeypatch.setattr("shotbox_matte.native.NativeAlphaEngine", Engine)
    monkeypatch.setenv("SHOTBOX_JOB_DIR", str(root / "backend_jobs"))
    assert cli.main(["--worker", str(job)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result = events[-1]["result"]
    assert events[-1]["type"] == "result" and result["status"] == "complete"
    capture = scan["capture"]
    capture.update(start=-2, end=0, view="main", fps=24.0, pixel_aspect=1.0)
    data = {"schema_version": 1, "integration_version": "0.1.0", "node_id": "node", "attempt_id": "attempt",
            "state": "validating", "created_at": "2026-10-23T00:00:00Z", "original_attempt_root": str(root),
            "worker_path": "worker.json", "worker_sha256": hashlib.sha256(job.read_bytes()).hexdigest(),
            "capture": capture, "fingerprint": {}, "settings": {"source_color": "acescg"}, "host": {},
            "probe": {}, "result": result, "worker_exit": 0, "outputs": [], "validation": {}}
    integration = root / "integration.json"
    integration.write_text(json.dumps(data))
    return integration, data, scan, result


def test_actual_cli_worker_encodes_only_a_and_bridge_validates_moved_result(tmp_path, monkeypatch, capsys):
    oiio = pytest.importorskip("OpenImageIO")
    root = tmp_path / "attempt"
    integration, data, scan, result = run_controlled_worker(root, monkeypatch, capsys)
    assert scan["plan"]["output_channels"] == ["A"]
    backend = json.loads(Path(result["manifest"]).read_text())
    assert backend["output_channels"] == ["A"]
    for index, path in enumerate(sorted(Path(result["output_dir"]).glob("*.exr"))):
        reader = oiio.ImageInput.open(str(path))
        try:
            assert list(reader.spec().channelnames) == ["A"]
        finally:
            reader.close()
        np.testing.assert_array_equal(read_alpha_exr(path), (-0.125, 1.25, 0.375)[index])
    report = bridge.validate(integration)
    assert report["valid"] and report["output_channels"] == ["A"]
    assert len(report["outputs"]) == 3 and all(len(item["sha256"]) == 64 for item in report["outputs"])
    data.update(state="complete", outputs=report["outputs"], validation=report,
                backend_manifest=Path(result["manifest"]).relative_to(root).as_posix())
    integration.write_text(json.dumps(data))
    moved = tmp_path / "relocated"
    shutil.move(root, moved)
    assert bridge.validate(moved / "integration.json")["valid"]


def test_legacy_worker_and_backend_without_mode_remain_rgb(tmp_path, monkeypatch, capsys):
    oiio = pytest.importorskip("OpenImageIO")
    integration, _, scan, result = run_controlled_worker(tmp_path, monkeypatch, capsys, channels="rgb", legacy=True)
    assert bridge.load_job(tmp_path / "worker.json").output_channels == "rgb"
    assert scan["plan"]["output_channels"] == ["R", "G", "B"]
    reader = oiio.ImageInput.open(result["first_output"])
    try:
        assert list(reader.spec().channelnames) == ["R", "G", "B"]
    finally:
        reader.close()
    backend = Path(result["manifest"])
    record = json.loads(backend.read_text())
    record.pop("output_channels")
    backend.write_text(json.dumps(record))
    assert bridge.validate(integration)["valid"]


@pytest.mark.parametrize("backend_mode", [None, ["R", "G", "B"], "alpha", ["A", "R"]])
def test_alpha_worker_requires_matching_backend_mode(tmp_path, monkeypatch, capsys, backend_mode):
    integration, _, _, result = run_controlled_worker(tmp_path, monkeypatch, capsys)
    backend = Path(result["manifest"])
    record = json.loads(backend.read_text())
    if backend_mode is None:
        record.pop("output_channels")
    else:
        record["output_channels"] = backend_mode
    backend.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="channel mode"):
        bridge.validate(integration)


@pytest.mark.parametrize("bad_pixels", [np.nan, np.inf])
def test_bridge_fully_decodes_nonfinite_alpha(tmp_path, bad_pixels):
    oiio = pytest.importorskip("OpenImageIO")
    path = tmp_path / "bad.exr"
    spec = oiio.ImageSpec(3, 2, 1, oiio.FLOAT)
    spec.channelnames = ["A"]
    output = oiio.ImageOutput.create(str(path))
    output.open(str(path), spec)
    output.write_image(np.full((2, 3, 1), bad_pixels, np.float32))
    output.close()
    with pytest.raises(ValueError, match="NaN or infinite"):
        bridge._record(path, tmp_path, channels="alpha")


def test_alpha_files_are_never_accepted_as_rgb_captures(tmp_path):
    path = tmp_path / "alpha.exr"
    write_exr(path, np.ones((2, 3, 3), np.float32), channels="alpha")
    with pytest.raises(ValueError, match="R/G/B"):
        bridge._record(path, tmp_path, precision="half")


@pytest.mark.parametrize("bad", ["rgba", "", 1, None])
def test_worker_and_plan_reject_invalid_output_channel_modes(tmp_path, bad):
    job = make_worker(tmp_path)
    payload = json.loads(job.read_text())
    payload["output_channels"] = bad
    job.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="output_channels"):
        bridge.load_job(job)
    with pytest.raises(ValueError, match="output_channels"):
        plan_job(JobOptions(str(tmp_path / "capture_v001"), output_channels=bad))
