"""Long-shot limits, segment alignment, overlap blending and transactional export."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from shotbox_matte.core import CancelledError, JobOptions, run_job
from shotbox_matte.native import _validate_frames
from shotbox_matte.temporal import temporal_chunks


@pytest.mark.parametrize("count", [1, 20, 121, 129, 137, 145])
def test_supported_short_shots_keep_single_pass(count):
    chunks = temporal_chunks(count, 24)
    assert len(chunks) == 1
    assert (chunks[0].start, chunks[0].stop) == (0, count)


def test_250_frames_use_three_overlapping_supported_segments():
    chunks = temporal_chunks(250, 24)
    assert [(chunk.start, chunk.stop, chunk.padded_count) for chunk in chunks] == [
        (0, 121, 121), (104, 225, 121), (208, 250, 49),
    ]
    assert sum(chunk.padded_count for chunk in chunks) == 291


def test_full_range_keeps_all_250_frames_in_one_padded_pass():
    chunks = temporal_chunks(250, 24, "full-range")
    assert [(chunk.start, chunk.stop, chunk.padded_count) for chunk in chunks] == [(0, 250, 257)]
    assert len(temporal_chunks(25, 2, "full-range")) == 1
    assert temporal_chunks(278, 24, "full-range")[0].padded_count == 281
    with pytest.raises(ValueError, match="processing mode"):
        temporal_chunks(250, 24, "unknown")


@pytest.mark.parametrize("count", [146, 147, 248, 249, 250, 278, 1000])
@pytest.mark.parametrize("fps", [1, 2, 12, 23.976, 24, 60])
def test_segments_cover_every_source_frame_and_pass_native_validation(count, fps):
    chunks = temporal_chunks(count, fps)
    covered = set()
    for chunk in chunks:
        covered.update(range(chunk.start, chunk.stop))
        frames = np.zeros((chunk.padded_count, 32, 32, 3), np.float32)
        assert _validate_frames(frames, fps)[0] <= 121
        assert (chunk.padded_count - 1) / fps <= 6 + 1e-9
    assert covered == set(range(count))


@pytest.mark.parametrize("count", [251, 278, 10000])
def test_shot_length_has_no_application_ceiling(count):
    chunks = temporal_chunks(count, 24)
    assert chunks[0].start == 0 and chunks[-1].stop == count
    assert all(chunk.padded_count <= 121 for chunk in chunks)
    assert all(left.stop - right.start == 17 for left, right in zip(chunks, chunks[1:]))


@pytest.fixture
def long_sequence(tmp_path):
    pytest.importorskip("OpenImageIO")
    from shotbox_matte.imaging import ImageWindow, write_exr

    folder = tmp_path / "long_v001"
    folder.mkdir()
    for index in range(250):
        # Distinct values and animated windows expose frame/order metadata errors.
        window = ImageWindow(index, -index, 8, 5, 0, 0, 512, 256)
        write_exr(folder / f"source.{1001 + index}.exr",
                  np.full((5, 8, 3), index / 1000, np.float32), "float", window=window)
    return folder


@pytest.mark.parametrize("mode", ["segmented", "full-range"])
def test_250_frame_export_preserves_alignment_and_applies_selected_mode(long_sequence, monkeypatch, mode):
    from shotbox_matte.imaging import read_exr, read_image_window

    calls = []
    forced = []
    progress_events = []

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            bias = len(calls)
            calls.append(frames.copy())
            forced.append(kwargs.get("force_full_range", False))
            kwargs["progress"]("inference", 8, 8, "Denoised")
            for index, frame in enumerate(frames):
                kwargs["progress"]("decode", index + 1, len(frames), "Decoded")
                yield np.full(frame.shape, frame[0, 0, 0] + bias, np.float32)

    monkeypatch.setattr("shotbox_matte.native.NativeAlphaEngine", Engine)
    options = JobOptions(str(long_sequence), source_color="srgb", generation_width=32,
                         generation_height=32, precision="float", temporal_mode=mode)
    result = run_job(options, progress=lambda *event: progress_events.append(event))
    segmented = mode == "segmented"
    assert [len(frames) for frames in calls] == ([121, 121, 49] if segmented else [257])
    assert forced == ([False, False, False] if segmented else [True])
    assert [float(frames[0, 0, 0, 0]) for frames in calls] == pytest.approx([0, .104, .208] if segmented else [0])
    np.testing.assert_array_equal(calls[-1][-8:], np.full(calls[-1][-8:].shape, .249, np.float32))
    assert result["frame_count"] == 250
    outputs = sorted(Path(result["output_dir"]).glob("*.exr"))
    assert len(outputs) == 250
    assert outputs[0].name.endswith(".1001.exr") and outputs[-1].name.endswith(".1250.exr")
    for index, path in enumerate(outputs):
        bias = (0 if index < 104 else 1 if index < 208 else 2) if segmented else 0
        if segmented and 104 <= index < 121:
            bias = (index - 104 + 1) / 18
        elif segmented and 208 <= index < 225:
            bias = 1 + (index - 208 + 1) / 18
        matte = read_exr(path)
        assert matte.shape == (5, 8, 3)
        np.testing.assert_allclose(matte, index / 1000 + bias, atol=1e-6)
        window = read_image_window(path)
        assert (window.x, window.y) == (index, -index)
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["status"] == "complete"
    padded = 291 if segmented else 257
    assert (manifest["selected_count"], manifest["padded_count"], manifest["chunk_count"]) == (250, padded, 3 if segmented else 1)
    assert manifest["temporal_mode"] == mode
    assert manifest["experimental_full_range"] is (mode == "full-range")
    assert manifest["temporal_blend"] == ("linear" if segmented else "none")
    assert (manifest["overlap_count"], manifest["padding_count"]) == (34 if segmented else 0, 7)
    steps = 24 if segmented else 8
    assert [event[1:3] for event in progress_events if event[0] == "inference" and event[2] == steps] == (
        [(8, 24), (16, 24), (24, 24)] if segmented else [(8, 8)]
    )
    assert [event for event in progress_events if event[0] == "decode"][-1][1:3] == (padded, padded)
    if not segmented:
        assert any("Experimental full range" in message for message in manifest["warnings"])


@pytest.mark.parametrize("failure", ["error", "cancelled", "short", "extra"])
@pytest.mark.parametrize("mode", ["segmented", "full-range"])
def test_failure_closes_inference_and_preserves_output_without_mode_fallback(long_sequence, monkeypatch, failure, mode):
    state = {"calls": 0, "closed": 0, "cancel": False}
    failure_call = 2 if mode == "segmented" else 1

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            state["calls"] += 1
            try:
                if state["calls"] == failure_call:
                    if failure == "error":
                        raise RuntimeError("Inference failed")
                    if failure == "cancelled":
                        state["cancel"] = True
                    elif failure == "short":
                        frames = frames[:-1]
                    elif failure == "extra":
                        frames = np.concatenate((frames, frames[-1:]))
                yield from frames
            finally:
                state["closed"] += 1

    monkeypatch.setattr("shotbox_matte.native.NativeAlphaEngine", Engine)
    output = long_sequence.with_name("long_roto_v001")
    output.mkdir()
    original = output / "existing.exr"
    original.write_bytes(b"existing completed sequence")
    options = JobOptions(str(long_sequence), generation_width=32, generation_height=32,
                         existing="overwrite", temporal_mode=mode)
    error = CancelledError if failure == "cancelled" else RuntimeError if failure == "error" else ValueError
    with pytest.raises(error):
        run_job(options, cancelled=lambda: state["cancel"])
    assert state["calls"] == state["closed"] == failure_call
    assert list(output.iterdir()) == [original]
    assert original.read_bytes() == b"existing completed sequence"
    manifests = list((long_sequence.parent / "app-job-data").glob("*/_shotbox_job.json"))
    manifest = json.loads(manifests[0].read_text())
    assert manifest["status"] == ("cancelled" if failure == "cancelled" else "failed")


def test_278_frame_export_keeps_every_frame_across_segment_joins(tmp_path, monkeypatch):
    from shotbox_matte.imaging import read_exr, write_exr

    source = tmp_path / "long_v001"
    source.mkdir()
    for index in range(278):
        write_exr(source / f"source.{1001 + index}.exr", np.full((2, 3, 3), index / 1000, np.float32), "float")
    calls = []

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            calls.append(len(frames))
            yield from frames

    monkeypatch.setattr("shotbox_matte.native.NativeAlphaEngine", Engine)
    result = run_job(JobOptions(str(source), source_color="srgb", generation_width=32,
                               generation_height=32, precision="float"))
    outputs = sorted(Path(result["output_dir"]).glob("*.exr"))
    assert calls == [121, 121, 73]
    assert result["frame_count"] == len(outputs) == 278
    assert outputs[0].name.endswith(".1001.exr") and outputs[-1].name.endswith(".1278.exr")
    for index, path in enumerate(outputs):
        np.testing.assert_allclose(read_exr(path), index / 1000, atol=1e-6)


def test_long_movie_range_and_skip_export_exactly_250_selected_frames(tmp_path, monkeypatch):
    av = pytest.importorskip("av")
    pytest.importorskip("OpenImageIO")
    from shotbox_matte.imaging import read_exr
    from shotbox_matte.media import read_frame

    source = tmp_path / "long_v001.mov"
    with av.open(str(source), "w") as container:
        stream = container.add_stream("png", rate=24)
        stream.width, stream.height, stream.pix_fmt = 16, 16, "rgb24"
        for index in range(501):
            frame = av.VideoFrame.from_ndarray(np.full((16, 16, 3), index % 256, np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    calls = []

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            calls.append((len(frames), float(frames[0, 0, 0, 0])))
            yield from frames

    monkeypatch.setattr("shotbox_matte.native.NativeAlphaEngine", Engine)
    result = run_job(JobOptions(str(source), source_start=2, source_end=501, frame_skip=1,
                               generation_width=32, generation_height=32, precision="float"))
    assert result["frame_count"] == 250
    assert [count for count, _value in calls] == [121, 121, 49]
    assert [value for _count, value in calls] == pytest.approx([
        float(read_frame(source, index)[0, 0, 0]) for index in [1, 209, 417]
    ])
    outputs = sorted(Path(result["output_dir"]).glob("*.exr"))
    assert len(outputs) == 250
    for index in [0, 103, 104, 120, 121, 207, 208, 224, 225, 249]:
        np.testing.assert_allclose(read_exr(outputs[index]), read_frame(source, 1 + index * 2), atol=1e-6)
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["source_count"] == 501
    assert manifest["selected_indices"] == list(range(1, 501, 2))
