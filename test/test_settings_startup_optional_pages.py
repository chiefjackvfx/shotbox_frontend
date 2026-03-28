from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PYQT_FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(PYQT_FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(PYQT_FRONTEND_DIR))

from PyQt6.QtWidgets import QApplication

import settings


class InMemorySettingsManager:
    def __init__(self, initial: dict | None = None):
        self._settings = copy.deepcopy(settings.DEFAULT_SETTINGS)
        if initial:
            self._settings.update(initial)
        self.settings_path = "/tmp/test_settings.yaml"

    def get(self, key: str, default=None):
        keys = key.split(".")
        value = self._settings
        try:
            for part in keys:
                value = value[part]
            return value
        except (KeyError, TypeError):
            return default

    def set(self, key: str, value, save: bool = True):
        keys = key.split(".")
        target = self._settings
        for part in keys[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[keys[-1]] = value

    def save(self):
        return True

    def get_polling_interval_ms(self):
        return int(self.get("polling_interval", 5) * 1000)


class SettingsStartupOptionalPagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_startup_optional_page_checkboxes_load_and_save(self):
        manager = InMemorySettingsManager(
            {
                "enable_assignment_board": True,
                "enable_review_page": False,
                "enable_activity_page": True,
                "enable_import_page": False,
                "enable_xml_import_page": True,
            }
        )

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertTrue(page.enable_assignment_board_check.isChecked())
            self.assertFalse(page.enable_review_page_check.isChecked())
            self.assertTrue(page.enable_activity_page_check.isChecked())
            self.assertFalse(page.enable_import_page_check.isChecked())
            self.assertTrue(page.enable_xml_import_page_check.isChecked())

            page.enable_assignment_board_check.setChecked(False)
            page.enable_review_page_check.setChecked(True)
            page.enable_activity_page_check.setChecked(False)
            page.enable_import_page_check.setChecked(True)
            page.enable_xml_import_page_check.setChecked(False)

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertFalse(manager.get("enable_assignment_board"))
            self.assertTrue(manager.get("enable_review_page"))
            self.assertFalse(manager.get("enable_activity_page"))
            self.assertTrue(manager.get("enable_import_page"))
            self.assertFalse(manager.get("enable_xml_import_page"))
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_project_load_profiler_debug_checkbox_loads_and_saves(self):
        manager = InMemorySettingsManager(
            {
                "debug_modes": {
                    "general": False,
                    "api_calls": False,
                    "ui_updates": False,
                    "notifications": False,
                    "project_load_profiler": True,
                    "suppress_qt_multimedia_warnings": False,
                }
            }
        )

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            self.assertTrue(page.debug_project_load_profiler_check.isChecked())

            page.debug_project_load_profiler_check.setChecked(False)

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertFalse(manager.get("debug_modes.project_load_profiler"))
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()

    def test_preview_size_combo_supports_nothumb_and_saves(self):
        manager = InMemorySettingsManager({"preview_thumbnail_size": "NoThumb"})

        with mock.patch.object(settings.SettingsPage, "_load_django_users", lambda self: None), \
            mock.patch.object(settings.SettingsPage, "_refresh_update_panel", lambda self: None), \
            mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
            page = settings.SettingsPage(settings_manager=manager)

        try:
            combo_items = [
                page.preview_size_combo.itemText(i)
                for i in range(page.preview_size_combo.count())
            ]
            self.assertIn("NoThumb", combo_items)
            self.assertEqual(page.preview_size_combo.currentText(), "NoThumb")

            page.preview_size_combo.setCurrentText("Small")

            with mock.patch.object(settings.QMessageBox, "information", lambda *args, **kwargs: None):
                page._save_all_settings()

            self.assertEqual(manager.get("preview_thumbnail_size"), "Small")
        finally:
            page.close()
            page.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
