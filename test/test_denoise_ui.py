from __future__ import annotations

import os
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint, QProcess
from PyQt6.QtWidgets import QApplication, QDialog, QMainWindow, QPushButton, QRadioButton, QWidget

import app_cache
import denoise_controller as ui
import nuke_detector
import widgets


class DenoiseUiTests(unittest.TestCase):
    def test_output_choice_defaults_to_overwrite_and_supports_new_and_cancel(self):
        for choice, expected in (("overwrite", "overwrite"), ("new", "new"), ("cancel", None)):
            def execute(dialog):
                radios = dialog.findChildren(QRadioButton)
                self.assertEqual([radio.text() for radio in radios], ["Overwrite", "Version up"])
                self.assertTrue(radios[0].isChecked())
                if choice == "new":
                    radios[1].setChecked(True)
                return QDialog.DialogCode.Rejected if choice == "cancel" else QDialog.DialogCode.Accepted
            with mock.patch.object(QDialog, "exec", execute):
                self.assertEqual(ui.choose_output_mode(self.window, 2, 1), expected)

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QMainWindow()
        self.controller = ui.DenoiseController(self.window)
        self.script = app_cache.cache_directory("temp") / "denoise-ui-fixture.mov"
        self.script.write_text("test script")
        self.addCleanup(self.script.unlink, missing_ok=True)
        self.media = {"source": str(self.script), "shot_dir": str(app_cache.cache_directory("temp") / "denoise-ui-shot"), "shot_name": "SH010"}
        self.addCleanup(self.window.deleteLater)
        self.fake_process = mock.Mock()
        self.fake_process.readAllStandardOutput.return_value = b""
        constructor = mock.Mock(return_value=self.fake_process)
        for name in ("ProcessChannelMode", "ExitStatus", "ProcessError"):
            setattr(constructor, name, getattr(QProcess, name))
        patchers = [mock.patch.object(ui, "discover_tools"),
                    mock.patch.object(ui.importlib.util, "find_spec", return_value=object()),
                    mock.patch.object(ui, "find_nuke_executable", return_value="/nuke"),
                    mock.patch.object(ui, "get_settings_manager", return_value=mock.Mock()),
                    mock.patch.object(ui.sys, "platform", "linux"),
                    mock.patch.object(ui, "QProcess", constructor)]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.finish_if_busy)

    def finish_if_busy(self):
        if self.controller.busy:
            self.controller._finished(1, QProcess.ExitStatus.NormalExit)

    def start(self, **values):
        self.controller.start({"id": 4, "title": "SH010", **values}, dict(self.media))

    def test_existing_own_and_foreign_locks_do_not_block_or_change(self):
        for owner in ("artist@desk", "someone@other"):
            with self.subTest(owner=owner):
                shot = {"title": "SH010", "nuke_in_use": owner}
                self.controller.start(shot, dict(self.media))
                self.assertTrue(self.controller.busy)
                self.assertEqual(shot["nuke_in_use"], owner)
                self.finish_if_busy()
        self.assertEqual(self.fake_process.start.call_count, 2)

    def test_windows_single_and_batch_denoise_do_not_import_xlib(self):
        with mock.patch.object(ui.sys, "platform", "win32"), \
                mock.patch.object(ui.importlib.util, "find_spec", side_effect=AssertionError("Windows must not import Xlib")), \
                mock.patch.object(ui.QMessageBox, "information"):
            self.start()
            self.complete_job()
            self.controller.start_batch(self.batch_tasks())
            self.complete_job()
            self.app.processEvents()
            self.complete_job()
            self.app.processEvents()
        self.assertEqual(self.fake_process.start.call_count, 3)
        self.assertEqual(self.controller.last_batch_result["completed"], 2)

    def test_open_sessions_do_not_require_a_process_scan_or_database_id(self):
        with mock.patch.object(nuke_detector, "scan_open_nuke_scripts",
                               side_effect=AssertionError("Denoise must not scan editing sessions")) as scan:
            self.controller.start({"title": "Already open"}, dict(self.media))
            self.fake_process.start.assert_called_once()
            scan.assert_not_called()

    def test_cancel_preserves_cache_lease_until_child_cleanup_finishes(self):
        before = app_cache._active_temp_tasks
        self.start()
        self.assertGreater(app_cache._active_temp_tasks, before)
        self.controller.cancel()
        self.assertTrue((self.controller.directory / "cancel").is_file())
        with mock.patch.object(ui, "stop_recorded_children", return_value=True), mock.patch.object(ui.QTimer, "singleShot"):
            self.controller._finished(1, QProcess.ExitStatus.NormalExit)
        self.assertTrue(self.controller.busy)
        self.assertGreater(app_cache._active_temp_tasks, before)
        with mock.patch.object(ui, "stop_recorded_children", return_value=False):
            self.controller._cleanup_children()
        self.assertFalse(self.controller.busy)
        self.assertEqual(app_cache._active_temp_tasks, before)

    def test_failed_process_launch_releases_cache_lease(self):
        before = app_cache._active_temp_tasks
        self.start()
        self.controller._error(QProcess.ProcessError.FailedToStart)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.last_result["stage"], "failed")
        self.assertEqual(app_cache._active_temp_tasks, before)

    def test_duplicate_start_does_not_create_a_second_job(self):
        self.start()
        original = self.controller.directory
        self.start()
        self.assertEqual(self.controller.directory, original)
        self.fake_process.start.assert_called_once()

    def test_shutdown_waits_for_process_and_cleanup(self):
        self.start()
        self.assertFalse(self.controller.shutdown())
        self.assertTrue((self.controller.directory / "cancel").exists())
        with mock.patch.object(ui.QTimer, "singleShot"):
            self.finish_if_busy()
        self.assertTrue(self.controller.shutdown())

    def complete_job(self, stage="complete"):
        result = {"stage": stage, "total": 3, "error": "Bad clip" if stage == "failed" else ""}
        (self.controller.directory / "result.json").write_text(json.dumps(result))
        self.controller._finished(0, QProcess.ExitStatus.NormalExit)

    def batch_tasks(self):
        return [{"shot": {"title": title}, "media": dict(self.media)} for title in ("SH010", "SH020")]

    def test_batch_runs_serially_continues_after_failure_and_emits_idle_once(self):
        busy, idle = [], mock.Mock()
        self.controller.busy_changed.connect(busy.append)
        self.controller.idle.connect(idle)
        with mock.patch.object(ui.QMessageBox, "information") as summary:
            self.assertTrue(self.controller.start_batch(self.batch_tasks(), skipped=2))
            self.assertIn("Clip 1 of 2", self.controller.dialog.batch_status.text())
            self.fake_process.start.assert_called_once()
            self.complete_job("failed")
            self.assertTrue(self.controller.busy)
            idle.assert_not_called()
            self.app.processEvents()
            self.assertEqual(self.fake_process.start.call_count, 2)
            self.assertIn("Clip 2 of 2", self.controller.dialog.batch_status.text())
            self.assertIn("Failed: 1", self.controller.dialog.batch_status.text())
            self.complete_job()
            self.app.processEvents()
            self.assertIn("Completed: 1\nFailed: 1\nSkipped: 2", summary.call_args.args[2])
        self.assertEqual(busy, [True, False])
        idle.assert_called_once()
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.last_batch_result["remaining"], 0)

    def test_batch_cancel_stops_current_and_remaining_clips_after_cleanup(self):
        before = app_cache._active_temp_tasks
        with mock.patch.object(ui.QMessageBox, "information") as summary:
            self.controller.start_batch(self.batch_tasks())
            self.controller.cancel()
            self.assertTrue(self.controller.busy)
            self.complete_job("cancelled")
            self.app.processEvents()
            self.assertTrue(summary.call_args.args[2].startswith("Cancelled."))
        self.fake_process.start.assert_called_once()
        self.assertEqual(app_cache._active_temp_tasks, before)
        self.assertTrue(self.controller.last_batch_result["cancelled"])
        self.assertEqual(self.controller.last_batch_result["remaining"], 1)

    def test_batch_cancel_between_clips_does_not_start_next_process(self):
        with mock.patch.object(ui.QMessageBox, "information"):
            self.controller.start_batch(self.batch_tasks())
            self.complete_job()
            self.assertIsNone(self.controller.process)
            self.controller.cancel()
            self.app.processEvents()
        self.fake_process.start.assert_called_once()
        self.assertEqual(self.controller.last_batch_result["completed"], 1)
        self.assertEqual(self.controller.last_batch_result["remaining"], 1)

    def test_batch_rejects_other_starts_even_between_clips(self):
        with mock.patch.object(ui.QMessageBox, "information"):
            self.controller.start_batch(self.batch_tasks())
            self.complete_job()
            self.assertFalse(self.controller.start_batch(self.batch_tasks()))
            self.assertFalse(self.controller.start({"title": "Third"}, dict(self.media)))
            self.controller.cancel()
            self.app.processEvents()
        self.fake_process.start.assert_called_once()

    def test_batch_startup_failure_is_counted_without_blocking_next_clip(self):
        tasks = self.batch_tasks()
        tasks[0]["media"]["source"] = str(self.script.with_name("missing.mov"))
        with mock.patch.object(ui.QMessageBox, "warning") as warning, \
                mock.patch.object(ui.QMessageBox, "information") as summary:
            self.controller.start_batch(tasks)
            self.app.processEvents()
            self.fake_process.start.assert_called_once()
            self.complete_job()
            self.app.processEvents()
            warning.assert_not_called()
            self.assertIn("Completed: 1\nFailed: 1", summary.call_args.args[2])
        self.assertFalse(self.controller.busy)

    def test_batch_with_no_startable_clips_finishes_without_a_progress_dialog(self):
        with mock.patch.object(ui, "discover_tools", side_effect=RuntimeError("Missing Xvfb")), \
                mock.patch.object(ui.QMessageBox, "information") as summary:
            self.controller.start_batch(self.batch_tasks())
            for _ in range(5):
                self.app.processEvents()
            self.assertFalse(self.controller.busy)
            self.assertIsNone(self.controller.dialog)
            self.fake_process.start.assert_not_called()
            self.assertIn("Completed: 0\nFailed: 2", summary.call_args.args[2])

    def test_batch_shutdown_waits_for_cleanup_and_discards_remaining_queue(self):
        with mock.patch.object(ui.QMessageBox, "information") as summary, \
                mock.patch.object(self.window, "close") as close:
            self.controller.start_batch(self.batch_tasks())
            self.assertFalse(self.controller.shutdown())
            self.complete_job("cancelled")
            self.app.processEvents()
            self.app.processEvents()
            close.assert_called_once()
            summary.assert_not_called()
        self.fake_process.start.assert_called_once()
        self.assertTrue(self.controller.shutdown())


