from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication, QGroupBox, QLabel, QPushButton  # noqa: E402

import toolbox_page  # noqa: E402


class ToolboxPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_resolve_connection_uses_bundled_scripting_loader(self):
        resolve = object()
        scripting_module = SimpleNamespace(
            scriptapp=mock.Mock(return_value=resolve)
        )

        with mock.patch.dict(
            sys.modules, {"DaVinciResolveScript": scripting_module}
        ):
            connected, error = toolbox_page._connect_to_resolve()

        scripting_module.scriptapp.assert_called_once_with("Resolve")
        self.assertIs(connected, resolve)
        self.assertIsNone(error)

    def test_page_displays_copy_speed_ramps_as_first_tool_with_guide(self):
        page = toolbox_page.ToolboxPage()
        try:
            title = page.findChild(QLabel, "toolbox_title")
            description = page.findChild(QLabel, "toolbox_description")
            group = page.findChild(QGroupBox, "copy_speed_ramps_group")
            button = page.findChild(QPushButton, "copy_speed_ramps_button")
            guide = page.findChild(QLabel, "copy_speed_ramps_guide")
            status = page.findChild(QLabel, "copy_speed_ramps_status")

            self.assertEqual(title.text(), "Toolbox")
            self.assertIn("Project setup", description.text())
            self.assertEqual(group.title(), "Copy Speed Ramps Up")
            self.assertIs(group.findChildren(QPushButton)[0], button)
            self.assertEqual(button.text(), "Copy Speed Ramps Up")
            self.assertTrue(button.isEnabled())
            self.assertIn("lower video layer", guide.text())
            self.assertIn("directly above", guide.text())
            self.assertIn("Align the visible starts", guide.text())
            self.assertIn("original timeline is left unchanged", guide.text())
            self.assertEqual(status.text(), "Ready.")
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_successful_action_connects_to_resolve_and_displays_result(self):
        page = toolbox_page.ToolboxPage()
        resolve = object()
        result = {
            "success": True,
            "message": "Created timeline 'Edit - matched speed ramps' and copied 2 speed-ramp pairs.",
            "timeline_name": "Edit - matched speed ramps",
            "copied_pairs": [],
            "warnings": [],
        }

        def run_action(received_resolve):
            self.assertIs(received_resolve, resolve)
            self.assertFalse(page.copy_speed_ramps_button.isEnabled())
            self.assertEqual(page.copy_speed_ramps_button.text(), "Running...")
            self.assertEqual(
                page.copy_speed_ramps_status.text(), "Copying speed ramps..."
            )
            return result

        try:
            with (
                mock.patch.object(
                    toolbox_page, "_connect_to_resolve", return_value=(resolve, None)
                ) as connect,
                mock.patch.object(
                    toolbox_page.copy_speed_ramps_up, "run", side_effect=run_action
                ) as run,
                mock.patch.object(toolbox_page.QMessageBox, "information") as information,
            ):
                page.copy_speed_ramps_button.click()

            connect.assert_called_once_with()
            run.assert_called_once_with(resolve)
            information.assert_called_once_with(
                page, "Copy Speed Ramps Up", result["message"]
            )
            self.assertEqual(page.copy_speed_ramps_status.text(), result["message"])
            self.assertEqual(page.copy_speed_ramps_button.text(), "Copy Speed Ramps Up")
            self.assertTrue(page.copy_speed_ramps_button.isEnabled())
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_successful_action_appends_warnings_to_result_dialog(self):
        page = toolbox_page.ToolboxPage()
        result = {
            "success": True,
            "message": "Created a new timeline.",
            "warnings": ["Could not open it automatically.", "Project was not saved."],
        }
        try:
            with (
                mock.patch.object(
                    toolbox_page, "_connect_to_resolve", return_value=(object(), None)
                ),
                mock.patch.object(
                    toolbox_page.copy_speed_ramps_up, "run", return_value=result
                ),
                mock.patch.object(toolbox_page.QMessageBox, "information") as information,
            ):
                page.copy_speed_ramps_button.click()

            dialog_text = information.call_args.args[2]
            self.assertIn(result["message"], dialog_text)
            self.assertIn("Warnings:", dialog_text)
            self.assertIn("Could not open it automatically.", dialog_text)
            self.assertIn("Project was not saved.", dialog_text)
            self.assertEqual(page.copy_speed_ramps_status.text(), dialog_text)
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_connection_failure_shows_error_without_running_action(self):
        page = toolbox_page.ToolboxPage()
        error = "Could not connect to DaVinci Resolve. Is Resolve open?"
        try:
            with (
                mock.patch.object(
                    toolbox_page, "_connect_to_resolve", return_value=(None, error)
                ),
                mock.patch.object(toolbox_page.copy_speed_ramps_up, "run") as run,
                mock.patch.object(toolbox_page.QMessageBox, "critical") as critical,
            ):
                page.copy_speed_ramps_button.click()

            run.assert_not_called()
            critical.assert_called_once_with(page, "Copy Speed Ramps Up", error)
            self.assertEqual(page.copy_speed_ramps_status.text(), error)
            self.assertTrue(page.copy_speed_ramps_button.isEnabled())
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_action_failure_updates_status_and_shows_error(self):
        page = toolbox_page.ToolboxPage()
        result = {
            "success": False,
            "message": "No aligned clip directly above a retimed clip was found.",
            "warnings": [],
        }
        try:
            with (
                mock.patch.object(
                    toolbox_page, "_connect_to_resolve", return_value=(object(), None)
                ),
                mock.patch.object(
                    toolbox_page.copy_speed_ramps_up, "run", return_value=result
                ),
                mock.patch.object(toolbox_page.QMessageBox, "critical") as critical,
            ):
                page.copy_speed_ramps_button.click()

            critical.assert_called_once_with(
                page, "Copy Speed Ramps Up", result["message"]
            )
            self.assertEqual(page.copy_speed_ramps_status.text(), result["message"])
            self.assertTrue(page.copy_speed_ramps_button.isEnabled())
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_action_exception_restores_button_and_shows_error(self):
        page = toolbox_page.ToolboxPage()
        try:
            with (
                mock.patch.object(
                    toolbox_page, "_connect_to_resolve", return_value=(object(), None)
                ),
                mock.patch.object(
                    toolbox_page.copy_speed_ramps_up,
                    "run",
                    side_effect=RuntimeError("unexpected"),
                ),
                mock.patch.object(toolbox_page.QMessageBox, "critical") as critical,
            ):
                page.copy_speed_ramps_button.click()

            self.assertIn("unexpected", page.copy_speed_ramps_status.text())
            self.assertEqual(page.copy_speed_ramps_button.text(), "Copy Speed Ramps Up")
            self.assertTrue(page.copy_speed_ramps_button.isEnabled())
            critical.assert_called_once()
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
