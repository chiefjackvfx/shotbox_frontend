from __future__ import annotations

import os
import shutil
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint, QProcess
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMainWindow, QPushButton, QRadioButton, QWidget

import app_cache
import denoise_controller as ui
import nuke_detector
import widgets


class DenoiseUiTests(unittest.TestCase):
    def test_batch_confirmation_identifies_timeline_and_clip_count_as_plain_text(self):
        def execute(dialog):
            labels = dialog.findChildren(QLabel)
            scope = next(label for label in labels if label.text().startswith('Timeline:'))
            self.assertEqual(scope.text(), 'Timeline: <Edit A>')
            self.assertEqual(scope.textFormat(), ui.Qt.TextFormat.PlainText)
            self.assertTrue(any('Denoise 3 source clip(s)' in label.text() for label in labels))
            return QDialog.DialogCode.Rejected
        with mock.patch.object(QDialog, 'exec', execute):
            self.assertIsNone(ui.choose_output_mode(self.window, 3, 1, timeline_name='<Edit A>'))

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

    def test_deadline_choice_forces_new_versions_and_allows_local_linux_projects(self):
        settings = mock.Mock()
        values = {"deadline_pool": "linux", "deadline_group": "none", "deadline_priority": 50,
                  "deadline_shared_roots": ["/Volumes/projects/PROJECTS"]}
        settings.get.side_effect = lambda key, default=None: values.get(key, default)

        def execute(dialog):
            destination = next(c for c in dialog.findChildren(ui.QComboBox) if c.accessibleName() == "Denoise destination")
            self.assertEqual(destination.currentText(), "Local")
            destination.setCurrentText("Deadline")
            overwrite, new = dialog.findChildren(QRadioButton)
            self.assertFalse(overwrite.isEnabled())
            self.assertTrue(new.isChecked())
            return QDialog.DialogCode.Accepted

        with mock.patch("deadline_ui.get_settings_manager", return_value=settings), \
                mock.patch.object(ui.sys, "platform", "linux"), mock.patch.object(QDialog, "exec", execute):
            result = ui.choose_output_mode(self.window, 4, include_destination=True)
        self.assertEqual(result["mode"], "new")
        self.assertTrue(result["deadline"]["allow_local"])
        self.assertEqual(result["deadline"]["pool"], "linux")
        self.assertEqual(result["deadline"]["shared_roots"], values["deadline_shared_roots"])
        self.assertTrue(result["deadline"]["chunked"])
        self.assertEqual(result["deadline"]["chunk_size"], 10)
        self.assertEqual(result["deadline"]["concurrent_tasks"], 1)

    def test_deadline_eligible_workers_follow_queue_selection(self):
        from deadline_ui import DeadlineOptions
        settings = mock.Mock()
        values = {"deadline_qualified_workers": ["linux-worker", "windows-worker"], "deadline_pool": "linux"}
        settings.get.side_effect = lambda key, default=None: values.get(key, default)
        with mock.patch("deadline_ui.get_settings_manager", return_value=settings):
            form = DeadlineOptions(self.window)
            form.repository = {"workers": [
                {"name": "linux-worker", "platform": "linux", "enabled": True, "pools": ["linux"]},
                {"name": "windows-worker", "platform": "win32", "enabled": True, "pools": ["houdini"]}]}
            form.update_eligible()
            self.assertIn("linux-worker", form.eligible.text())
            self.assertNotIn("windows-worker", form.eligible.text())
            form.pool.setCurrentText("houdini")
            self.assertIn("windows-worker", form.eligible.text())
            self.assertNotIn("linux-worker", form.eligible.text())
            self.assertEqual(form.options()["qualified_workers"], values["deadline_qualified_workers"])
            form.pool.setCurrentText("missing")
            self.assertIn("None.", form.eligible.text())
            form.stop()

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
                    mock.patch.object(ui, "batch_worker_limit", return_value=1),
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

    def start_deadline(self, status="submitted"):
        import deadline_denoise
        media = dict(self.media, deadline={"allow_local": True, "client": "/client"})
        with mock.patch("deadline_client.discover_client", return_value="/client"):
            self.controller.start({"id": 4, "title": "SH010"}, media)
        directory = self.controller.directory
        self.addCleanup(shutil.rmtree, directory, True)
        if self.controller.request_path is None:
            return directory, None
        request = json.loads(self.controller.request_path.read_text())
        receipt, lock = deadline_denoise.submission_paths(request)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(receipt))
        receipt.write_text(json.dumps({"status": status, "job_id": "b" * 24,
                                       "lock": str(lock), "directory": str(directory)}))
        self.addCleanup(receipt.unlink, missing_ok=True)
        self.addCleanup(lock.unlink, missing_ok=True)
        result = {"stage": "submission_unknown" if status == "unknown" else status,
                  "job_id": "b" * 24, "receipt": str(receipt), "error": "test failure"}
        (directory / "result.json").write_text(json.dumps(result))
        (directory / "job-info.job").write_text("submission package")
        return directory, receipt

    def test_deadline_receipt_and_workspace_removed_only_after_children_stop(self):
        directory, receipt = self.start_deadline()
        with mock.patch.object(ui, "stop_recorded_children", side_effect=[True, False]), \
                mock.patch.object(ui.QTimer, "singleShot") as retry:
            self.controller._finished(0, QProcess.ExitStatus.NormalExit)
            self.assertTrue(directory.exists())
            self.assertTrue(receipt.exists())
            self.assertIsNotNone(self.controller._lease)
            retry.call_args.args[1]()
        self.assertFalse(directory.exists())
        self.assertFalse(receipt.exists())
        self.assertEqual(self.controller.last_result["job_id"], "b" * 24)
        self.assertNotIn("receipt", self.controller.last_result)
        self.assertFalse(self.controller.dialog.logs_button.isEnabled())
        self.assertIsNone(self.controller._lease)
        self.assertFalse((Path(self.media["shot_dir"]) / "shot_assets/deadline").exists())

    def test_deadline_cancelled_and_failed_workspaces_are_removed(self):
        for status in ("cancelled", "failed"):
            with self.subTest(status=status):
                directory, receipt = self.start_deadline(status)
                self.controller._finished(1, QProcess.ExitStatus.NormalExit)
                self.assertFalse(directory.exists())
                self.assertFalse(receipt.exists())
                self.assertEqual(self.controller.last_result["stage"], status)
                self.assertNotIn("receipt", self.controller.last_result)

    def test_deadline_unknown_outcome_keeps_only_cache_recovery_files(self):
        directory, receipt = self.start_deadline("unknown")
        self.controller._finished(1, QProcess.ExitStatus.NormalExit)
        self.assertTrue(directory.exists())
        self.assertTrue(receipt.exists())
        self.assertEqual(self.controller.last_result["stage"], "submission_unknown")
        self.assertEqual(self.controller.last_result["receipt"], str(receipt))
        self.assertTrue(self.controller.dialog.logs_button.isEnabled())
        self.assertFalse((Path(self.media["shot_dir"]) / "shot_assets/deadline").exists())

    def test_deadline_startup_failure_removes_allocated_workspace(self):
        with mock.patch.object(ui, "create_request", side_effect=OSError("request failed")), \
                mock.patch.object(ui.QMessageBox, "warning"), \
                mock.patch("deadline_client.discover_client", return_value="/client"):
            media = dict(self.media, deadline={"allow_local": True, "client": "/client"})
            self.controller.start({"id": 4, "title": "SH010"}, media)
        self.assertFalse(self.controller.directory.exists())
        self.assertIsNone(self.controller._lease)
        self.assertEqual(self.controller.last_result["stage"], "failed")

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

    def parallel_tasks(self):
        return [{"shot": {"title": f"SH{i}"}, "media": dict(self.media, shot_dir=self.media["shot_dir"] + str(i))}
                for i in range(4)]

    def finish_worker(self, worker, stage="complete"):
        (worker.directory / "result.json").write_text(json.dumps({"stage": stage, "total": 3, "error": "Bad clip"}))
        worker._finished(0, QProcess.ExitStatus.NormalExit)
        self.app.processEvents()

    def test_parallel_batch_refills_slots_and_reports_failures_once(self):
        idle = mock.Mock()
        self.controller.idle.connect(idle)
        with mock.patch.object(ui, "batch_worker_limit", return_value=2), mock.patch.object(ui.QMessageBox, "information") as summary:
            self.controller.start_batch(self.parallel_tasks())
            self.assertEqual(len(self.controller._workers), 2)
            first, second = self.controller._workers
            self.finish_worker(second, "failed")
            self.assertEqual(len(self.controller._workers), 2)
            self.assertEqual(self.controller._batch["index"], 3)
            self.finish_worker(first)
            self.assertEqual(self.controller._batch["index"], 4)
            for worker in list(self.controller._workers):
                self.finish_worker(worker)
            self.assertFalse(self.controller.busy)
            self.assertEqual(self.controller.last_batch_result["completed"], 3)
            self.assertEqual(self.controller.last_batch_result["failed"], 1)
            self.assertIn("SH1: Bad clip", self.controller.last_batch_result["errors"][0])
            summary.assert_called_once()
            idle.assert_called_once()

    def test_parallel_cancel_from_either_dialog_cancels_all_and_waits(self):
        with mock.patch.object(ui, "batch_worker_limit", return_value=2), mock.patch.object(ui.QMessageBox, "information"):
            self.controller.start_batch(self.parallel_tasks())
            workers = list(self.controller._workers)
            workers[0].cancel()
            self.assertTrue(all((w.directory / "cancel").exists() for w in workers))
            self.finish_worker(workers[0], "cancelled")
            self.assertTrue(self.controller.busy)
            self.finish_worker(workers[1], "cancelled")
            self.assertFalse(self.controller.busy)
            self.assertEqual(self.controller.last_batch_result["remaining"], 2)
            self.assertTrue(self.controller.last_batch_result["cancelled"])

    def test_parallel_batch_serializes_shared_output_folders(self):
        with mock.patch.object(ui, "batch_worker_limit", return_value=2), mock.patch.object(ui.QMessageBox, "information"):
            self.controller.start_batch(self.batch_tasks())
            self.assertEqual(len(self.controller._workers), 1)
            self.finish_worker(next(iter(self.controller._workers)))
            self.assertEqual(len(self.controller._workers), 1)
            self.finish_worker(next(iter(self.controller._workers)))
            self.assertEqual(self.controller.last_batch_result["completed"], 2)

    def test_parallel_startup_failures_do_not_show_modal_warnings(self):
        tasks = self.parallel_tasks()
        for task in tasks:
            task["media"]["source"] = str(self.script) + ".missing"
        with mock.patch.object(ui, "batch_worker_limit", return_value=2), \
                mock.patch.object(ui.QMessageBox, "information") as summary, \
                mock.patch.object(ui.QMessageBox, "warning") as warning:
            self.controller.start_batch(tasks)
            self.assertFalse(self.controller.busy)
            self.assertEqual(self.controller.last_batch_result["failed"], 4)
            summary.assert_called_once()
            warning.assert_not_called()

    def test_local_deadline_start_reserves_new_version_and_uses_submission_worker(self):
        media = dict(self.media, mode="overwrite", deadline={"allow_local": True, "shared_roots": [],
                                                           "pool": "linux", "group": "none", "batch_name": "test"})
        with mock.patch("deadline_client.discover_client", return_value="/client"):
            self.assertTrue(self.controller.start({"title": "local"}, media))
        request = json.loads(self.controller.request_path.read_text())
        self.assertTrue(request["deadline"]["allow_local"])
        self.assertEqual(len(request["deadline"]["submission_id"]), 32)
        self.assertTrue(self.fake_process.start.call_args.args[1][0].endswith("deadline_denoise.py"))

    def test_submitted_batch_ids_survive_cancellation_of_remaining_clips(self):
        with mock.patch.object(ui.QMessageBox, "information") as summary:
            self.controller.start_batch(self.batch_tasks())
            result = {"stage": "submitted", "job_id": "a" * 24, "receipt": "/shot/receipt.json"}
            (self.controller.directory / "result.json").write_text(json.dumps(result))
            self.controller._finished(0, QProcess.ExitStatus.NormalExit)
            self.controller.cancel()
            self.app.processEvents()
        self.assertEqual(self.controller.last_batch_result["submitted"][0]["job_id"], "a" * 24)
        self.assertIn("queued, not rendered", summary.call_args.args[2])
        self.assertEqual(self.controller.last_batch_result["remaining"], 1)

    def test_parallel_shutdown_waits_for_both_workers(self):
        with mock.patch.object(ui, "batch_worker_limit", return_value=2), \
                mock.patch.object(ui.QMessageBox, "information") as summary, \
                mock.patch.object(ui.QTimer, "singleShot") as close:
            self.controller.start_batch(self.parallel_tasks())
            workers = list(self.controller._workers)
            self.assertFalse(self.controller.shutdown())
            self.finish_worker(workers[0], "cancelled")
            close.assert_not_called()
            self.assertTrue(self.controller.busy)
            self.finish_worker(workers[1], "cancelled")
            self.assertFalse(self.controller.busy)
            close.assert_called_once()
            summary.assert_not_called()

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
    _populate_original_clip_menu = widgets.ShotCard._populate_original_clip_menu
    _populate_original_clip_submenu = widgets.ShotCard._populate_original_clip_submenu
    _resolve_original_clip_entries = widgets.ShotCard._resolve_original_clip_entries
    _is_valid_original_clip_path = widgets.ShotCard._is_valid_original_clip_path

    def __init__(self, owner):
        super().__init__()
        self.btn_open_nuke = QPushButton("script.nk", self)
        self.btn_latest_render = QPushButton("render", self)
        self.btn_open_nuke.setGeometry(0, 0, 150, 30)
        self.btn_latest_render.setGeometry(0, 40, 150, 30)
        self.btn_open_nuke.setProperty("file_path", "/shot/clicked.nk")
        self.label_original_clip = QLabel('Original clips', self)
        self.label_original_clip.setGeometry(0, 80, 150, 30)
        self.data = {'original_clips': ['/shot/plate1.mov', '/shot/plate2.mov']}
        self._original_clip_entries = self._resolve_original_clip_entries()
        self._nuke_open_handler = owner._handle_nuke_open_request
        self.filesIO = mock.Mock()
        self._copy_to_clipboard = mock.Mock()
        self._on_make_preview_clicked = mock.Mock()
        self._on_make_precomp_exr_clicked = mock.Mock()


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
            submenu = menu.actions()[1].menu()
            action = next(action for action in submenu.actions() if action.text() == "Denoise")
            if owner._denoise_controller is None and widgets.sys.platform in ('linux', 'win32'):
                self.assertTrue(action.isEnabled())
                card.btn_open_nuke.setProperty("file_path", "/shot/newer.nk")
                action.trigger()
            else:
                self.assertFalse(action.isEnabled())
        event = mock.Mock()
        event.pos.return_value = QPoint(10, 90)
        event.globalPos.return_value = QPoint(10, 90)
        with mock.patch.object(widgets.QMenu, "exec", execute), mock.patch.object(widgets.sys, "platform", "linux"):
            card.contextMenuEvent(event)
            self.assertEqual(owner.path, '/shot/plate2.mov')
            owner._denoise_controller = mock.Mock(busy=True)
            card.contextMenuEvent(event)
        self.assertEqual(len(menus), 2)
        owner._denoise_controller = None
        with mock.patch.object(widgets.QMenu, "exec", execute), mock.patch.object(widgets.sys, "platform", "win32"):
            card.contextMenuEvent(event)
        self.assertEqual(len(menus), 3)
        with mock.patch.object(widgets.QMenu, "exec", execute), mock.patch.object(widgets.sys, "platform", "darwin"):
            card.contextMenuEvent(event)

    def test_clip_denoise_is_disabled_without_dashboard(self):
        card = CardHarness(mock.Mock())
        card._nuke_open_handler = None
        self.addCleanup(card.deleteLater)
        menu = widgets.QMenu(card)
        card._populate_original_clip_menu(menu)
        for action in menu.actions():
            denoise = next(item for item in action.menu().actions() if item.text() == 'Denoise')
            self.assertFalse(denoise.isEnabled())

    def test_nuke_menu_never_offers_denoise(self):
        owner = mock.Mock()
        card = CardHarness(owner)
        self.addCleanup(card.deleteLater)
        event = mock.Mock()
        event.pos.return_value = QPoint(10, 10)
        def execute(menu, _):
            self.assertNotIn('Denoise', [action.text() for action in menu.actions()])
        with mock.patch.object(widgets.QMenu, 'exec', execute):
            for path in ('/shot/comp.nk', None):
                card.btn_open_nuke.setProperty('file_path', path)
                card.contextMenuEvent(event)


class BatchWorkerLimitTests(unittest.TestCase):
    def test_only_large_linux_hosts_use_two_workers_and_opt_out_is_respected(self):
        import psutil
        from types import SimpleNamespace
        for platform, cpus, gib, override, expected in (
            ("linux", 128, 64, "", 2), ("linux", 64, 32, "", 2),
            ("linux", 32, 64, "", 1), ("linux", None, 64, "", 1),
            ("linux", 128, 31, "", 1), ("linux", 128, 64, "1", 1),
            ("win32", 128, 64, "", 1),
        ):
            with self.subTest(platform=platform, cpus=cpus, gib=gib, override=override), \
                    mock.patch.object(ui.sys, "platform", platform), \
                    mock.patch.object(ui.os, "cpu_count", return_value=cpus), \
                    mock.patch.object(psutil, "virtual_memory", return_value=SimpleNamespace(available=gib * 1024**3)), \
                    mock.patch.dict(os.environ, SHOTBOX_DENOISE_BATCH_WORKERS=override):
                self.assertEqual(ui.batch_worker_limit(), expected)


if __name__ == "__main__":
    unittest.main()
