"""Real file/Qt checks for frame mapping, playback and nondestructive overlays."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

from shotbox_matte.qc_preview import (
    PreviewFrame, compose_preview, create_qc_preview, load_frame_pair, result_frames, source_frames,
)


def wait_until(app, condition, timeout=8):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("QC preview did not finish")
        time.sleep(0.005)
    app.processEvents()


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def clip(tmp_path):
    from PIL import Image
    from shotbox_matte.sequences import discover_sequences
    folder = tmp_path / "plate_v001"
    folder.mkdir()
    for offset, number in enumerate(range(1039, 1043)):
        Image.fromarray(np.full((8, 12, 3), 40 + offset * 40, np.uint8)).save(folder / f"plate.{number}.png")
    return discover_sequences(folder)[0]


def make_result(tmp_path, clip):
    from shotbox_matte.imaging import write_exr
    output = tmp_path / "plate_roto_v001"
    output.mkdir()
    names = [f"{output.name}.{number}.exr" for number in (1040, 1041)]
    for offset, name in enumerate(names):
        write_exr(output / name, np.full((8, 12, 3), offset, np.float32), "float")
    job = tmp_path / "logs/jobs/completed"
    job.mkdir(parents=True)
    manifest = job / "_shotbox_job.json"
    manifest.write_text(json.dumps({
        "sequence": clip.to_dict(), "selected_files": [str(clip.files[i]) for i in (1, 3)],
        "selected_indices": [1, 3], "source_numbers": [1040, 1042], "frames": names,
        "source_color": "srgb", "fps": 25,
    }), encoding="utf-8")
    return {"output_dir": str(output), "manifest": str(manifest), "frame_count": 2}


def test_import_is_headless_and_never_imports_qt_or_torch():
    result = subprocess.run([sys.executable, "-c",
                             "import sys; import shotbox_matte.qc_preview; "
                             "assert not any(n.startswith(('PyQt6','torch')) for n in sys.modules)"],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_source_mapping_preserves_selected_range_and_frame_numbers(clip):
    frames = source_frames(clip, (1, 3))
    assert [frame.source_number for frame in frames] == [1040, 1042]
    assert [frame.source_path for frame in frames] == [str(clip.files[1]), str(clip.files[3])]
    assert all(frame.source_index == 0 for frame in frames)
    with pytest.raises(ValueError, match="outside"):
        source_frames(clip, (4,))


def test_result_mapping_uses_job_selection_and_exact_output_names(tmp_path, clip):
    frames, color, fps = result_frames(make_result(tmp_path, clip))
    assert color == "srgb" and fps == 25
    assert [frame.source_number for frame in frames] == [1040, 1042]
    assert Path(frames[1].matte_path).name == "plate_roto_v001.1041.exr"
    pair = load_frame_pair(frames[1], color)
    np.testing.assert_array_equal(pair["source"], 160)
    np.testing.assert_array_equal(pair["matte"], 255)
    assert not pair["errors"]


def test_overlay_coverage_opacity_and_preserves_source_values():
    source = np.full((2, 3, 3), 100, np.uint8)
    matte = np.zeros_like(source)
    matte[:, 1:] = 255
    original = source.copy()
    pair = {"source": source, "matte": matte}
    overlay = compose_preview(pair, "overlay", .5)
    np.testing.assert_array_equal(overlay[:, 0], 100)
    np.testing.assert_array_equal(overlay[0, 1], [172, 98, 74])
    np.testing.assert_array_equal(compose_preview(pair, "overlay", 0), source)
    np.testing.assert_array_equal(source, original)


def test_movie_preview_uses_selected_absolute_ordinals(tmp_path):
    av = pytest.importorskip("av")
    from shotbox_matte.sequences import discover_sequences
    path = tmp_path / "plate_v001.mov"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("png", rate=25)
        stream.width = stream.height = 16
        stream.pix_fmt = "rgb24"
        for index in range(6):
            frame = av.VideoFrame.from_ndarray(np.full((16, 16, 3), index * 30, np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    frames = source_frames(discover_sequences(path)[0], (3, 5))
    assert [frame.source_index for frame in frames] == [3, 5]
    assert [frame.source_number for frame in frames] == [4, 6]
    np.testing.assert_allclose(load_frame_pair(frames[0], "srgb")["source"], 90, atol=1)


def test_qt_slider_steps_playback_modes_and_disposal(qt_app, tmp_path, clip):
    widget = create_qc_preview()
    try:
        widget.show()
        widget.set_source(clip, "srgb", (1, 3))
        wait_until(qt_app, lambda: widget.canvas.pixmap is not None and widget._active is None)
        assert "1040" in widget.frame_label.text()
        assert not widget.mode.isEnabled()
        widget.next_button.click()
        wait_until(qt_app, lambda: widget.slider.value() == 1 and widget._active is None and not widget._debounce.isActive())
        assert "1042" in widget.frame_label.text()
        assert widget.canvas.pixmap.toImage().pixelColor(0, 0).red() == 160
        widget.previous_button.click()
        wait_until(qt_app, lambda: widget.slider.value() == 0 and not widget._debounce.isActive())
        widget.set_result(make_result(tmp_path, clip))
        wait_until(qt_app, lambda: widget.canvas.pixmap is not None and widget._active is None)
        assert widget.mode.currentData() == "matte" and widget._fps == 25
        widget.mode.setCurrentIndex(2)
        assert widget.opacity.isEnabled()
        widget.slider.setValue(1)
        wait_until(qt_app, lambda: not widget._debounce.isActive() and widget._active is None)
        assert widget.canvas.pixmap.toImage().pixelColor(0, 0).red() > 160
        widget.mode.setCurrentIndex(0)
        widget.slider.setValue(0)
        widget.loop.setChecked(False)
        widget.set_fps(60)
        widget.play_button.click()
        wait_until(qt_app, lambda: widget.slider.value() == 1 and not widget._timer.isActive())
        widget.dispose()
        assert not widget._timer.isActive() and widget._closed
    finally:
        widget.close()


def test_stale_frame_load_cannot_replace_new_source(qt_app, clip, monkeypatch):
    import shotbox_matte.qc_preview as qc
    entered, release = threading.Event(), threading.Event()
    original = qc.load_frame_pair

    def delayed(frame, color):
        if frame.source_number == 1039:
            entered.set()
            release.wait(5)
        return original(frame, color)

    monkeypatch.setattr(qc, "load_frame_pair", delayed)
    widget = create_qc_preview()
    try:
        widget.set_source(clip, "srgb", (0,))
        wait_until(qt_app, entered.is_set)
        widget.set_source(clip, "srgb", (3,))
        release.set()
        wait_until(qt_app, lambda: widget._active is None and widget.canvas.pixmap is not None)
        assert widget.canvas.pixmap.toImage().pixelColor(0, 0).red() == 160
        assert "1042" in widget.frame_label.text()
    finally:
        release.set()
        widget.close()


def test_missing_source_still_allows_matte_inspection(tmp_path):
    from shotbox_matte.imaging import write_exr
    matte = tmp_path / "matte.exr"
    write_exr(matte, np.ones((4, 6, 3), np.float32))
    pair = load_frame_pair(PreviewFrame(str(tmp_path / "missing.png"), matte_path=str(matte)), "srgb")
    assert pair["source"] is None and pair["errors"]
    np.testing.assert_array_equal(compose_preview(pair, "matte"), 255)
