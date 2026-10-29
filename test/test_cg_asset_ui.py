from __future__ import annotations

import os
from pathlib import Path
import sys
import time
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt6.QtCore import QProcess, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QToolButton, QWidget

import app_cache
import cg_asset_actions as actions
import cg_asset_helpers as helpers
from cg_asset_dialog import CgAssetDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(app, condition, seconds=5):
    end = time.monotonic() + seconds
    while not condition():
        app.processEvents()
        if time.monotonic() > end:
            pytest.fail("Timed out waiting for background worker")
        QTest.qWait(5)
    app.processEvents()


@pytest.fixture(params=["job_assets", "shot_assets"])
def dialog(app, tmp_path, monkeypatch, request):
    work = tmp_path / "work"
    work.mkdir()
    sources = [tmp_path / name for name in ("short_hou.py", "long_hou.py")]
    for path in sources:
        path.write_text("raise AssertionError('the UI must never execute camera scripts')")
    dialog = CgAssetDialog(tmp_path / request.param / "cg", sources, "", work)
    warnings = []
    monkeypatch.setattr("cg_asset_dialog.QMessageBox.warning", lambda *args: warnings.append(args[-1]))
    monkeypatch.setattr(helpers, "resolve_houdini", lambda configured: (Path(sys.executable), Path(sys.executable)))
    monkeypatch.setattr(helpers, "worker_environment", lambda *args: dict(os.environ))
    # Exercise real QProcess completion/cancellation with a stand-in worker, not a licence.
    fake = work / "fake_worker.py"
    fake.write_text('''import json, pathlib, sys, time
request = json.loads(pathlib.Path(sys.argv[1]).read_text())
if request.get('duration') == 999:
    time.sleep(10)
if request.get('duration') == 666:
    print('save failed', flush=True)
    sys.exit(1)
print('SHOTBOX_PROGRESS Saving test scene', flush=True)
pathlib.Path(request['output']).write_bytes(b'prepared hip')
cameras = [dict(source=source, duration=80 if 'short' in source else 120)
           for source in request.get('sources', [])]
pathlib.Path(sys.argv[2]).write_text(json.dumps(dict(output=request['output'], cameras=cameras)))
''')
    start = dialog.process.start
    monkeypatch.setattr(dialog.process, "start", lambda program, args: start(
        sys.executable, [str(fake), *args[1:]]))
    dialog.test_warnings = warnings
    yield dialog
    dialog.stop_worker()
    dialog.close()
    dialog.deleteLater()
    app.processEvents()


def test_dialog_empty_scene_requires_duration_and_publishes_after_worker_exit(app, dialog):
    dialog.name_edit.setText("Oak")
    assert not dialog.create_button.isEnabled()
    assert not dialog.cg_root.parent.exists()
    dialog.duration_spin.setValue(100)
    assert dialog.create_button.isEnabled()
    assert dialog.range_label.text() == "1001 – 1100"
    dialog._create()
    assert dialog._busy and not dialog.inputs.isEnabled()
    assert not dialog.cg_root.parent.exists()
    wait_until(app, lambda: not dialog._busy)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.process.state() == QProcess.ProcessState.NotRunning
    assert dialog.project_path.read_bytes() == b"prepared hip"
    assert (dialog.project_path.parent / "geo").is_dir()
    assert not dialog.test_warnings


def test_dialog_inspection_duration_override_invalidation_and_source_changes(app, dialog):
    dialog.name_edit.setText("Oak")
    for index in range(2):
        dialog.camera_list.item(index).setCheckState(Qt.CheckState.Checked)
    assert not dialog.create_button.isEnabled()
    dialog._inspect()
    wait_until(app, lambda: not dialog._busy)
    assert dialog.duration_spin.value() == 120
    assert not dialog.duration_spin.isEnabled()
    assert dialog.create_button.isEnabled()
    assert "80 frames" in dialog.camera_summary.text()
    assert "120 frames" in dialog.camera_summary.text()
    dialog.override_check.setChecked(True)
    dialog.duration_spin.setValue(50)
    assert dialog.range_label.text() == "1001 – 1050"
    dialog.override_check.setChecked(False)
    assert dialog.duration_spin.value() == 120
    dialog.selected_sources()[0].write_text("changed")
    dialog._create()
    assert "changed" in dialog.test_warnings[-1]
    assert not dialog._prepared and not dialog.create_button.isEnabled()
    assert not dialog.cg_root.parent.exists()
    dialog._inspect()
    wait_until(app, lambda: not dialog._busy)
    assert dialog.create_button.isEnabled()
    dialog.camera_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert not dialog._prepared and dialog.duration_spin.value() == 0


