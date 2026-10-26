from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
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

    def test_clear_cache_button_removes_files_without_saving_settings(self):
        import app_cache
        from exr_thumbnails import EXRThumbnailLoader
        from image_loader import ImageLoader

        root = Path(self.temp.name) / ".cache"
        with patch.object(app_cache, "CACHE_ROOT", root), \
             patch.object(EXRThumbnailLoader, "_instance", None), \
             patch.object(ImageLoader, "_instance", None):
            (app_cache.cache_directory("exr-previews-v1") / "frame.jpg").touch()
            self.page.clear_cache_btn.click()
            self.assertFalse(self.page.clear_cache_btn.isEnabled())
            deadline = time.monotonic() + 3
            while self.page._cache_clear_task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIsNone(self.page._cache_clear_task)
            self.assertTrue(self.page.clear_cache_btn.isEnabled())
            self.assertEqual(list(root.iterdir()), [])
            self.assertEqual(self.page.cache_status_label.text(), "Cache cleared.")
            self.manager.save.assert_not_called()

    def test_cache_clear_error_is_visible_and_button_can_retry(self):
        from exr_thumbnails import EXRThumbnailLoader
        from image_loader import ImageLoader

        with patch.object(settings, "clear_cache_files", side_effect=PermissionError("locked")), \
             patch.object(EXRThumbnailLoader, "_instance", None), \
             patch.object(ImageLoader, "_instance", None):
            self.page.clear_cache_btn.click()
            deadline = time.monotonic() + 3
            while self.page._cache_clear_task is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertTrue(self.page.clear_cache_btn.isEnabled())
            self.assertIn("could not be cleared", self.page.cache_status_label.text())
            self.assertIn("locked", self.page.cache_status_label.toolTip())

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

    def test_nuke_lock_release_settings_autosave_and_reload(self):
        self.assertTrue(self.page.nuke_lock_auto_release_check.isChecked())
        self.assertEqual(self.page.nuke_lock_release_grace_spin.value(), 90)
        emitted = []
        self.page.settings_changed.connect(lambda *args: emitted.append(args))
        self.page.nuke_lock_release_grace_spin.setValue(180)
        self.page.nuke_lock_auto_release_check.setChecked(False)
        self.page._flush_autosave()
        self.assertEqual(self.read_saved("nuke_lock_release_grace_sec"), 180)
        self.assertFalse(self.read_saved("nuke_lock_auto_release"))
        self.assertFalse(self.page.nuke_lock_release_grace_spin.isEnabled())
        self.assertIn(("nuke_lock_release_grace_sec", 180), emitted)
        self.assertIn(("nuke_lock_auto_release", False), emitted)
        self.page._load_current_values()
        self.assertEqual(self.page.nuke_lock_release_grace_spin.value(), 180)
        self.page.nuke_lock_auto_release_check.setChecked(True)
        self.page._flush_autosave()
        self.assertTrue(self.read_saved("nuke_lock_auto_release"))
        self.assertTrue(self.page.nuke_lock_release_grace_spin.isEnabled())

    def test_nuke_detector_debug_autosaves_reloads_and_can_be_disabled(self):
        self.assertFalse(self.page.debug_nuke_detector_check.isChecked())
        emitted = []
        self.page.settings_changed.connect(lambda *args: emitted.append(args))
        self.page.debug_nuke_detector_check.setChecked(True)
        self.page._flush_autosave()
        self.assertTrue(self.read_saved("debug_modes.nuke_detector"))
        self.assertIn(("debug_modes.nuke_detector", True), emitted)
        self.page._load_current_values()
        self.assertTrue(self.page.debug_nuke_detector_check.isChecked())
        self.page.debug_nuke_detector_check.setChecked(False)
        self.page._flush_autosave()
        self.assertFalse(self.read_saved("debug_modes.nuke_detector"))
        self.assertIn(("debug_modes.nuke_detector", False), emitted)

    def test_nuke_detector_interval_autosaves_and_reloads(self):
        self.assertEqual(self.page.nuke_detector_interval_spin.value(), 45)
        emitted = []
        self.page.settings_changed.connect(lambda *args: emitted.append(args))
        self.page.nuke_detector_interval_spin.setValue(5)
        self.page._flush_autosave()
        self.assertEqual(self.read_saved("nuke_detector_interval_sec"), 5)
        self.assertIn(("nuke_detector_interval_sec", 5), emitted)
        self.page._load_current_values()
        self.assertEqual(self.page.nuke_detector_interval_spin.value(), 5)

    def test_manual_lock_buttons_request_previews_without_saving_settings(self):
        previews, clears = [], []
        self.page.nuke_locks_preview_requested.connect(previews.append)
        self.page.nuke_locks_clear_requested.connect(clears.append)
        self.page.clear_stale_nuke_locks_btn.click()
        self.assertEqual(previews, [True])
        self.assertFalse(self.page.clear_all_nuke_locks_btn.isEnabled())
        self.page.review_nuke_lock_clear_preview({"stale_only": True, "shots": []})
        self.page.clear_all_nuke_locks_btn.click()
        self.assertEqual(previews, [True, False])
        self.assertEqual(clears, [])
        self.manager.save.assert_not_called()
        self.assertFalse(self.page._autosave_pending)

    def test_manual_lock_preview_shows_affected_shots_before_clear(self):
        clears = []
        self.page.nuke_locks_clear_requested.connect(clears.append)
        for stale_only in (True, False):
            with self.subTest(stale_only=stale_only):
                preview = {"stale_only": stale_only, "shots": [
                    {"shot_id": 7, "title": "bam030", "owner_id": "artist@desk"},
                ]}
                dialog = Mock()
                dialog.exec.return_value = QMessageBox.StandardButton.Yes
                with patch.object(settings, "QMessageBox", return_value=dialog) as box:
                    box.Icon = QMessageBox.Icon
                    box.StandardButton = QMessageBox.StandardButton
                    self.page.review_nuke_lock_clear_preview(preview)
                dialog.setDetailedText.assert_called_once_with("bam030 — artist@desk")
                dialog.setDefaultButton.assert_called_once_with(QMessageBox.StandardButton.Cancel)
                if not stale_only:
                    self.assertIn("active locks", dialog.setInformativeText.call_args.args[0])
                self.assertEqual(clears[-1], preview)

    def test_manual_lock_preview_cancellation_reenables_buttons_without_clear(self):
        clears = []
        self.page.nuke_locks_clear_requested.connect(clears.append)
        self.page._request_nuke_lock_clear_preview(False)
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Cancel):
            self.page.review_nuke_lock_clear_preview({"stale_only": False, "shots": [
                {"shot_id": 7, "title": "bam030", "owner_id": "artist@desk"},
            ]})
        self.assertEqual(clears, [])
        self.assertTrue(self.page.clear_stale_nuke_locks_btn.isEnabled())
        self.assertTrue(self.page.clear_all_nuke_locks_btn.isEnabled())
        self.assertIn("cancelled", self.page.nuke_lock_cleanup_status.text())

    def test_manual_lock_empty_or_failed_preview_reenables_buttons(self):
        for error in (None, "Server unavailable"):
            with self.subTest(error=error):
                self.page._request_nuke_lock_clear_preview(True)
                self.page.review_nuke_lock_clear_preview({"stale_only": True, "shots": [], "error": error})
                self.assertTrue(self.page.clear_stale_nuke_locks_btn.isEnabled())
                self.assertTrue(self.page.clear_all_nuke_locks_btn.isEnabled())
                if error:
                    self.warning.assert_called_once()
                    self.assertEqual(self.page.nuke_lock_cleanup_status.text(), error)
                else:
                    self.warning.assert_not_called()
                    self.assertIn("No stale locks", self.page.nuke_lock_cleanup_status.text())

    def test_manual_lock_result_reports_partial_failure_and_restores_controls(self):
        self.page._request_nuke_lock_clear_preview(False)
        self.page.show_nuke_lock_clear_result({
            "cleared": [{"shot_id": 7}], "skipped": [{"shot_id": 8}],
            "failed": [{"title": "bam070", "detail": "Write unavailable"}],
        })
        self.assertTrue(self.page.clear_all_nuke_locks_btn.isEnabled())
        self.assertEqual(self.page.nuke_lock_cleanup_status.text(), "Cleared 1; skipped 1; failed 1.")
        self.assertIn("bam070: Write unavailable", self.warning.call_args.args[2])

    def test_saved_identity_is_emitted_after_server_and_unlink_is_emitted(self):
        self.page._django_users = [{"id": 1, "username": "jack"}]
        self.page._populate_user_combo()
        emitted = []
        self.page.settings_changed.connect(
            lambda key, value: emitted.append((key, value)) if key == "django_username" else None
        )
        self.page.server_url_changed.connect(lambda value: emitted.append(("server_url", value)))
        self.page.django_user_combo.setCurrentIndex(1)
        self.page.server_url_edit.setText("http://new-server:8000")
        self.page._flush_autosave()
        self.assertEqual(emitted, [("server_url", "http://new-server:8000"), ("django_username", 1)])
        self.assertEqual(self.read_saved("django_username"), 1)
        self.page.django_user_combo.setCurrentIndex(0)
        self.page._flush_autosave()
        self.assertEqual(emitted[-1], ("django_username", None))

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