class CardHarness(QWidget):
    contextMenuEvent = widgets.ShotCard.contextMenuEvent
    _denoise_dashboard = widgets.ShotCard._denoise_dashboard

    def __init__(self, owner):
        super().__init__()
        self.btn_open_nuke = QPushButton("script.nk", self)
        self.btn_latest_render = QPushButton("render", self)
        self.btn_open_nuke.setGeometry(0, 0, 150, 30)
        self.btn_latest_render.setGeometry(0, 40, 150, 30)
        self.btn_open_nuke.setProperty("file_path", "/shot/clicked.nk")
        self._nuke_open_handler = owner._handle_nuke_open_request
        self.filesIO = mock.Mock()
        self._copy_to_clipboard = mock.Mock()


class CardMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_denoise_action_works_without_a_script_and_disables_while_busy(self):
        class Owner:
            _denoise_controller = None
            def _handle_nuke_open_request(self, *_):
                pass
            def _handle_denoise_request(self, card, path):
                self.path = path
        owner = Owner()
        card = CardHarness(owner)
        card.btn_open_nuke.setProperty("file_path", None)
        self.addCleanup(card.deleteLater)
        menus = []

        def execute(menu, _):
            menus.append(menu)
            action = next(action for action in menu.actions() if action.text() == "Denoise")
            if owner._denoise_controller is None:
                self.assertTrue(action.isEnabled())
                card.btn_open_nuke.setProperty("file_path", "/shot/newer.nk")
                action.trigger()
            else:
                self.assertFalse(action.isEnabled())
        event = mock.Mock()
        event.pos.return_value = QPoint(10, 10)
        event.globalPos.return_value = QPoint(10, 10)
        with mock.patch.object(widgets.QMenu, "exec", execute), mock.patch.object(widgets.sys, "platform", "linux"):
            card.contextMenuEvent(event)
            self.assertIsNone(owner.path)
            owner._denoise_controller = mock.Mock(busy=True)
            card.contextMenuEvent(event)
        self.assertEqual(len(menus), 2)
        owner._denoise_controller = None
        with mock.patch.object(widgets.QMenu, "exec", execute), mock.patch.object(widgets.sys, "platform", "win32"):
            card.contextMenuEvent(event)
        self.assertEqual(len(menus), 3)


if __name__ == "__main__":
    unittest.main()