def test_failed_save_and_cancellation_do_not_publish_asset(app, dialog):
    dialog.name_edit.setText("Oak")
    dialog.duration_spin.setValue(666)
    dialog._create()
    wait_until(app, lambda: not dialog._busy)
    assert "save failed" in dialog.test_warnings[-1]
    assert not dialog.cg_root.parent.exists()
    assert dialog.create_button.isEnabled()
    dialog.duration_spin.setValue(999)
    dialog._create()
    dialog._cancel_or_close()
    wait_until(app, lambda: not dialog._busy)
    assert dialog.process.state() == QProcess.ProcessState.NotRunning
    assert not dialog.cg_root.parent.exists()
    assert "cancelled" in dialog.status_label.text()
    assert dialog.inputs.isEnabled()


def test_close_waits_for_cancelled_worker(app, dialog):
    dialog.name_edit.setText("Oak")
    dialog.duration_spin.setValue(999)
    dialog._create()
    dialog.reject()
    wait_until(app, lambda: dialog.process.state() == QProcess.ProcessState.NotRunning)
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.cg_root.parent.exists()


def test_failed_to_start_reports_error_and_restores_inputs(app, dialog, monkeypatch):
    monkeypatch.setattr(dialog.process, "start", lambda *args: QProcess.start(
        dialog.process, "/nonexistent/hython", []))
    dialog.name_edit.setText("Oak")
    dialog.duration_spin.setValue(80)
    dialog._create()
    wait_until(app, lambda: not dialog._busy)
    assert dialog.test_warnings
    assert dialog.inputs.isEnabled()
    assert not dialog.cg_root.parent.exists()


class Page(QWidget):
    def __init__(self, assets):
        super().__init__()
        self.assets = assets
        self.horizontalLayout_assets_actions = QHBoxLayout(self)
        self.btn_open_job_assets = QToolButton(self)
        self.horizontalLayout_assets_actions.addWidget(self.btn_open_job_assets)
        self.filesIO = mock.Mock()

    def _resolve_job_assets_dir(self):
        return self.assets


def test_buttons_refresh_versions_job_switch_open_and_explorer(app, tmp_path):
    assets = tmp_path / "job_assets"
    folder = assets / "cg" / "Oak"
    folder.mkdir(parents=True)
    first = folder / "Oak_v001.hip"
    first.touch()
    page = Page(assets)
    try:
        actions.refresh_buttons(page)
        assert len(page._job_cg_buttons) == 1
        button = page._job_cg_buttons[0]
        assert button.text() == first.name
        assert page.horizontalLayout_assets_actions.indexOf(button) == 1
        latest = folder / "Oak_v010.hip"
        latest.touch()
        with mock.patch.object(helpers, "open_scene") as opened, \
                mock.patch.object(actions, "configured_houdini", return_value="configured"):
            button.click()
            opened.assert_called_once_with(latest, "configured")
        assert button.text() == latest.name
        with mock.patch.object(actions.QMenu, "exec", side_effect=lambda *args: None):
            actions.show_context_menu(page, button, button.rect().center())
        actions.open_folder(page, folder)
        page.filesIO.openFileLocation.assert_called_once_with(str(folder))
        actions.refresh_buttons(page, loading=True)
        assert page._job_cg_buttons == []
        actions.refresh_buttons(page)
        assert len(page._job_cg_buttons) == 1
        page.assets = tmp_path / "another_job" / "job_assets"
        actions.refresh_buttons(page)
        assert page._job_cg_buttons == []
    finally:
        page.close()


def test_saved_scene_survives_launch_failure(app, tmp_path, monkeypatch):
    page = Page(tmp_path / "job_assets")
    project = page.assets / "cg" / "Oak" / "Oak_v001.hip"
    project.parent.mkdir(parents=True)
    project.write_bytes(b"hip")
    page._active_job_data = lambda: {}
    page._resolved_job_matchmove_dir = lambda: page.assets / "matchmove"
    monkeypatch.setattr("cg_asset_dialog.show_create_dialog", lambda *args: project)
    monkeypatch.setattr(actions, "configured_houdini", lambda: "")
    monkeypatch.setattr(helpers, "open_scene", mock.Mock(side_effect=OSError("launch failed")))
    with mock.patch.object(actions.QMessageBox, "warning") as warning:
        actions.create_for_job(page)
    assert project.read_bytes() == b"hip"
    assert page._job_cg_buttons[0].text() == project.name
    assert "retry" in warning.call_args.args[-1]
    page.close()


def test_cache_clear_preserves_active_houdini_work(tmp_path, monkeypatch):
    monkeypatch.setattr(app_cache, "CACHE_ROOT", tmp_path / ".cache")

    @app_cache.temporary_cache_task
    def operation():
        artifact = app_cache.cache_directory("temp") / "houdini-cg-scene.hip"
        artifact.write_bytes(b"in progress")
        assert app_cache.clear_cache_files()["active_temp"]
        assert artifact.read_bytes() == b"in progress"
        return artifact

    artifact = operation()
    assert not app_cache.clear_cache_files()["active_temp"]
    assert not artifact.exists()
