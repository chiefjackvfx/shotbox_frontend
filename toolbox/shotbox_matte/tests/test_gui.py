"""Desktop checks use real Qt/OIIO when those optional dependencies exist."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

from shotbox_matte.gui import MODEL_FIELDS, accepts_input_path, parse_worker_line, temporal_summary


def test_import_gui_does_not_import_qt_or_create_application():
    result = subprocess.run(
        [sys.executable, "-c", "import sys; import shotbox_matte.gui; assert 'PyQt6' not in sys.modules"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_worker_json_protocol_ignores_native_logs():
    assert parse_worker_line("Loading weights: 50%") is None
    assert parse_worker_line("[]") is None
    assert parse_worker_line('{"type":"unknown"}') is None
    event = {"type": "progress", "stage": "export", "current": 10, "total": 20}
    assert parse_worker_line(json.dumps(event)) == event
    assert temporal_summary(20, 25) == "20 selected → 25 model frames · 5 added copies"


def test_source_format_allowlist_and_directory_support(tmp_path):
    from shotbox_matte.media import SUPPORTED_EXTENSIONS

    assert SUPPORTED_EXTENSIONS == {
        ".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dpx", ".bmp", ".mov", ".mp4", ".mxf",
    }
    for suffix in SUPPORTED_EXTENSIONS:
        assert accepts_input_path(tmp_path / f"source{suffix}")
        assert accepts_input_path(tmp_path / f"SOURCE{suffix.upper()}")
    for suffix in (".gif", ".avi", ".mkv", ".webm", ".wav", ".txt", ".py", ".safetensors"):
        assert not accepts_input_path(tmp_path / f"source{suffix}")
    assert accepts_input_path(tmp_path)


def test_portable_preferences_relocate_windows_paths_and_preserve_custom_external_models(tmp_path):
    from shotbox_matte.gui import _portable_preferences
    from shotbox_matte.models import MODEL_FILES

    old_root = r"C:\Tools\ShotBox Matte"
    old_transformer = old_root + "\\models\\" + MODEL_FILES["transformer"].replace("/", "\\")
    custom_video = old_root + r"\models\vae\my-video-vae.safetensors"
    custom_external = r"D:\VFX Library\custom-audio.safetensors"
    values = {
        "portable_dir": old_root,
        "model_root": old_root + r"\models",
        "models": {
            "transformer": old_transformer,
            "video_vae": custom_video,
            "audio_vae": custom_external,
            "text_encoder": "E:\\Old Library\\" + Path(MODEL_FILES["text_encoder"]).name,
        },
    }
    restored = _portable_preferences(values, tmp_path)
    assert restored["model_root"] == str(tmp_path / "models")
    assert restored["models"]["transformer"] == str(tmp_path / "models" / MODEL_FILES["transformer"])
    assert restored["models"]["video_vae"] == str(tmp_path / "models" / "vae" / "my-video-vae.safetensors")
    assert restored["models"]["audio_vae"] == custom_external
    assert restored["models"]["text_encoder"] == values["models"]["text_encoder"]
    assert restored["portable_dir"] == str(tmp_path)
    assert values["models"]["transformer"] == old_transformer


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


def wait_until(app, condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Qt operation did not finish in time")
        time.sleep(0.005)
    app.processEvents()


def test_old_external_model_preferences_preserve_paths_and_other_settings(app, tmp_path):
    from PyQt6.QtCore import QSettings
    from shotbox_matte.gui import MODEL_PATHS_VERSION, create_window

    settings = QSettings(str(tmp_path / "old-preferences.ini"), QSettings.Format.IniFormat)
    old = tmp_path / "previous-model-library"
    settings.setValue("preferences", json.dumps({
        "model_root": str(old), "models": {"transformer": str(old / "old.safetensors")},
        "fps": 23.976, "suffix": "_alpha",
    }))
    instance = create_window(settings)
    try:
        assert instance.model_root.text() == str(old)
        assert instance.model_paths["transformer"].text() == str(old / "old.safetensors")
        assert instance.fps.value() == 23.976 and instance.suffix.text() == "_alpha"
        app.processEvents()
        wait_until(app, lambda: not instance._tasks)
        assert instance.model_paths["transformer"].text() == str(old / "old.safetensors")
        instance._save_settings()
        stored = json.loads(settings.value("preferences"))
        assert stored["model_paths_version"] == MODEL_PATHS_VERSION
        assert stored["model_root"] == str(old)
    finally:
        instance.close()
        app.processEvents()


@pytest.fixture
def window(app, tmp_path):
    from PyQt6.QtCore import QSettings
    from shotbox_matte.gui import create_window

    settings = QSettings(str(tmp_path / "preferences.ini"), QSettings.Format.IniFormat)
    instance = create_window(settings)
    instance.show()
    app.processEvents()
    yield instance
    if instance._is_running():
        instance.cancel()
        wait_until(app, lambda: not instance._is_running(), timeout=15)
    wait_until(app, lambda: not instance._tasks)
    instance.close()
    app.processEvents()


@pytest.fixture
def sequence(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("OpenImageIO")
    from shotbox_matte.imaging import write_exr

    folder = tmp_path / "scene_v001"
    folder.mkdir()
    for number in range(1001, 1021):
        write_exr(folder / f"scene.{number}.exr", np.full((8, 12, 3), 0.25, dtype=np.float32), "half")
    return folder


def test_real_sequence_plan_preview_and_frame_selection(app, window, sequence):
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None and window._source_preview is not None)
    assert window._plan["source_count"] == 20
    assert window._plan["padded_count"] == 25
    assert window._plan["first_frame"] == 1001
    assert "12 × 8 px" in window.source_info.text()
    assert "20 selected → 25 model frames" in window.frame_summary.text()
    assert window.output_preview.text().endswith("scene_roto_v001")
    assert window.generate_button.isEnabled()
    assert window._source_preview[:2] == (12, 8)

    window.frame_skip.setValue(1)
    window.frame_cap.setValue(3)
    wait_until(app, lambda: window._plan is not None and window._plan["selected_count"] == 3)
    assert window._plan["padded_count"] == 9
    assert window.options()["frame_skip"] == 1
    assert window.options()["first_frame"] is None


@pytest.mark.parametrize("source_size, requested_height, expected_size", [
    ((32, 18), 360, (640, 360)),
    ((18, 32), 640, (360, 640)),
    ((24, 24), 320, (320, 320)),
    ((32, 18), 1088, (1920, 1080)),
    ((64, 27), 1088, (1920, 810)),
])
def test_generation_height_updates_width_and_processing_plan(
    app, window, tmp_path, source_size, requested_height, expected_size,
):
    pytest.importorskip("OpenImageIO")
    Image = pytest.importorskip("PIL.Image")
    source = tmp_path / "source.png"
    Image.new("RGB", source_size, (128, 64, 32)).save(source)
    window.load_input(str(source))
    wait_until(app, lambda: window._plan is not None)

    window.generation_height.setValue(requested_height)
    assert (window.generation_width.value(), window.generation_height.value()) == expected_size
    wait_until(app, lambda: window._plan is not None)
    assert window._plan["source_width"] == source_size[0]
    assert window._plan["source_height"] == source_size[1]
    assert (window._plan["spatial"]["resized_width"], window._plan["spatial"]["resized_height"]) == expected_size
    assert window._plan["generation_width"] % 32 == 0
    assert window._plan["generation_height"] % 32 == 0


def test_source_selection_and_presets_recalculate_generation_width(app, window, tmp_path):
    pytest.importorskip("OpenImageIO")
    Image = pytest.importorskip("PIL.Image")
    folder = tmp_path / "sources"
    folder.mkdir()
    Image.new("RGB", (32, 18)).save(folder / "landscape.png")
    Image.new("RGB", (18, 32)).save(folder / "portrait.png")
    window.generation_height.setValue(360)
    window.load_input(str(folder))
    wait_until(app, lambda: window._plan is not None)
    assert window.generation_width.value() == 640

    window.sequence_combo.setCurrentIndex(window.sequence_combo.findData("portrait.png"))
    assert window.generation_height.value() == 360
    assert window.generation_width.value() == 202
    wait_until(app, lambda: window._plan is not None)

    # An old preset's landscape width must adapt even if height is unchanged.
    window._presets["Landscape"] = {"generation_width": 640, "generation_height": 360}
    window._refresh_preset_names("Landscape")
    window.preset_apply.click()
    assert window.generation_width.value() == 202
    wait_until(app, lambda: window._plan is not None)
    window.sequence_combo.setCurrentIndex(window.sequence_combo.findData("landscape.png"))
    assert window.generation_width.value() == 640
    wait_until(app, lambda: window._plan is not None)


def test_long_shot_is_enabled_without_cap_and_segmented_plan_is_visible(app, window, tmp_path):
    pytest.importorskip("OpenImageIO")
    Image = pytest.importorskip("PIL.Image")
    folder = tmp_path / "long_source"
    folder.mkdir()
    for number in range(1001, 1279):
        Image.new("RGB", (8, 5)).save(folder / f"source.{number}.png")
    window.load_input(str(folder))
    wait_until(app, lambda: window._plan is not None)
    assert window._plan["selected_count"] == 278
    assert window.generate_button.isEnabled()
    window.frame_cap.setValue(250)
    wait_until(app, lambda: window._plan is not None)
    assert window._plan["selected_count"] == 250
    assert window._plan["padded_count"] == 291
    assert window._plan["chunk_count"] == 3
    assert "3 segments" in window.frame_summary.text()
    assert "overlaps blended" in window.frame_summary.text()
    assert "review transitions" in window.validation_info.text()
    assert window.generate_button.isEnabled()

    assert window.temporal_mode.currentData() == "segmented"
    window.temporal_mode.setCurrentIndex(window.temporal_mode.findData("full-range"))
    wait_until(app, lambda: window._plan is not None)
    assert window._plan["chunk_count"] == 1 and window._plan["padded_count"] == 257
    assert window._plan["temporal_mode"] == "full-range"
    assert "one pass" in window.frame_summary.text()
    assert "experimental full range" in window.validation_info.text()
    assert "GPU memory" in window.temporal_mode_hint.text()
    assert any("RGB may leak" in message for message in window._plan["warnings"])
    window.preset_combo.setCurrentText("Full shot")
    window.preset_save.click()
    assert json.loads(window.settings.value("presets"))["Full shot"]["temporal_mode"] == "full-range"
    window.add_current_to_queue()
    assert window.queue.jobs[0].options["temporal_mode"] == "full-range"
    window.temporal_mode.setCurrentIndex(window.temporal_mode.findData("segmented"))
    wait_until(app, lambda: window._plan is not None)
    assert window._plan["chunk_count"] == 3
    assert window.queue.jobs[0].options["temporal_mode"] == "full-range"
    window.preset_apply.click()
    wait_until(app, lambda: window._plan is not None)
    assert window.temporal_mode.currentData() == "full-range"
    assert window._plan["chunk_count"] == 1


def test_drag_events_enforce_supported_source_formats(app, window, tmp_path):
    from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent

    def mime(path):
        data = QMimeData()
        data.setUrls([QUrl.fromLocalFile(str(path))])
        return data

    for suffix, accepted in ((".EXR", True), (".MP4", True), (".mov", True), (".png", True), (".avi", False), (".txt", False)):
        data = mime(tmp_path / f"source{suffix}")
        event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, data, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        window.drop_area.dragEnterEvent(event)
        assert event.isAccepted() is accepted
        window.drop_area._reset_drag()

    rejected_mime = mime(tmp_path / "source.py")
    rejected = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, rejected_mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.drop_area.dropEvent(rejected)
    assert not rejected.isAccepted()
    assert not window._input_path


def test_unsupported_file_and_empty_folder_clear_ready_state(app, window, sequence, tmp_path):
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    unsupported = tmp_path / "unsupported.avi"
    unsupported.write_text("not supported")
    window.load_input(str(unsupported))
    wait_until(app, lambda: not window._tasks)
    assert window._plan is None
    assert not window.generate_button.isEnabled()
    assert "unsupported" in window.source_info.text().lower()
    empty = tmp_path / "no_media"
    empty.mkdir()
    window.load_input(str(empty))
    wait_until(app, lambda: not window._tasks)
    assert window._plan is None
    assert not window.generate_button.isEnabled()
    assert "no " in window.source_info.text().lower()


def test_png_sequence_auto_colour_and_preview_override(app, window, tmp_path):
    pytest.importorskip("OpenImageIO")
    Image = pytest.importorskip("PIL.Image")
    from shotbox_matte.gui import _preview_data

    folder = tmp_path / "png_v001"
    folder.mkdir()
    for number in range(1001, 1004):
        Image.new("RGB", (32, 24), (128, 64, 32)).save(folder / f"source.{number}.png")
    window.load_input(str(folder))
    wait_until(app, lambda: window._plan is not None and window._source_preview is not None)
    assert window.options()["source_color"] == "auto"
    assert window._plan["source_color"] == "srgb"
    assert window._plan["selected_count"] == 3
    assert window._source_preview[:2] == (32, 24)
    assert abs(window._source_preview[2][0] - 128) <= 1
    path = str(folder / "source.1001.png")
    assert _preview_data(path, "linear-srgb")[2][0] > 180
    assert abs(_preview_data(path, "linear-srgb", prepared=True)[2][0] - 128) <= 1


@pytest.mark.parametrize("extension", [".mp4", ".mov"])
def test_real_video_loads_preview_and_detected_fps(app, window, tmp_path, extension):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    path = tmp_path / f"video{extension}"
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=30)
        stream.width = 32
        stream.height = 24
        stream.pix_fmt = "yuv420p"
        for _ in range(5):
            pixels = np.full((24, 32, 3), (128, 64, 32), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    window.load_input(str(path))
    wait_until(app, lambda: window._plan is not None and window._source_preview is not None)
    assert window._plan["source_count"] == 5
    assert window._plan["padded_count"] == 9
    assert window._plan["source_color"] == "srgb"
    assert window._source_preview[:2] == (32, 24)
    assert "30 FPS detected" in window.source_info.text()
    assert window.fps.value() == 30


def test_sequence_inspection_runs_outside_ui_thread(app, window, monkeypatch, sequence):
    from PyQt6.QtCore import QTimer
    from shotbox_matte import sequences

    original = sequences.discover_sequences
    entered = threading.Event()
    release = threading.Event()

    def blocked(path):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test did not release sequence inspection")
        return original(path)

    monkeypatch.setattr(sequences, "discover_sequences", blocked)
    timer = QTimer()
    ticks = []
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(5)
    try:
        window.load_input(str(sequence))
        wait_until(app, entered.is_set)
        wait_until(app, lambda: len(ticks) >= 4)
        assert window._plan is None
        assert not window.generate_button.isEnabled()
    finally:
        release.set()
        timer.stop()
    wait_until(app, lambda: window._plan is not None)


def test_model_validation_failure_in_real_worker_is_not_complete(app, window, sequence, tmp_path):
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    window.seed.setValue(17)
    wait_until(app, lambda: window._plan is not None)
    for key, _label in MODEL_FIELDS:
        invalid = tmp_path / f"{key}.safetensors"
        invalid.write_bytes(b"not valid weights")
        window.model_paths[key].setText(str(invalid))
    window.generate()
    job = json.loads(window._worker_path.read_text(encoding="utf-8"))
    assert job["seed"] == 17 and "prompt" not in job
    wait_until(app, lambda: window._process is None, timeout=15)
    assert window._result is None
    assert not window.open_output.isEnabled()
    assert "failed" in window.stage_label.text().lower()
    assert "safetensors" in window.log.toPlainText().lower()
    assert window.generate_button.isEnabled()
    assert not list(sequence.parent.glob("scene_roto_v*/_shotbox_complete.json"))


def test_completed_previews_live_outside_exr_only_output_and_folder_opens(app, window, sequence, tmp_path, monkeypatch):
    """Exercise real Qt process completion and raster I/O without GPU inference."""
    from shotbox_matte.worker import MatteWorker
    from PyQt6.QtGui import QDesktopServices
    from shotbox_matte.export import MANIFEST
    from shotbox_matte.imaging import write_exr, write_preview

    np = pytest.importorskip("numpy")
    pytest.importorskip("OpenImageIO")
    output = tmp_path / "scene_roto_v001"
    output.mkdir()
    first_output = output / "scene_roto_v001.1001.exr"
    write_exr(first_output, np.full((8, 12, 3), 0.6, dtype=np.float32), "half")
    job_dir = tmp_path / "app-storage" / "jobs" / "job-123"
    job_dir.mkdir(parents=True)
    source_preview = job_dir / "source.png"
    matte_preview = job_dir / "matte.png"
    write_preview(source_preview, np.full((8, 12, 3), 0.75, dtype=np.float32))
    write_preview(matte_preview, np.full((8, 12, 3), 0.2, dtype=np.float32))
    manifest = job_dir / MANIFEST
    manifest.write_text(json.dumps({"state": "complete"}), encoding="utf-8")
    result = {
        "output_dir": str(output), "frame_count": 1,
        "first_source": str(sequence / "scene.1001.exr"), "first_output": str(first_output),
        "source_preview": str(source_preview), "matte_preview": str(matte_preview),
        "manifest": str(manifest),
    }
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    window.seed.setValue(17)
    window._set_busy(True)
    # The controlled worker supplies the real result protocol. Native inference
    # and export correctness are covered independently by the processing tests.
    worker = MatteWorker(window, window.options())
    window._worker = worker
    window._process = worker.process
    worker.raw_output.connect(window._record_raw)
    worker.stdout_line.connect(window._handle_worker_line)
    worker.stderr_line.connect(lambda line: window._append_log(line, persist=False))
    worker.finished.connect(window._worker_finished)
    worker.process.setProgram(sys.executable)
    worker.process.setArguments([
        "-u", "-c", "import sys; print(sys.argv[1], flush=True)",
        json.dumps({"type": "result", "result": result}),
    ])
    worker.start()
    wait_until(app, lambda: window._process is None and window._matte_preview is not None)

    assert window._result == result
    assert window._source_preview[:2] == (12, 8)
    assert window._matte_preview[:2] == (12, 8)
    # Different PNG/EXR values prove the explicit external previews were used.
    assert abs(window._source_preview[2][0] - 191) <= 1
    assert abs(window._matte_preview[2][0] - 51) <= 1
    assert window.preview_mode.isEnabled()
    assert window.preview_mode.currentText() == "Matte"
    assert abs(window.preview_canvas.pixmap.toImage().pixelColor(0, 0).red() - 51) <= 1
    window.preview_mode.setCurrentIndex(0)
    assert abs(window.preview_canvas.pixmap.toImage().pixelColor(0, 0).red() - 191) <= 1
    assert window.output_preview.text() == str(output)
    assert "COMPLETED EXPORT" in window.output_title.text()
    assert window.open_output.isEnabled()
    window.open_output.click()
    assert opened == [str(output)]
    assert window.options()["seed"] == 17 and "prompt" not in window.options()
    assert sorted(output.iterdir()) == [first_output]
    assert source_preview.parent == matte_preview.parent == manifest.parent == job_dir


def test_useful_settings_persist_without_restoring_old_input(app, window, tmp_path):
    from shotbox_matte.gui import create_window

    window.fps.setValue(23.976)
    window.temporal_mode.setCurrentIndex(window.temporal_mode.findData("full-range"))
    window.seed.setValue(17)
    window.suffix.setText("_alpha")
    window.auto_first.setChecked(False)
    window.first_frame.setValue(2001)
    window.existing.setCurrentIndex(window.existing.findData("overwrite"))
    window.model_paths["transformer"].setText(str(tmp_path / "existing.safetensors"))
    window._save_settings()
    assert window.options()["existing"] == "overwrite"
    preferences = json.loads(window.settings.value("preferences"))
    assert "existing" not in preferences
    # Previously saved Overwrite preferences must also start with Version up.
    preferences["existing"] = "overwrite"
    window.settings.setValue("preferences", json.dumps(preferences))
    restored = create_window(window.settings)
    try:
        assert restored.fps.value() == 23.976
        assert restored.options()["temporal_mode"] == "full-range"
        assert "GPU memory" in restored.temporal_mode_hint.text()
        assert window.options()["seed"] == 17
        assert restored.options()["seed"] == 17
        assert json.loads(window.settings.value("preferences"))["seed"] == 17
        assert "prompt" not in restored.options()
        assert restored.suffix.text() == "_alpha"
        assert restored.options()["first_frame"] == 2001
        assert restored.options()["existing"] == "version-up"
        restored._apply_workflow_settings({"existing": "overwrite"})
        assert restored.options()["existing"] == "overwrite"
        assert restored.model_paths["transformer"].text().endswith("existing.safetensors")
        assert restored.options()["input_path"] == ""
        app.processEvents()
        wait_until(app, lambda: not restored._tasks)
    finally:
        restored.close()


def test_legacy_prompt_preferences_and_named_presets_are_discarded(app, window):
    from PyQt6.QtWidgets import QLabel, QWidget
    from shotbox_matte.gui import create_window

    assert not hasattr(window, "prompt") and "prompt" not in window.options()
    window.settings.setValue("preferences", json.dumps({"suffix": "_legacy", "seed": 17, "prompt": "old custom conditioning"}))
    window.settings.setValue("presets", json.dumps({"Legacy": {"suffix": "_saved", "seed": 7, "prompt": "old preset conditioning"}}))
    restored = create_window(window.settings)
    try:
        assert not hasattr(restored, "prompt") and "prompt" not in restored.options()
        assert restored.findChild(QWidget, "prompt") is None
        assert all(label.text() != "Prompt" for label in restored.findChildren(QLabel))
        assert restored.suffix.text() == "_legacy"
        assert restored.seed.value() == 17
        assert restored._presets == {"Legacy": {"suffix": "_saved", "seed": 7}}
        restored.preset_combo.setCurrentText("Legacy")
        restored.preset_apply.click()
        assert restored.seed.value() == 7 and restored.suffix.text() == "_saved"
        assert "prompt" not in restored.options()
        restored._save_settings()
        assert "prompt" not in json.loads(restored.settings.value("preferences"))
        assert "prompt" not in json.loads(restored.settings.value("presets"))["Legacy"]
        restored._set_busy(True)
        assert not restored.seed.isEnabled()
        restored._set_busy(False)
        assert restored.seed.isEnabled()
        app.processEvents()
        wait_until(app, lambda: not restored._tasks)
    finally:
        restored.close()


def test_portable_ini_settings_and_models_follow_moved_application(app, tmp_path, monkeypatch):
    from shotbox_matte.gui import create_window
    from shotbox_matte.models import MODEL_FILES

    original = tmp_path / "Original Portable"
    original.mkdir()
    transformer = original / "models" / MODEL_FILES["transformer"]
    transformer.parent.mkdir(parents=True)
    transformer.touch()
    custom_video = original / "models" / "vae" / "my-video.safetensors"
    custom_video.parent.mkdir(parents=True)
    custom_video.touch()
    custom_external = tmp_path / "outside-audio.safetensors"
    custom_external.touch()
    monkeypatch.setenv("SHOTBOX_PORTABLE_DIR", str(original))
    first = create_window()
    try:
        wait_until(app, lambda: not first._tasks)
        assert Path(first.settings.fileName()) == original / "settings.ini"
        assert first.model_root.text() == str(original / "models")
        first.fps.setValue(25)
        first.seed.setValue(17)
        first.suffix.setText("_portable")
        first.model_paths["transformer"].setText(str(transformer))
        first.model_paths["video_vae"].setText(str(custom_video))
        first.model_paths["audio_vae"].setText(str(custom_external))
        first._save_settings()
    finally:
        wait_until(app, lambda: not first._tasks)
        first.close()

    relocated = tmp_path / "Moved Portable"
    shutil.copytree(original, relocated)
    monkeypatch.setenv("SHOTBOX_PORTABLE_DIR", str(relocated))
    moved = create_window()
    try:
        wait_until(app, lambda: not moved._tasks)
        assert Path(moved.settings.fileName()) == relocated / "settings.ini"
        assert moved.model_root.text() == str(relocated / "models")
        assert moved.model_paths["transformer"].text() == str(relocated / "models" / MODEL_FILES["transformer"])
        assert moved.model_paths["video_vae"].text() == str(relocated / "models" / "vae" / "my-video.safetensors")
        assert moved.model_paths["audio_vae"].text() == str(custom_external)
        assert moved.fps.value() == 25
        assert moved.options()["seed"] == 17 and "prompt" not in moved.options()
        assert moved.suffix.text() == "_portable"
        assert moved.options()["input_path"] == ""
        moved._save_settings()
        assert json.loads(moved.settings.value("preferences"))["portable_dir"] == str(relocated)
    finally:
        wait_until(app, lambda: not moved._tasks)
        moved.close()


def test_gui_limits_and_source_range_plan_feed_scrubbable_review(app, window, sequence):
    assert (window.padding.minimum(), window.padding.maximum()) == (1, 8)
    assert window.first_frame.minimum() == 0
    assert window.generation_width.maximum() == 1920
    assert window.generation_height.maximum() == 1088
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    assert (window.source_start.value(), window.source_end.value()) == (1001, 1020)
    window.range_enabled.setChecked(True)
    window.source_start.setValue(1005)
    window.source_end.setValue(1010)
    window.frame_skip.setValue(1)
    wait_until(app, lambda: window._plan is not None and window._plan["selected_count"] == 3)
    assert window.options()["source_start"] == 1005
    assert window.options()["source_end"] == 1010
    assert window._plan["selected_indices"] == [4, 6, 8]
    assert window.qc_preview.slider.maximum() == 2
    assert "1005" in window.qc_preview.frame_label.text()
    window.qc_preview.next_button.click()
    assert "1007" in window.qc_preview.frame_label.text()


def test_named_presets_persist_apply_delete_and_preserve_selected_source_models(app, window, sequence, tmp_path):
    from shotbox_matte.gui import create_window

    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    original_models = {key: field.text() for key, field in window.model_paths.items()}
    window.seed.setValue(17)
    window.fps.setValue(25)
    window.range_enabled.setChecked(True)
    window.source_start.setValue(1003)
    window.source_end.setValue(1008)
    window.offload.setCurrentIndex(window.offload.findData("disk"))
    window.preset_combo.setCurrentText("Green screen")
    window.preset_save.click()
    saved = json.loads(window.settings.value("presets"))["Green screen"]
    assert "models" not in saved and "input_path" not in saved and "sequence" not in saved
    window.seed.setValue(99)
    window.fps.setValue(24)
    window.range_enabled.setChecked(False)
    window.preset_apply.click()
    assert window.seed.value() == 17 and "prompt" not in window.options()
    assert window.fps.value() == 25 and window.offload.currentData() == "disk"
    assert window.options()["source_start"] == 1003 and window.options()["source_end"] == 1008
    assert window.options()["input_path"] == str(sequence)
    assert {key: field.text() for key, field in window.model_paths.items()} == original_models
    restored = create_window(window.settings)
    try:
        assert "Green screen" in restored._presets
        restored.preset_combo.setCurrentText("Green screen")
        restored.preset_delete.click()
        assert json.loads(restored.settings.value("presets")) == {}
        wait_until(app, lambda: not restored._tasks)
    finally:
        restored.close()


def test_malformed_saved_preferences_and_presets_are_ignored(app, tmp_path):
    from PyQt6.QtCore import QSettings
    from shotbox_matte.gui import MODEL_PATHS_VERSION, create_window

    settings = QSettings(str(tmp_path / "bad.ini"), QSettings.Format.IniFormat)
    settings.setValue("preferences", json.dumps({"model_paths_version": MODEL_PATHS_VERSION, "models": [],
                                                "fps": "bad", "padding": 99, "prompt": [],
                                                "first_frame": -100, "source_color": [1]}))
    settings.setValue("presets", json.dumps({"Good": {"seed": 7, "prompt": "legacy", "models": {}}, "Bad": [], "": {}}))
    instance = create_window(settings)
    try:
        assert instance.fps.value() == 24 and instance.padding.value() == 4
        assert instance.options()["first_frame"] is None and "prompt" not in instance.options()
        assert instance._presets == {"Good": {"seed": 7}}
        wait_until(app, lambda: not instance._tasks)
    finally:
        instance.close()


@pytest.fixture
def controlled_queue_workers(window, monkeypatch, tmp_path):
    """Use real QProcesses and wire protocol, replacing GPU inference only."""
    from PyQt6.QtCore import QProcess

    calls = []
    original = QProcess.start
    for key, _label in MODEL_FIELDS:
        path = tmp_path / f"{key}.safetensors"
        path.touch()
        window.model_paths[key].setText(str(path))
    script = """
