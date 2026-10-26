"""Real Qt, EXR planning and process-boundary checks for the compact workflow."""
import json
import os
from pathlib import Path
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QProcess, QSettings
from PyQt6.QtWidgets import QApplication, QMenu

import app_cache
from toolbox.shotbox_matte import plugin, roto_dialog
from toolbox.shotbox_matte.shotbox_matte.imaging import write_exr
from toolbox.shotbox_matte.shotbox_matte.models import MODEL_FILES
from toolbox.shotbox_matte.shotbox_matte.runtime import RuntimeConfig
from toolbox_page import ToolboxPage


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait(app, condition, timeout=10):
    deadline = time.monotonic() + timeout
    while not condition():
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Qt work did not finish")
        time.sleep(.005)
    app.processEvents()


def source(folder, count=77, stem="plate", first=1001):
    folder.mkdir(parents=True, exist_ok=True)
    for number in range(first, first + count):
        write_exr(folder / f"{stem}.{number}.exr", np.full((18, 32, 3), .18, np.float32), "half")
    return folder / f"{stem}.{first}.exr"


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setattr(app_cache, "CACHE_ROOT", tmp_path / ".cache")
    temporary = app_cache.cache_directory("temp") / "shotbox-matte"
    temporary.mkdir()
    models = tmp_path / "models"
    for name in MODEL_FILES.values():
        weight = models / name
        weight.parent.mkdir(parents=True, exist_ok=True)
        weight.touch()
    state = tmp_path / "state"
    state.mkdir()
    settings = QSettings(str(state / "settings.ini"), QSettings.Format.IniFormat)
    runtime = RuntimeConfig(plugin.APPLICATION_ROOT, Path(sys.executable), str(models), state, temporary)
    monkeypatch.setattr(plugin, "_host_context", lambda: (settings, runtime))
    return settings, runtime


@pytest.fixture
def dialog(app, context, tmp_path):
    frame = source(tmp_path / "plate_v003")
    window = plugin.open_roto(None, frame)
    window.show()
    wait(app, lambda: window._plan is not None)
    yield window
    window.close()
    wait(app, lambda: not window.is_busy())
    app.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_defaults_exact_preview_sequence_and_no_settings_writes(app, context, tmp_path):
    settings, _ = context
    saved = json.dumps({"fps": 12, "suffix": "_saved", "source_color": "srgb", "existing": "overwrite"})
    settings.setValue("preferences", saved)
    frame = source(tmp_path / "plate_v003")
    source(frame.parent, count=100, stem="other", first=2001)
    # A mixed folder must stay on the menu's explicitly selected sequence.
    window = plugin.open_roto(None, frame)
    try:
        wait(app, lambda: window._plan is not None)
        assert window._plan["selected_count"] == 77
        assert window._plan["padded_count"] == 81
        assert window._plan["first_frame"] == 1001
        assert window.frame_summary.text() == "77 selected → 81 model frames · 4 added copies"
        assert window.options().fps == 25
        assert window.options().source_color == "acescg"
        assert window.options().existing == "version-up"
        assert window.options().suffix == "_roto"
        assert window.seed.value() == window.options().seed == 42
        assert window.options().precision == "half"
        assert window.options().temporal_mode == "segmented"
        assert window._plan["output_preview"].endswith("plate_roto_v003")
        assert not Path(window._plan["output_preview"]).exists()
        assert not window.range_enabled.isChecked()
        assert not window.source_start.isEnabled()
        assert not window.queue_button.isEnabled()
        assert window.queue_button.toolTip() == "Coming later"
        assert window.generate_button.isEnabled()
        assert settings.value("preferences") == saved
    finally:
        window.close()
        wait(app, lambda: not window.is_busy())


