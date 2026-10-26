"""Real media decoding through shared preparation/export with controlled inference."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from shotbox_matte.core import JobOptions, plan_job, run_job
from shotbox_matte.imaging import ImageWindow, read_exr, read_image_window, write_exr
from shotbox_matte.media import read_frame


def fake_engine(monkeypatch, captured):
    class Engine:
        def __init__(self, config, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            captured["frames"] = frames.copy()
            captured["inference_options"] = kwargs
            yield from frames

    module = SimpleNamespace(
        ModelConfig=SimpleNamespace(from_dict=lambda values: values), NativeAlphaEngine=Engine,
    )
    monkeypatch.setitem(sys.modules, "shotbox_matte.native", module)


@pytest.mark.parametrize("suffix", [".mov", ".mp4"])
@pytest.mark.parametrize("source_range,indices", [(None, (0, 2, 4)), ((2, 7), (1, 3, 5))])
def test_movie_selection_auto_colour_naming_and_exr_export(tmp_path, monkeypatch, suffix, source_range, indices):
    av = pytest.importorskip("av")
    pytest.importorskip("OpenImageIO")
    path = tmp_path / ("plate_v003" + suffix)
    with av.open(str(path), "w") as container:
        stream = container.add_stream("png" if suffix == ".mov" else "libx264", rate=25)
        stream.width, stream.height = 16, 16
        stream.pix_fmt = "rgb24" if suffix == ".mov" else "yuv420p"
        for number in range(7):
            frame = av.VideoFrame.from_ndarray(np.full((16, 16, 3), 20 + number * 25, np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    original_size = path.stat().st_size
    options = JobOptions(str(path), frame_skip=1, frame_cap=3, fps=25,
                         generation_width=32, generation_height=32, precision="float",
                         source_start=source_range[0] if source_range else None,
                         source_end=source_range[1] if source_range else None)
    plan = plan_job(options)
    assert plan.selected_indices == indices
    assert plan.source_color == "srgb" and plan.padded_count == 9
    assert plan.output_preview == tmp_path / "plate_roto_v003"
    captured = {}
    fake_engine(monkeypatch, captured)
    result = run_job(options)
    assert "prompt" not in captured["inference_options"]
    for selected, ordinal in enumerate(indices):
        source = read_frame(path, ordinal)
        # Constant input makes resize neutral; an incorrect display transfer fails this check.
        expected = np.broadcast_to(source[0, 0], captured["frames"][selected].shape)
        np.testing.assert_allclose(captured["frames"][selected], expected, atol=1e-6)
    np.testing.assert_array_equal(captured["frames"][3:], np.repeat(captured["frames"][2:3], 6, axis=0))
    matte = read_exr(result["first_output"])
    assert result["frame_count"] == 3 and matte.shape == (16, 16, 3)
    np.testing.assert_allclose(matte[..., 0], read_frame(path, indices[0])[..., 0], atol=1e-6)
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["selected_indices"] == list(indices) and manifest["first_frame"] == 1001
    assert manifest["source_range_mode"] == "ordinal"
    assert (manifest["source_start"], manifest["source_end"]) == (source_range or (1, 7))
    assert manifest["fps"] == 25
    assert manifest["status"] == "complete" and path.stat().st_size == original_size
    assert manifest["prompt"] == ""
    assert {item.suffix for item in Path(result["output_dir"]).iterdir()} == {".exr"}
    assert Path(result["manifest"]).parent == Path(result["job_dir"])


def test_png_sequence_uses_display_values_and_source_numbering(tmp_path, monkeypatch):
    from PIL import Image
    pytest.importorskip("OpenImageIO")
    source = tmp_path / "plate_v002"
    source.mkdir()
    for number in range(1001, 1006):
        Image.fromarray(np.full((8, 12, 3), 64, np.uint8)).save(source / f"plate.{number}.png")
    captured = {}
    fake_engine(monkeypatch, captured)
    result = run_job(JobOptions(str(source), frame_skip=1, frame_cap=3,
                                generation_width=32, generation_height=32, precision="float"))
    np.testing.assert_allclose(captured["frames"], 64 / 255, atol=1e-6)
    assert result["frame_count"] == 3
    assert result["first_output"].endswith("plate_roto_v002.1001.exr")
    assert len(list(Path(result["output_dir"]).iterdir())) == result["frame_count"]


def test_selected_exrs_preserve_each_data_and_display_window(tmp_path, monkeypatch):
    pytest.importorskip("OpenImageIO")
    folder = tmp_path / "cropped_v001"
    folder.mkdir()
    windows = [
        ImageWindow(30, 40, 16, 12, 0, 0, 100, 80),
        ImageWindow(-3, -5, 16, 12, 0, 0, 8, 6, 1.25),
        ImageWindow(10, -5, 16, 12, -20, -30, 120, 90),
        ImageWindow(-8, 5, 16, 12, -30, -10, 100, 80),
    ]
    for index, window in enumerate(windows):
        write_exr(folder / f"plate.{1001 + index}.exr", np.full((12, 16, 3), .18, np.float32), "float", window=window)
    captured = {}
    fake_engine(monkeypatch, captured)
    options = JobOptions(str(folder), source_start=1002, source_end=1004, frame_skip=1,
                         generation_width=32, generation_height=32, precision="float")
    result = run_job(options)
    outputs = sorted(Path(result["output_dir"]).iterdir())
    assert len(outputs) == 2
    assert outputs[0].name.endswith(".1002.exr") and outputs[1].name.endswith(".1003.exr")
    assert [read_image_window(path) for path in outputs] == [windows[1], windows[3]]
    for path in outputs:
        assert read_exr(path).shape == (12, 16, 3)
        np.testing.assert_allclose(read_exr(path), .46135613, atol=1e-6)
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["source_windows"] == [windows[1].to_dict(), windows[3].to_dict()]
    assert manifest["selected_indices"] == [1, 3]
    assert {path.suffix for path in outputs} == {".exr"}


def test_changed_data_window_size_is_rejected_before_loading_model(tmp_path, monkeypatch):
    pytest.importorskip("OpenImageIO")
    folder = tmp_path / "changed_v001"
    folder.mkdir()
    write_exr(folder / "plate.1001.exr", np.full((12, 16, 3), .18, np.float32))
    write_exr(folder / "plate.1002.exr", np.full((10, 16, 3), .18, np.float32))
    monkeypatch.setitem(sys.modules, "shotbox_matte.native", SimpleNamespace())
    with pytest.raises(ValueError, match="frame size changed.*different data-window sizes"):
        run_job(JobOptions(str(folder)))
    assert not (tmp_path / "changed_roto_v001").exists()