import json, pathlib, sys, time
path = pathlib.Path(sys.argv[1])
options = json.loads(path.read_text())
suffix = options.get('suffix', '')
print('optional library test: Matplotlib is not available', file=sys.stderr, flush=True)
print(json.dumps(dict(type='progress', stage='prepare', current=1, total=2, message='Preparing')), flush=True)
if suffix == '_cancel':
    for _ in range(200):
        if pathlib.Path(str(path) + '.cancel').exists():
            print(json.dumps(dict(type='cancelled', message='Cancelled by test')), flush=True)
            sys.exit(130)
        time.sleep(.01)
time.sleep(.12)
if suffix == '_fail':
    print(json.dumps(dict(type='error', message='Controlled worker failure')), flush=True)
    sys.exit(2)
print(json.dumps(dict(type='progress', stage='export', current=1, total=1, message='Exported')), flush=True)
print(json.dumps(dict(type='result', result=dict(output_dir=str(path.parent / 'result'), frame_count=1))), flush=True)
"""

    def start(process, *args):
        arguments = process.arguments()
        if "--worker" in arguments:
            path = arguments[-1]
            calls.append(json.loads(Path(path).read_text(encoding="utf-8")))
            return original(process, sys.executable, ["-u", "-c", script, path])
        return original(process, *args)

    monkeypatch.setattr(QProcess, "start", start)
    return calls


def test_batch_queue_keeps_per_job_settings_and_continues_after_failure(app, window, sequence, controlled_queue_workers):
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    for suffix in ("_fail", "_second"):
        window.suffix.setText(suffix)
        wait_until(app, lambda: window._plan is not None)
        window.queue_add_current.click()
    window.suffix.setText("_later")
    window.start_queue()
    wait_until(app, lambda: window._process is None and not window.queue.running and all(job.status != "pending" for job in window.queue.jobs))
    assert [job.status for job in window.queue.jobs] == ["failed", "complete"]
    assert [options["suffix"] for options in controlled_queue_workers] == ["_fail", "_second"]
    assert all("prompt" not in options for options in controlled_queue_workers)
    assert "[OPTIONAL LIBRARY]" in window.log.toPlainText()
    assert "[ERROR] Controlled worker failure" in window.log.toPlainText()
    assert "Stage timing:" in window.log.toPlainText()
    raw = window._log_path.read_text(encoding="utf-8")
    assert '"type": "progress"' in raw and "Controlled worker failure" in raw
    assert "Elapsed" in window.timing_label.text()
    window.copy_log.click()
    assert app.clipboard().text() == raw


def test_pause_after_current_preserves_pending_and_can_resume(app, window, sequence, controlled_queue_workers):
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    window.queue.add(window.options())
    window.queue.add(window.options())
    window.start_queue()
    window.pause_queue()
    wait_until(app, lambda: window._process is None)
    assert [job.status for job in window.queue.jobs] == ["complete", "pending"]
    assert len(controlled_queue_workers) == 1
    assert window.queue_start.isEnabled()
    window.start_queue()
    wait_until(app, lambda: window._process is None and not window.queue.running)
    assert [job.status for job in window.queue.jobs] == ["complete", "complete"]
    assert len(controlled_queue_workers) == 2


def test_cancel_stops_queue_and_does_not_start_pending_job(app, window, sequence, controlled_queue_workers):
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    first = window.options()
    first["suffix"] = "_cancel"
    window.queue.add(first)
    window.queue.add(window.options())
    window.start_queue()
    wait_until(app, window._is_running)
    window.cancel()
    wait_until(app, lambda: window._process is None)
    assert [job.status for job in window.queue.jobs] == ["cancelled", "pending"]
    assert len(controlled_queue_workers) == 1 and not window.queue.running


def test_add_multiple_source_paths_deduplicates_one_image_sequence(app, window, sequence):
    window.seed.setValue(17)
    window.add_paths_to_queue([str(sequence / "scene.1001.exr"), str(sequence / "scene.1002.exr")])
    wait_until(app, lambda: not window._tasks)
    assert len(window.queue.jobs) == 1
    assert window.queue.jobs[0].options["seed"] == 17
    assert "prompt" not in window.queue.jobs[0].options
    assert window.queue.jobs[0].status == "pending"


def test_open_logs_targets_app_logs_outside_results(app, window, monkeypatch):
    from PyQt6.QtGui import QDesktopServices

    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()) or True)
    window.open_logs.click()
    assert opened == [str(window._log_path.parent.parent)]
    assert window.build_label.text()


def test_successful_worker_saves_history_and_matching_next_job_estimates_finish(app, window, sequence, controlled_queue_workers,
                                                                             monkeypatch):
    from shotbox_matte.gui import create_window

    assert "Estimated finish unknown (learning)" in window.timing_label.text()
    window.load_input(str(sequence))
    wait_until(app, lambda: window._plan is not None)
    window.generate()
    wait_until(app, lambda: window._process is None)
    saved = json.loads(window.settings.value("timing_history"))
    assert saved and "prepare" in next(iter(saved.values()))["stages"]
    restored = create_window(window.settings)
    try:
        assert restored._history.records == saved
        wait_until(app, lambda: not restored._tasks)
    finally:
        restored.close()
    observed_timing = []
    update_timing = window._update_timing

    def record_timing():
        update_timing()
        observed_timing.append(window.timing_label.text())

    monkeypatch.setattr(window, "_update_timing", record_timing)
    window.generate()
    wait_until(app, lambda: window._process is None)
    assert any("Estimated finish ~" in text and "(historical)" in text for text in observed_timing)
    assert len(controlled_queue_workers) == 2


def test_raw_worker_bytes_are_preserved_and_split_unicode_displays_correctly(app, window):
    class Chunks:
        def __init__(self):
            self.chunks = [b"source caf\xc3", b"\xa9\nraw-invalid-byte:\xff\n"]

        def readAllStandardOutput(self):
            return self.chunks.pop(0)

    from shotbox_matte.worker import MatteWorker
    worker = MatteWorker(window, window.options())
    worker.raw_output.connect(window._record_raw)
    worker.stdout_line.connect(window._handle_worker_line)
    worker.process = Chunks()
    try:
        worker._read("stdout")
        worker._read("stdout")
    finally:
        worker.cleanup()
    assert "source café" in window.log.toPlainText()
    assert b"source caf\xc3\xa9\nraw-invalid-byte:\xff\n" in window._log_path.read_bytes()
    window.copy_log.click()
    assert "source café" in app.clipboard().text()


def test_expanded_review_and_queue_remain_accessible_in_small_window(app, window):
    window.qc_group.toggle.setChecked(True)
    window.queue_group.toggle.setChecked(True)
    window.log_group.toggle.setChecked(True)
    window.resize(960, 730)
    app.processEvents()
    assert window.width() == 960 and window.height() == 730
    scroll = window.left_scroll.verticalScrollBar()
    assert scroll.maximum() > 0
    window.workspace_tabs.setCurrentIndex(1)
    app.processEvents()
    for button in (window.queue_add_files, window.queue_add_folder, window.queue_scan_folder,
                   window.queue_start, window.queue_pause, window.queue_remove, window.queue_clear):
        window.batch_scroll.ensureWidgetVisible(button)
        app.processEvents()
        viewport_position = button.mapTo(window.batch_scroll.viewport(), button.rect().center())
        assert window.batch_scroll.viewport().rect().contains(viewport_position)


def test_portable_move_retains_external_library_with_released_filenames(tmp_path):
    from shotbox_matte.gui import _portable_preferences
    from shotbox_matte.models import MODEL_FILES
    library = r"D:\External Library"
    transformer = library + "\\" + MODEL_FILES["transformer"].replace("/", "\\")
    values = {"portable_dir": r"C:\Old App", "model_root": library,
              "models": {"transformer": transformer}}
    moved = _portable_preferences(values, tmp_path)
    assert moved["model_root"] == library
    assert moved["models"]["transformer"] == transformer