def test_ranges_limits_sizing_and_invalid_suffix(app, dialog, tmp_path):
    dialog.range_enabled.setChecked(True)
    dialog.source_start.setValue(1005)
    dialog.source_end.setValue(1010)
    wait(app, lambda: dialog._plan is not None)
    assert dialog._plan["selected_count"] == 6
    assert dialog._plan["first_frame"] == 1005
    dialog.source_start.setValue(1011)
    wait(app, lambda: "must not exceed" in dialog.validation_info.text())
    assert not dialog.generate_button.isEnabled()
    dialog.source_start.setValue(999)
    wait(app, lambda: "outside" in dialog.validation_info.text())
    dialog.range_enabled.setChecked(False)
    dialog.generation_height.setValue(1088)
    wait(app, lambda: dialog._plan is not None)
    assert dialog.generation_width.value() <= 1920
    assert dialog.generation_height.value() <= 1088
    assert abs(dialog.generation_width.value() / dialog.generation_height.value() - 32 / 18) < .05
    dialog.suffix.setText("../bad")
    wait(app, lambda: dialog._plan is None and "suffix" in dialog.validation_info.text().lower())
    frame = source(tmp_path / "long_v001", count=278)
    dialog.load_source(frame)
    wait(app, lambda: dialog._plan is not None)
    assert dialog._plan["selected_count"] == 278
    assert dialog.generate_button.isEnabled()
    dialog.range_enabled.setChecked(True)
    dialog.source_end.setValue(1240)
    wait(app, lambda: dialog._plan is not None)
    assert dialog._plan["selected_count"] == 240
    assert dialog._plan["chunk_count"] > 1
    assert "segments" in dialog.frame_summary.text()


def test_stale_plan_cannot_replace_new_settings(app, dialog, monkeypatch):
    original = roto_dialog.plan_job
    started, release = threading.Event(), threading.Event()

    def slow(options):
        if options.suffix == "_old":
            started.set()
            assert release.wait(5)
        return original(options)

    monkeypatch.setattr(roto_dialog, "plan_job", slow)
    try:
        dialog.suffix.setText("_old")
        wait(app, started.is_set)
        dialog.suffix.setText("_new")
        assert not dialog.generate_button.isEnabled()
        wait(app, lambda: dialog._plan is not None and "_new_" in dialog.output_preview.text())
    finally:
        release.set()
    wait(app, lambda: not dialog.is_busy())
    assert "_new_" in dialog.output_preview.text()


def test_long_clip_can_switch_between_segments_and_experimental_pass(app, dialog, tmp_path):
    saved = dialog.settings.value("preferences")
    dialog.load_source(source(tmp_path / "long_v001", count=278))
    wait(app, lambda: dialog._plan is not None)
    assert dialog.temporal_mode.currentData() == "segmented"
    assert dialog._plan["selected_count"] == 278
    assert dialog._plan["chunk_count"] == 3
    assert "3 segments" in dialog.frame_summary.text()

    dialog.temporal_mode.setCurrentIndex(dialog.temporal_mode.findData("full-range"))
    assert not dialog.generate_button.isEnabled()
    wait(app, lambda: dialog._plan is not None)
    assert dialog.options().temporal_mode == dialog._plan["temporal_mode"] == "full-range"
    assert dialog._plan["selected_count"] == 278
    assert dialog._plan["chunk_count"] == 1
    assert dialog._plan["padded_count"] == 281
    assert "one pass" in dialog.frame_summary.text()
    assert "experimental full range" in dialog.validation_info.text()
    assert "GPU memory" in dialog.temporal_mode_hint.text()
    assert dialog.generate_button.isEnabled()

    dialog.temporal_mode.setCurrentIndex(dialog.temporal_mode.findData("segmented"))
    wait(app, lambda: dialog._plan is not None)
    assert dialog._plan["chunk_count"] == 3
    assert "one pass" not in dialog.frame_summary.text()
    assert "overlapping segments" in dialog.temporal_mode_hint.text()
    assert dialog.settings.value("preferences") == saved


