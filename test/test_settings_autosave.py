from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox
import settings


class SettingsAutosaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.yaml"
        self.manager = settings.SettingsManager(str(self.path))
        self.manager.save = Mock(wraps=self.manager.save)
        self.information = patch.object(QMessageBox, "information").start()
        self.warning = patch.object(QMessageBox, "warning").start()
        self.addCleanup(patch.stopall)
        with (patch.object(settings.SettingsPage, "_load_django_users", lambda page: page._populate_user_combo()),
              patch.object(settings.SettingsPage, "_refresh_update_panel")):
            self.page = settings.SettingsPage(self.manager)
        self.addCleanup(self.cleanup_page)

    def cleanup_page(self):
        self.page._autosave_pending = False
        self.page._autosave_timer.stop()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def read_saved(self, key):
        return settings.SettingsManager(str(self.path)).get(key)

    def test_initial_load_and_changelog_browsing_do_not_save(self):
        self.page.change_log_combo.setCurrentIndex(1)
        self.page._load_current_values()
        QTest.qWait(600)
        self.manager.save.assert_not_called()
        self.assertFalse(self.page._autosave_pending)

    def test_edits_are_debounced_persisted_and_quiet(self):
        emitted = []
        self.page.settings_changed.connect(lambda *args: emitted.append(args))
        server = Mock()
        self.page.server_url_changed.connect(server)
        self.page.preview_output_subdir_edit.setText("first")
        self.page.preview_output_subdir_edit.setText("final")
        self.page.quick_view_size_spin.setValue(44)
        self.page.enable_review_page_check.setChecked(False)
        self.page.notif_silent_radio.setChecked(True)
        self.page.preview_size_combo.setCurrentText("Small")
        self.manager.save.assert_not_called()
        QTest.qWait(650)
        self.manager.save.assert_called_once()
        self.assertEqual(self.read_saved("preview_output_subdir"), "final")
        self.assertEqual(self.read_saved("quick_view_screen_percentage"), 44)
        self.assertFalse(self.read_saved("enable_review_page"))
        self.assertEqual(self.read_saved("notifications"), "silent")
        self.assertEqual(self.read_saved("preview_thumbnail_size"), "Small")
        self.assertIn(("quick_view_screen_percentage", 44), emitted)
        self.assertFalse(any(key == "always_on_top" for key, _ in emitted))
        server.assert_not_called()
        self.information.assert_not_called()
        self.warning.assert_not_called()

    def test_leaving_page_flushes_pending_text(self):
        self.page.show()
        self.page.server_url_edit.setText("http://new-server:8000")
        self.page.hide()
        self.assertEqual(self.read_saved("server_url"), "http://new-server:8000")
        self.assertFalse(self.page._autosave_timer.isActive())

    def test_quitting_flushes_pending_changes(self):
        self.page.preview_output_subdir_edit.setText("on-exit")
        self.app.aboutToQuit.emit()
        self.assertEqual(self.read_saved("preview_output_subdir"), "on-exit")

    def test_failed_autosave_is_visible_and_retry_notifies(self):
        self.manager.save.return_value = False
        self.page.quick_view_size_spin.setValue(47)
        emitted = []
        self.page.settings_changed.connect(lambda *args: emitted.append(args))
        self.page._flush_autosave()
        self.assertTrue(self.page._autosave_pending)
        self.assertIn("Save failed", self.page.save_status_label.text())
        self.assertEqual(emitted, [])
        self.warning.assert_not_called()
        self.manager.save.return_value = True
        self.page._flush_autosave()
        self.assertIn(("quick_view_screen_percentage", 47), emitted)
        self.assertFalse(self.page._autosave_pending)

    def test_manual_save_cancels_pending_timer(self):
        self.page.preview_output_subdir_edit.setText("manual")
        self.page.save_button.click()
        QTest.qWait(600)
        self.manager.save.assert_called_once()
        self.assertEqual(self.read_saved("preview_output_subdir"), "manual")

    def test_user_repopulation_preserves_unavailable_link_without_saving(self):
        self.manager.set("django_username", 123)
        self.page._populate_user_combo()
        self.assertEqual(self.page.django_user_combo.currentData(), 123)
        self.assertFalse(self.page._autosave_pending)
        self.manager.save.assert_not_called()

    def test_reset_cancels_queued_changes_without_saving_partial_form(self):
        self.manager.set("django_username", 123)
        self.page._populate_user_combo()
        self.page.preview_output_subdir_edit.setText("discard-me")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.page._reset_to_defaults()
        QTest.qWait(600)
        self.manager.save.assert_not_called()
        self.assertEqual(self.read_saved("preview_output_subdir"), settings.DEFAULT_SETTINGS["preview_output_subdir"])
        self.assertIsNone(self.page.django_user_combo.currentData())
        self.assertFalse(self.page._autosave_pending)


if __name__ == "__main__":
    unittest.main()
