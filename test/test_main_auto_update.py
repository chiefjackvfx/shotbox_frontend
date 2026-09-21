from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtCore import QProcess  # noqa: E402
from PyQt6.QtGui import QCloseEvent  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QMainWindow,
    QMessageBox as QtMessageBox,
    QTabWidget,
    QWidget,
)

import app_update  # noqa: E402
import main  # noqa: E402


class DummySignal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class FakeProcess:
    def __init__(self):
        self.finished = DummySignal()
        self.errorOccurred = DummySignal()
        self.working_directory = None
        self.start_calls = []
        self.deleted = False
        self.blocked = False
        self.terminated = False
        self.killed = False
        self.process_state = QProcess.ProcessState.NotRunning
        self.stderr = b""
        self.wait_results = [True]

    def setWorkingDirectory(self, path):
        self.working_directory = path

    def start(self, program, arguments):
        self.start_calls.append((program, arguments))
        self.process_state = QProcess.ProcessState.Running

    def readAllStandardError(self):
        return self.stderr

    def errorString(self):
        return "could not start git"

    def deleteLater(self):
        self.deleted = True

    def state(self):
        return self.process_state

    def blockSignals(self, blocked):
        self.blocked = blocked

    def terminate(self):
        self.terminated = True

    def waitForFinished(self, _timeout):
        return self.wait_results.pop(0) if self.wait_results else True

    def kill(self):
        self.killed = True


class FakeMessageBox:
    Icon = QtMessageBox.Icon
    ButtonRole = QtMessageBox.ButtonRole
    next_click = "Remind Later"
    last_instance = None

    def __init__(self, parent):
        self.parent = parent
        self.buttons = []
        self.default_button = None
        self.escape_button = None
        self.clicked_button = None
        self.text = ""
        self.informative_text = ""
        FakeMessageBox.last_instance = self

    def setIcon(self, _icon):
        return

    def setWindowTitle(self, _title):
        return

    def setText(self, text):
        self.text = text

    def setInformativeText(self, text):
        self.informative_text = text

    def addButton(self, text, role):
        button = SimpleNamespace(text=text, role=role)
        self.buttons.append(button)
        return button

    def setDefaultButton(self, button):
        self.default_button = button

    def setEscapeButton(self, button):
        self.escape_button = button

    def exec(self):
        self.clicked_button = next(
            button
            for button in self.buttons
            if button.text == self.next_click
        )

    def clickedButton(self):
        return self.clicked_button


class FakeSettingsPage(QWidget):
    def __init__(self):
        super().__init__()
        self.in_progress_calls = 0
        self.applied_statuses = []
        self.launch_calls = []
        self.focus_calls = 0

    def set_update_check_in_progress(self):
        self.in_progress_calls += 1

    def apply_update_status(self, status):
        self.applied_statuses.append(status)

    def launch_update_and_restart(self, status, *, parent=None, confirm=True):
        self.launch_calls.append((status, parent, confirm))
        return True

    def focus_update_section(self):
        self.focus_calls += 1


class AutoUpdateHarness(main.MainWindow):
    def __init__(self):
        QMainWindow.__init__(self)
        self.page_settings = FakeSettingsPage()
        self.page_nukedash = SimpleNamespace()
        self.tabs = QTabWidget()
        self.other_page = QWidget()
        self.tabs.addTab(self.other_page, "Other")
        self.tabs.addTab(self.page_settings, "Settings")
        self.setCentralWidget(self.tabs)
        self._settings_manager = mock.Mock()
        self._settings_manager.get.return_value = False
        self._automatic_update_check_started = False
        self._automatic_update_check_finished = False
        self._automatic_update_process = None
        self._automatic_update_initial_status = None

    def showEvent(self, event):
        QMainWindow.showEvent(self, event)


def update_status(*, has_update=False, can_update=False, can_check=True):
    return app_update.UpdateStatus(
        supported=True,
        can_check=can_check,
        can_update=can_update,
        has_update=has_update,
        is_dirty=False,
        branch="main",
        current_branch="main",
        current_version="1.0.0",
        current_commit="current",
        current_display="1.0.0 (current)",
        status_message="Update available." if has_update else "Already current.",
        remote_version="2.0.0",
        remote_commit="remote",
        remote_display="2.0.0 (remote)",
        changelog_preview="2.0.0\n- Automatic updates",
    )


class MainAutoUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_safe_prompt_contains_versions_changelog_and_both_actions(self):
        status = update_status(has_update=True, can_update=True)
        FakeMessageBox.next_click = "Update Now"

        with mock.patch.object(main, "QMessageBox", FakeMessageBox):
            action = main._show_automatic_update_dialog(None, status)

        dialog = FakeMessageBox.last_instance
        self.assertEqual(action, "update")
        self.assertEqual(
            [button.text for button in dialog.buttons],
            ["Update Now", "Remind Later"],
        )
        self.assertIn(status.current_display, dialog.text)
        self.assertIn(status.remote_display, dialog.text)
        self.assertIn(status.changelog_preview, dialog.informative_text)
        self.assertEqual(dialog.default_button.text, "Remind Later")
        self.assertEqual(dialog.escape_button.text, "Remind Later")

    def test_blocked_prompt_offers_settings_and_remind_later(self):
        status = update_status(has_update=True, can_update=False)
        status.status_message = "Local changes must be cleaned up first."
        FakeMessageBox.next_click = "Open Settings"

        with mock.patch.object(main, "QMessageBox", FakeMessageBox):
            action = main._show_automatic_update_dialog(None, status)

        dialog = FakeMessageBox.last_instance
        self.assertEqual(action, "settings")
        self.assertEqual(
            [button.text for button in dialog.buttons],
            ["Open Settings", "Remind Later"],
        )
        self.assertIn(status.status_message, dialog.informative_text)

    def test_checker_fetches_once_asynchronously_and_applies_result(self):
        window = AutoUpdateHarness()
        process = FakeProcess()
        initial = update_status()
        checked = update_status()
        try:
            with (
                mock.patch.object(main.app_update, "inspect_install", return_value=initial),
                mock.patch.object(main, "QProcess", return_value=process),
                mock.patch.object(
                    main.app_update,
                    "check_for_updates",
                    return_value=checked,
                ) as check,
            ):
                window._start_automatic_update_check()
                window._start_automatic_update_check()
                self.assertEqual(process.start_calls, [("git", ["fetch", "origin", "main"])])
                self.assertEqual(window.page_settings.in_progress_calls, 1)
                self.assertEqual(check.call_count, 0)

                process.process_state = QProcess.ProcessState.NotRunning
                process.finished.emit(0, QProcess.ExitStatus.NormalExit)

            check.assert_called_once_with(fetch_remote=False)
            self.assertEqual(window.page_settings.applied_statuses, [checked])
            self.assertTrue(process.deleted)
            self.assertIsNone(window._automatic_update_process)
        finally:
            window.deleteLater()
            self.app.processEvents()

    def test_failed_fetch_updates_settings_without_prompt(self):
        window = AutoUpdateHarness()
        process = FakeProcess()
        process.stderr = b"network unavailable"
        try:
            with (
                mock.patch.object(
                    main.app_update, "inspect_install", return_value=update_status()
                ),
                mock.patch.object(main, "QProcess", return_value=process),
                mock.patch.object(window, "_prompt_for_automatic_update") as prompt,
            ):
                window._start_automatic_update_check()
                process.process_state = QProcess.ProcessState.NotRunning
                process.finished.emit(1, QProcess.ExitStatus.NormalExit)

            applied = window.page_settings.applied_statuses[-1]
            self.assertEqual(applied.status_message, "network unavailable")
            self.assertFalse(applied.has_update)
            prompt.assert_not_called()
        finally:
            window.deleteLater()
            self.app.processEvents()

    def test_update_now_uses_confirmed_launch_path(self):
        window = AutoUpdateHarness()
        status = update_status(has_update=True, can_update=True)
        window.show()
        self.app.processEvents()
        try:
            with mock.patch.object(
                main, "_show_automatic_update_dialog", return_value="update"
            ):
                window._prompt_for_automatic_update(status)

            self.assertEqual(
                window.page_settings.launch_calls,
                [(status, window, False)],
            )
        finally:
            window.hide()
            window.deleteLater()
            self.app.processEvents()

    def test_remind_later_takes_no_action(self):
        window = AutoUpdateHarness()
        status = update_status(has_update=True, can_update=True)
        window.show()
        self.app.processEvents()
        try:
            with mock.patch.object(
                main, "_show_automatic_update_dialog", return_value="later"
            ):
                window._prompt_for_automatic_update(status)

            self.assertEqual(window.page_settings.launch_calls, [])
            self.assertIs(window.tabs.currentWidget(), window.other_page)
        finally:
            window.hide()
            window.deleteLater()
            self.app.processEvents()

    def test_blocked_update_can_open_and_focus_settings(self):
        window = AutoUpdateHarness()
        status = update_status(has_update=True, can_update=False)
        window.show()
        self.app.processEvents()
        try:
            with (
                mock.patch.object(
                    main, "_show_automatic_update_dialog", return_value="settings"
                ),
                mock.patch.object(
                    main.QTimer,
                    "singleShot",
                    side_effect=lambda _delay, callback: callback(),
                ),
            ):
                window._prompt_for_automatic_update(status)

            self.assertIs(window.tabs.currentWidget(), window.page_settings)
            self.assertEqual(window.page_settings.focus_calls, 1)
            self.assertEqual(window.page_settings.launch_calls, [])
        finally:
            window.hide()
            window.deleteLater()
            self.app.processEvents()

    def test_close_terminates_and_then_kills_a_stuck_fetch(self):
        window = AutoUpdateHarness()
        process = FakeProcess()
        process.process_state = QProcess.ProcessState.Running
        process.wait_results = [False, True]
        window._automatic_update_process = process

        main.MainWindow.closeEvent(window, QCloseEvent())

        self.assertTrue(window._automatic_update_check_finished)
        self.assertTrue(process.blocked)
        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        window.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