def controlled_worker(monkeypatch, script):
    original = QProcess.start

    def start(process, *args):
        # Override execution only; retain the actual worker environment and job file.
        process.setArguments(["-u", "-c", script, process.arguments()[-1]])
        return original(process, *args)

    monkeypatch.setattr(QProcess, "start", start)


@pytest.mark.parametrize("mode", ["segmented", "full-range"])
def test_worker_completion_uses_reserved_result_and_cleans_temporary_job(app, dialog, monkeypatch, mode):
    dialog.seed.setValue(12345)
    dialog.temporal_mode.setCurrentIndex(dialog.temporal_mode.findData(mode))
    wait(app, lambda: dialog._plan is not None)
    interpreter_record = dialog.runtime.state_directory / "worker-python.txt"
    controlled_worker(monkeypatch,
        "import json,sys,time; from pathlib import Path; "
        "p=Path(sys.argv[-1]); o=json.loads(p.read_text()); "
        f"Path({str(interpreter_record)!r}).write_text(sys.executable); "
        "print(json.dumps({'type':'progress','stage':'export','current':1,'total':77}),flush=True); "
        "time.sleep(.2); "
        "print(json.dumps({'type':'result','result':{'output_dir':str(p.parent/'reserved_v004'),'frame_count':77}}))")
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda *error: errors.append(error))
    dialog.generate_button.click()
    assert not errors, [(kind.__name__, str(error)) for kind, error, _ in errors]
    job = dialog._worker.path
    assert dialog._worker.process.program() == sys.executable
    assert job.is_relative_to(dialog.runtime.temporary_directory)
    assert json.loads(job.read_text())["input_path"] == dialog.source_frame
    assert json.loads(job.read_text())["seed"] == 12345
    assert json.loads(job.read_text())["temporal_mode"] == mode
    assert not dialog.controls.isEnabled()
    assert not dialog.temporal_mode.isEnabled()
    assert dialog.is_busy()
    wait(app, lambda: dialog._worker is None)
    assert interpreter_record.read_text() == os.path.abspath(sys.executable)
    assert not job.exists()
    assert "77 RGB matte frames" in dialog.stage_label.text()
    assert "COMPLETED EXPORT" in dialog.output_title.text()
    assert dialog.output_preview.text().endswith("reserved_v004")
    assert dialog.open_output.isEnabled()
    assert dialog.controls.isEnabled()
    assert not dialog.queue_button.isEnabled()


@pytest.mark.parametrize("failure", ["error", "crash", "start"])
def test_worker_failures_are_visible_and_retryable(app, dialog, monkeypatch, failure):
    if failure == "start":
        original = QProcess.start

        def start(process, *args):
            process.setProgram("/nonexistent/shotbox-roto-worker")
            return original(process, *args)

        monkeypatch.setattr(QProcess, "start", start)
    else:
        controlled_worker(monkeypatch,
            "import json,sys; print(json.dumps({'type':'error','message':'Controlled inference failure'})); sys.exit(7)"
            if failure == "error" else "import os; os._exit(9)")
    dialog.generate_button.click()
    wait(app, lambda: dialog._worker is None)
    assert dialog._result is None
    assert not dialog.open_output.isEnabled()
    assert dialog.details.isChecked()
    assert dialog.generate_button.isEnabled()
    assert dialog.stage_label.text()


def test_cancel_and_close_wait_for_worker_and_release_cache(app, context, tmp_path, monkeypatch):
    controlled_worker(monkeypatch,
        "import json,sys,time; from pathlib import Path\n"
        "p=Path(sys.argv[-1]+'.cancel')\n"
        "print(json.dumps({'type':'progress','stage':'infer','current':1,'total':8}),flush=True)\n"
        "while not p.exists(): time.sleep(.01)\n"
        "print(json.dumps({'type':'cancelled','message':'Cancelled'})); sys.exit(130)\n")
    page = ToolboxPage()
    page.open_roto(source(tmp_path / "plate_v001"))
    window = page._tool_windows["shotbox_roto"]
    wait(app, lambda: window.generate_button.isEnabled())
    window.generate_button.click()
    wait(app, lambda: "Infer" in window.stage_label.text())
    job = window._worker.path
    assert page.has_busy_tools()
    assert app_cache.clear_cache_files()["active_temp"]
    assert job.exists()
    window.close()
    assert window.isVisible()
    assert window._worker is not None
    wait(app, lambda: not window.is_busy())
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not page._tool_windows
    assert not app_cache.clear_cache_files()["active_temp"]
    page.close()


