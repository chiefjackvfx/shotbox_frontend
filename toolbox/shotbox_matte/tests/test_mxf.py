from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from shotbox_matte.batch import discover_batch_sources
from shotbox_matte.core import CancelledError, JobOptions, plan_job, run_job
from shotbox_matte.imaging import read_exr, write_exr
from shotbox_matte.media import default_source_color, probe_video, read_frame, read_source_frames
from shotbox_matte.sequences import discover_sequences


@pytest.mark.parametrize("suffix", [".mxf", ".MXF"])
def test_genuine_mxf_metadata_count_fps_and_nonzero_frame_decode(tmp_path, make_mxf, suffix):
    av = pytest.importorskip("av")
    path = make_mxf(tmp_path / ("camera_v002" + suffix))
    original = hashlib.sha256(path.read_bytes()).digest()
    # MXF uses SMPTE universal-label KLV keys. Confirm the muxed container and
    # codec as well, so a renamed MOV cannot accidentally satisfy this test.
    assert path.read_bytes().startswith(bytes.fromhex("060e2b34"))
    with av.open(str(path)) as container:
        assert container.format.name == "mxf"
        stream = container.streams.video[0]
        assert stream.codec_context.name == "mpeg2video"
        assert stream.codec_context.format.name == "yuv422p"
    info = probe_video(path)
    assert (info.width, info.height, info.count, info.fps) == (720, 576, 7, 25.0)
    first, later = read_frame(path), read_frame(path, 4)
    assert first.shape == later.shape == (576, 720, 3)
    assert first.dtype == later.dtype == np.float32
    assert np.isfinite(first).all() and 0 <= first.min() <= first.max() <= 1
    assert float(later.mean()) > float(first.mean()) + .35
    sequence = discover_sequences(path)[0]
    assert sequence.media_type == "video" and sequence.video_path == path
    assert sequence.frame_indices == tuple(range(7)) and sequence.fps == 25
    assert (sequence.source_start, sequence.source_end, sequence.source_range_mode) == (1, 7, "ordinal")
    assert sequence.output_source == path.with_suffix("")
    assert default_source_color(path) == "srgb"
    assert hashlib.sha256(path.read_bytes()).digest() == original


def test_mxf_inclusive_range_skip_cap_and_real_exr_export(tmp_path, make_mxf, monkeypatch):
    pytest.importorskip("OpenImageIO")
    path = make_mxf(tmp_path / "plate_v003.mxf")
    original = hashlib.sha256(path.read_bytes()).digest()
    captured = {}

    class Engine:
        def __init__(self, _config, **_kwargs):
            pass

        def infer(self, frames, **kwargs):
            captured["frames"] = frames.copy()
            captured["fps"] = kwargs["fps"]
            yield from frames

    monkeypatch.setitem(sys.modules, "shotbox_matte.native", SimpleNamespace(
        ModelConfig=SimpleNamespace(from_dict=lambda models: models), NativeAlphaEngine=Engine,
    ))
    options = JobOptions(str(path), source_start=2, source_end=7, frame_skip=1, frame_cap=3,
                         generation_width=64, generation_height=64, fps=25, precision="float")
    plan = plan_job(options)
    assert plan.selected_indices == (1, 3, 5)
    assert (plan.selected_count, plan.padded_count, plan.first_frame) == (3, 9, 1001)
    assert plan.source_color == "srgb"
    result = run_job(options)
    assert result["frame_count"] == 3 and captured["fps"] == 25
    outputs = sorted(Path(result["output_dir"]).iterdir())
    assert [path.name for path in outputs] == [f"plate_roto_v003.{number}.exr" for number in (1001, 1002, 1003)]
    for selected, ordinal in enumerate(plan.selected_indices):
        source = read_frame(path, ordinal)
        np.testing.assert_allclose(captured["frames"][selected], source[0, 0, 0], atol=1e-6)
        matte = read_exr(outputs[selected])
        assert matte.shape == source.shape
        np.testing.assert_allclose(matte, source[0, 0, 0], atol=1e-6)
    np.testing.assert_array_equal(captured["frames"][3:], np.repeat(captured["frames"][2:3], 6, axis=0))
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["status"] == "complete" and manifest["selected_indices"] == [1, 3, 5]
    assert manifest["sequence"]["video_path"] == str(path)
    assert (manifest["source_start"], manifest["source_end"], manifest["source_range_mode"]) == (2, 7, "ordinal")
    assert Path(result["manifest"]).parent != Path(result["output_dir"])
    assert {item.suffix for item in outputs} == {".exr"}
    assert hashlib.sha256(path.read_bytes()).digest() == original