def test_popup_reuse_source_reset_and_busy_source_protection(app, context, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    page = ToolboxPage()
    first = source(tmp_path / "first_v001")
    second = source(tmp_path / "second_v003", count=20)
    try:
        page.open_roto(first)
        window = page._tool_windows["shotbox_roto"]
        wait(app, lambda: window._plan is not None)
        window.fps.setValue(30)
        window.seed.setValue(17)
        window.temporal_mode.setCurrentIndex(window.temporal_mode.findData("full-range"))
        page.open_roto(first)
        assert page._tool_windows["shotbox_roto"] is window
        assert window.fps.value() == 30
        assert window.seed.value() == 17
        assert window.temporal_mode.currentData() == "full-range"
        wait(app, lambda: not window.is_busy() and window._plan is not None)
        page.open_roto(second)
        wait(app, lambda: window._plan is not None)
        assert window.fps.value() == 25
        assert window.seed.value() == 42
        assert window.temporal_mode.currentData() == "segmented"
        assert window._plan["selected_count"] == 20
        with monkeypatch.context() as busy:
            busy.setattr(window, "is_busy", lambda: True)
            messages = []
            busy.setattr(QMessageBox, "information", lambda *args: messages.append(args))
            page.open_roto(first)
            assert window.source_frame == str(second)
            assert messages
    finally:
        page.close_tools()
        page.close()
        app.processEvents()


def test_roto_action_passes_the_preview_sequence(app, tmp_path, monkeypatch):
    from test.test_shotcard_matchmove import ShotCardMatchmoveTests

    fixture = ShotCardMatchmoveTests()
    frame = source(tmp_path / "renders" / "precomp" / "plate_v001", count=3)
    card, _ = fixture._make_card(str(tmp_path))
    menu = QMenu()
    received = []
    monkeypatch.setattr(card, "_open_roto", received.append)
    try:
        card._populate_precomp_menu(menu)
        folder = menu.actions()[0].menu()
        action = next(action for action in folder.actions() if action.text() == "Roto")
        assert action.isEnabled()
        action.trigger()
        assert received == [str(frame)]
    finally:
        menu.close()
        card.close()
        fixture.doCleanups()


def test_missing_weights_keep_plan_visible(app, context, tmp_path):
    settings, runtime = context
    (Path(runtime.model_root) / MODEL_FILES["alpha_lora"]).unlink()
    window = plugin.open_roto(None, source(tmp_path / "plate_v001"))
    try:
        wait(app, lambda: window._plan is not None)
        assert not window.generate_button.isEnabled()
        assert "Missing Matte weights" in window.stage_label.text()
        assert "Toolbox" in window.stage_label.text()
    finally:
        window.close()
        wait(app, lambda: not window.is_busy())


def test_padding_trim_and_version_race_export_real_exrs(app, dialog, monkeypatch):
    from types import SimpleNamespace
    from toolbox.shotbox_matte.shotbox_matte.core import run_job
    from toolbox.shotbox_matte.shotbox_matte.imaging import read_exr

    captured = []

    class Engine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            captured.append(len(frames))
            for frame in frames:
                yield np.full_like(frame, .75)

    fake = SimpleNamespace(ModelConfig=SimpleNamespace(from_dict=lambda values: values), NativeAlphaEngine=Engine)
    monkeypatch.setitem(sys.modules, "toolbox.shotbox_matte.shotbox_matte.native", fake)
    monkeypatch.setenv("SHOTBOX_TEMP_DIR", str(dialog.runtime.temporary_directory))
    monkeypatch.setenv("SHOTBOX_JOB_DIR", str(dialog.runtime.temporary_directory / "jobs"))
    # Another export reserves the displayed v003 after planning.
    provisional = Path(dialog._plan["output_preview"])
    provisional.mkdir()
    sentinel = provisional / "keep.txt"
    sentinel.write_text("existing version")
    options = dialog.options()
    options.generation_width, options.generation_height = 64, 64
    final = provisional.with_name("plate_roto_v004")
    observed_staging = []

    def progress(stage, current, total, message):
        if stage in {"prepare", "export", "commit"}:
            assert not final.exists()
            observed_staging.append(stage)
        elif stage == "complete":
            assert len(list(final.glob("*.exr"))) == 77

    result = run_job(options, progress=progress)
    assert "export" in observed_staging and "commit" in observed_staging
    files = sorted(Path(result["output_dir"]).glob("*.exr"))
    assert captured == [81]
    assert len(files) == result["frame_count"] == 77
    assert files[0].name.endswith(".1001.exr")
    assert files[-1].name.endswith(".1077.exr")
    assert Path(result["output_dir"]).name == "plate_roto_v004"
    assert sentinel.read_text() == "existing version"
    assert read_exr(files[0]).shape == (18, 32, 3)
    np.testing.assert_allclose(read_exr(files[-1]), .75)


def test_unresponsive_worker_is_killed_after_cancel(app, dialog, monkeypatch):
    controlled_worker(monkeypatch,
        "import json,time; print(json.dumps({'type':'progress','stage':'inference','current':1,'total':8}),flush=True); "
        "time.sleep(30)")
    dialog.generate_button.click()
    wait(app, lambda: "Inference" in dialog.stage_label.text())
    worker = dialog._worker
    dialog.cancel()
    # Exercise the timeout callback directly rather than sleeping ten seconds.
    worker._kill_if_running()
    wait(app, lambda: dialog._worker is None)
    assert "Cancelled" in dialog.stage_label.text()
    assert not dialog.open_output.isEnabled()
    assert not worker.path.exists()


@pytest.mark.parametrize("problem", ["runtime", "configuration"])
def test_setup_problems_disable_generate_without_hiding_output_plan(app, context, tmp_path, monkeypatch, problem):
    from dataclasses import replace

    settings, runtime = context
    if problem == "runtime":
        runtime = replace(runtime, worker_python=tmp_path / "missing-python")
    else:
        config = tmp_path / "malformed.json"
        config.write_text("[]")
        runtime = replace(runtime, model_config=str(config))
    monkeypatch.setattr(plugin, "_host_context", lambda: (settings, runtime))
    window = plugin.open_roto(None, source(tmp_path / "plate_v001"))
    try:
        wait(app, lambda: window._plan is not None)
        assert not window.generate_button.isEnabled()
        assert "Settings → Plugins" in window.stage_label.text()
        assert window.output_preview.text()
    finally:
        window.close()
        wait(app, lambda: not window.is_busy())


def test_small_dialog_wraps_actions_and_scrolls_long_paths(app, dialog):
    from PyQt6.QtWidgets import QScrollArea

    dialog.setStyleSheet((Path(__file__).parents[1] / "ui" / "dark_v01.qss").read_text())
    dialog.output_preview.setText("/Volumes/Projects/" + "long_project_name/" * 12 + "phone040_DN_roto_v003")
    dialog.details.setChecked(True)
    dialog.resize(460, 420)
    app.processEvents()
    assert (dialog.width(), dialog.height()) == (460, 420)
    scroll = dialog.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum() == 0
    assert scroll.verticalScrollBar().maximum() > 0
    for button in (dialog.close_button, dialog.queue_button, dialog.generate_button):
        position = button.mapTo(dialog, button.rect().bottomRight())
        assert position.x() < dialog.width()
        assert position.y() < dialog.height()