def test_mixed_mxf_and_exr_batch_discovery_isolated_and_deduplicated(tmp_path, make_mxf):
    pytest.importorskip("OpenImageIO")
    first = make_mxf(tmp_path / "shot2.mxf")
    second = make_mxf(tmp_path / "shot10.mxf")
    folder = tmp_path / "images"
    folder.mkdir()
    for number in (1001, 1002):
        write_exr(folder / f"plate.{number}.exr", np.full((8, 12, 3), .18, np.float32))
    bad = tmp_path / "broken.mxf"
    bad.write_bytes(b"damaged MXF")
    result = discover_batch_sources([second, tmp_path, first])
    assert [source.video_path or source.files[0] for source in result.sequences] == [second, folder / "plate.1001.exr", first]
    assert [source.count for source in result.sequences] == [7, 2, 7]
    assert result.duplicate_count == 2
    assert len(result.errors) == 1 and "broken.mxf" in result.errors[0]


@pytest.mark.parametrize("operation,label", [(probe_video, "inspect"), (read_frame, "decode")])
def test_corrupt_mxf_has_readable_context(tmp_path, operation, label):
    pytest.importorskip("av")
    path = tmp_path / "corrupt.mxf"
    path.write_bytes(b"not an MXF container")
    with pytest.raises(ValueError, match=rf"Cannot {label} MOV/MP4/MXF.*corrupt.mxf"):
        operation(path)


@pytest.mark.parametrize("operation,label", [(probe_video, "inspect"), (read_frame, "decode")])
def test_missing_mxf_decoder_is_reported_instead_of_escaping_as_lookup_error(monkeypatch, operation, label):
    av = pytest.importorskip("av")
    from shotbox_matte import media

    def unavailable(*_args, **_kwargs):
        raise av.error.DecoderNotFoundError(1, "MXF video decoder is unavailable in this FFmpeg build")

    monkeypatch.setattr(media, "_pyav", lambda: SimpleNamespace(open=unavailable, error=av.error))
    with pytest.raises(ValueError, match=rf"Cannot {label} MOV/MP4/MXF.*decoder is unavailable") as caught:
        operation("missing-codec.mxf")
    assert isinstance(caught.value.__cause__, av.error.DecoderNotFoundError)


def test_audio_only_opatom_mxf_is_rejected_as_no_video(tmp_path):
    av = pytest.importorskip("av")
    path = tmp_path / "audio-only.mxf"
    with av.open(str(path), "w", format="mxf_opatom") as container:
        stream = container.add_stream("pcm_s16le", rate=48000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 9600), np.int16), format="s16", layout="mono")
        frame.sample_rate = 48000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    with av.open(str(path)) as container:
        assert container.format.name == "mxf" and not container.streams.video
    for operation in (probe_video, read_frame):
        with pytest.raises(ValueError, match="MOV/MP4/MXF contains no video stream"):
            operation(path)


def test_mxf_decode_preserves_cooperative_cancellation(tmp_path, make_mxf):
    path = make_mxf(tmp_path / "clip.mxf")
    sequence = discover_sequences(path)[0]
    cancelled = False
    frames = read_source_frames(sequence, (0, 2, 4), cancelled=lambda: cancelled)
    assert next(frames).shape == (576, 720, 3)
    cancelled = True
    with pytest.raises(CancelledError, match="Source loading cancelled"):
        next(frames)


def test_ten_bit_dnxhd_mxf_keeps_sub_rgb24_precision(tmp_path):
    av = pytest.importorskip("av")
    path = tmp_path / "ten-bit-dnxhd.mxf"
    with av.open(str(path), "w", format="mxf") as container:
        stream = container.add_stream("dnxhd", rate=25)
        stream.width, stream.height, stream.pix_fmt = 1920, 1080, "yuv422p10le"
        stream.bit_rate = 185_000_000
        stream.codec_context.thread_count = 1
        for index in range(2):
            frame = av.VideoFrame.from_ndarray(np.full((1080, 1920, 3), 12345 + index * 10000, np.uint16), format="rgb48le")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    with av.open(str(path)) as container:
        assert container.format.name == "mxf"
        assert container.streams.video[0].codec_context.name == "dnxhd"
        assert container.streams.video[0].codec_context.format.name == "yuv422p10le"
    info = probe_video(path)
    assert (info.width, info.height, info.count, info.fps) == (1920, 1080, 2, 25)
    first, second = read_frame(path), read_frame(path, 1)
    assert first.dtype == np.float32 and first.shape == (1080, 1920, 3)
    assert float(second[0, 0, 0] - first[0, 0, 0]) > .1
    # A hidden RGB24 conversion would constrain every value to exact /255 steps.
    value = float(first[0, 0, 0]) * 255
    assert abs(value - round(value)) > .05
