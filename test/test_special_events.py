import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from datetime import timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QFrame
from app_cache import cache_directory
import settings
import themes
from special_events import (ACTIVATION, COMPLETION_KEY, EVENT_THEME, CHRISTMAS_EVENT,
                            SpecialEventController)


class SpecialEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        themes.apply_theme(themes.DEFAULT_THEME)
        self.temp = tempfile.TemporaryDirectory(dir=cache_directory('temp'))
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'settings.yaml'
        self.manager = settings.SettingsManager(str(self.path))
        self.now = ACTIVATION - timedelta(seconds=1)
        self.controller = SpecialEventController(self.manager, clock=lambda: self.now)
        self.addCleanup(self.cleanup_controller)

    def cleanup_controller(self):
        self.controller.timer.stop()
        self.controller.deleteLater()
        self.app.processEvents()
        themes.apply_theme(themes.DEFAULT_THEME)

    def page(self):
        with (patch.object(settings.SettingsPage, '_load_django_users', lambda page: page._populate_user_combo()),
              patch.object(settings.SettingsPage, '_refresh_update_panel')):
            page = settings.SettingsPage(self.manager)
        def cleanup():
            self.controller.page = None
            page._autosave_pending = False
            page._autosave_timer.stop()
            page.close()
            page.deleteLater()
            self.app.processEvents()
        self.addCleanup(cleanup)
        self.controller.bind_settings_page(page)
        return page

    def test_activation_boundary_and_completion_persist_together(self):
        self.manager.save = Mock(wraps=self.manager.save)
        self.assertFalse(self.controller.check())
        self.manager.save.assert_not_called()
        self.now = ACTIVATION
        self.assertTrue(self.controller.check())
        self.manager.save.assert_called_once_with()
        reloaded = settings.SettingsManager(str(self.path))
        self.assertTrue(reloaded.get(COMPLETION_KEY))
        self.assertEqual(reloaded.get('theme_file'), EVENT_THEME)
        self.assertEqual(self.app._shotbox_theme, EVENT_THEME)

    def test_late_startup_applies_before_new_header_is_created(self):
        self.now = ACTIVATION + timedelta(days=20)
        self.assertTrue(self.controller.check())
        header = themes.create_theme_header()
        try:
            self.assertFalse(header.isHidden())
        finally:
            header.close()
            header.deleteLater()

    def test_timer_catches_overnight_sessions_and_continues_for_christmas(self):
        self.controller.start()
        self.assertTrue(self.controller.timer.isActive())
        self.assertEqual(self.controller.timer.interval(), 30_000)
        self.now = ACTIVATION
        self.controller.timer.timeout.emit()
        self.assertTrue(self.manager.get(COMPLETION_KEY))
        self.assertTrue(self.controller.timer.isActive())
        self.now = CHRISTMAS_EVENT.activation
        self.controller.timer.timeout.emit()
        self.assertEqual(self.manager.get('theme_file'), themes.CHRISTMAS_THEME)
        self.assertTrue(self.manager.get(CHRISTMAS_EVENT.completion_key))

    def test_christmas_switches_at_midnight_on_december_first(self):
        self.manager.set(COMPLETION_KEY, True)
        self.now = CHRISTMAS_EVENT.activation - timedelta(microseconds=1)
        self.assertFalse(self.controller.check())
        self.assertFalse(self.manager.get(CHRISTMAS_EVENT.completion_key))
        self.now = CHRISTMAS_EVENT.activation
        self.assertTrue(self.controller.check())
        self.assertEqual(self.app._shotbox_theme, themes.CHRISTMAS_THEME)
        self.assertTrue(settings.SettingsManager(str(self.path)).get(CHRISTMAS_EVENT.completion_key))

    def test_late_first_launch_uses_christmas_without_flashing_gta(self):
        self.now = CHRISTMAS_EVENT.activation + timedelta(days=4)
        self.controller.theme_applier = Mock(wraps=self.controller.theme_applier)
        self.manager.save = Mock(wraps=self.manager.save)
        self.assertTrue(self.controller.check())
        self.controller.theme_applier.assert_called_once_with(themes.CHRISTMAS_THEME)
        self.manager.save.assert_called_once()
        reloaded = settings.SettingsManager(str(self.path))
        self.assertTrue(reloaded.get(COMPLETION_KEY))
        self.assertTrue(reloaded.get(CHRISTMAS_EVENT.completion_key))

    def test_christmas_override_and_preference_reset_do_not_reactivate(self):
        self.now = CHRISTMAS_EVENT.activation
        self.controller.check()
        self.manager.reset_to_defaults()
        themes.apply_theme(themes.DEFAULT_THEME)
        self.assertTrue(self.manager.get(COMPLETION_KEY))
        self.assertTrue(self.manager.get(CHRISTMAS_EVENT.completion_key))
        self.assertFalse(self.controller.check())
        self.assertEqual(self.manager.get('theme_file'), themes.DEFAULT_THEME)

    def test_christmas_failure_restores_both_pending_markers_and_retries(self):
        self.now = CHRISTMAS_EVENT.activation
        with patch.object(self.manager, 'save', return_value=False):
            self.assertFalse(self.controller.check())
        self.assertFalse(self.manager.get(COMPLETION_KEY))
        self.assertFalse(self.manager.get(CHRISTMAS_EVENT.completion_key))
        self.assertEqual(self.app._shotbox_theme, themes.DEFAULT_THEME)
        self.assertTrue(self.controller.check())

    def test_missing_christmas_pack_keeps_gta_until_retry(self):
        self.now = ACTIVATION
        self.controller.check()
        self.now = CHRISTMAS_EVENT.activation
        def missing(filename):
            raise FileNotFoundError(filename)
        with patch.object(self.controller, 'theme_applier', side_effect=missing):
            self.assertFalse(self.controller.check())
        self.assertEqual(self.app._shotbox_theme, EVENT_THEME)
        self.assertTrue(self.manager.get(COMPLETION_KEY))
        self.assertFalse(self.manager.get(CHRISTMAS_EVENT.completion_key))
        self.assertTrue(self.controller.check())

    def test_party_countdown_rolls_over_without_reapplying_theme(self):
        from PyQt6.QtWidgets import QLabel
        page = self.page()
        header = themes.create_theme_header()
        self.addCleanup(header.deleteLater)
        self.now = CHRISTMAS_EVENT.activation
        self.controller.start()
        title = header.findChild(QLabel, 'christmas_party_countdown')
        self.assertEqual(title.text(), '20 DAYS TO THE PARTY')
        self.controller.theme_applier = Mock()
        self.now += timedelta(days=19)
        self.controller.timer.timeout.emit()
        self.assertEqual(title.text(), '1 DAY TO THE PARTY')
        self.now += timedelta(days=1)
        self.app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        self.assertEqual(title.text(), "IT'S PARTY DAY!")
        self.now += timedelta(days=1)
        self.controller.timer.timeout.emit()
        self.assertEqual(title.text(), 'MERRY CHRISTMAS!')
        self.controller.theme_applier.assert_not_called()

    def test_christmas_countdown_uses_the_same_winter_date_for_all_timezones(self):
        party_midnight = CHRISTMAS_EVENT.activation + timedelta(days=20)
        other_zone = party_midnight.astimezone(timezone(timedelta(hours=-8)))
        self.assertEqual(themes.christmas_party_countdown(other_zone), "IT'S PARTY DAY!")

    def test_foreground_resume_triggers_with_non_uk_clock_offset(self):
        self.controller.start()
        self.now = ACTIVATION.astimezone(timezone(timedelta(hours=-5)))
        self.app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
        self.assertTrue(self.manager.get(COMPLETION_KEY))

    def test_manual_override_relaunch_and_reset_do_not_repeat(self):
        self.now = ACTIVATION
        self.controller.check()
        self.manager.set('theme_file', themes.DEFAULT_THEME)
        themes.apply_theme(themes.DEFAULT_THEME)
        self.assertFalse(self.controller.check())
        reloaded = settings.SettingsManager(str(self.path))
        reloaded.reset_to_defaults()
        self.assertTrue(reloaded.get(COMPLETION_KEY))
        new_controller = SpecialEventController(reloaded, clock=lambda: self.now)
        try:
            self.assertFalse(new_controller.check())
            self.assertEqual(reloaded.get('theme_file'), themes.DEFAULT_THEME)
            self.assertEqual(self.app._shotbox_theme, themes.DEFAULT_THEME)
        finally:
            new_controller.deleteLater()

    def test_completion_does_not_leak_into_new_users_defaults(self):
        self.now = ACTIVATION
        self.controller.check()
        fresh = settings.SettingsManager(str(Path(self.temp.name) / 'other-user.yaml'))
        self.assertFalse(fresh.get(COMPLETION_KEY))
        self.assertFalse(settings.DEFAULT_SETTINGS['special_events']['gta6_2026_11_18_applied'])

    def test_runtime_flushes_edits_and_syncs_combo_without_new_autosave(self):
        page = self.page()
        page.preview_output_subdir_edit.setText('renders/event-test')
        snapshots = []
        save = self.manager.save
        def record_save():
            snapshots.append((self.manager.get('theme_file'), self.manager.get(COMPLETION_KEY)))
            return save()
        self.manager.save = record_save
        changes = []
        page.theme_combo.currentIndexChanged.connect(changes.append)
        self.now = ACTIVATION
        self.assertTrue(self.controller.check())
        self.assertEqual(snapshots, [(themes.DEFAULT_THEME, False), (EVENT_THEME, True)])
        reloaded = settings.SettingsManager(str(self.path))
        self.assertEqual(reloaded.get('preview_output_subdir'), 'renders/event-test')
        self.assertEqual(page.theme_combo.currentData(), EVENT_THEME)
        self.assertFalse(page._autosave_pending)
        self.assertFalse(page._autosave_timer.isActive())
        self.assertFalse(changes)

    def test_failed_pending_autosave_defers_event(self):
        page = self.page()
        page.preview_output_subdir_edit.setText('pending-edit')
        self.controller.theme_applier = Mock()
        self.now = ACTIVATION
        with patch.object(self.manager, 'save', return_value=False):
            self.assertFalse(self.controller.check())
        self.controller.theme_applier.assert_not_called()
        self.assertTrue(page._autosave_pending)
        self.assertFalse(self.manager.get(COMPLETION_KEY))

    def test_save_failure_restores_live_theme_marker_and_dropdown_then_retries(self):
        page = self.page()
        frame = QFrame()
        self.addCleanup(frame.close)
        source = 'background: #1A1D23; color: #D4D4D4;'
        themes.set_widget_style(frame, source)
        self.now = ACTIVATION
        with patch.object(self.manager, 'save', return_value=False):
            self.assertFalse(self.controller.check())
        self.assertFalse(self.manager.get(COMPLETION_KEY))
        self.assertEqual(self.manager.get('theme_file'), themes.DEFAULT_THEME)
        self.assertEqual(self.app._shotbox_theme, themes.DEFAULT_THEME)
        self.assertEqual(page.theme_combo.currentData(), themes.DEFAULT_THEME)
        self.assertEqual(frame.styleSheet(), source)
        self.assertTrue(self.controller.check())
        self.assertIn('#171126', frame.styleSheet())

    def test_missing_pack_keeps_previous_theme_and_retries_when_available(self):
        self.now = ACTIVATION
        ui_dir = Path(self.temp.name) / 'ui'
        ui_dir.mkdir()
        (ui_dir / themes.DEFAULT_THEME).write_text(
            (themes.UI_DIR / themes.DEFAULT_THEME).read_text(encoding='utf-8'), encoding='utf-8')
        with patch.object(themes, 'UI_DIR', ui_dir):
            self.assertFalse(self.controller.check())
            (ui_dir / EVENT_THEME).write_bytes(b'\xff\xfe')
            self.assertFalse(self.controller.check())
        self.assertFalse(self.manager.get(COMPLETION_KEY))
        self.assertEqual(self.app._shotbox_theme, themes.DEFAULT_THEME)
        self.assertTrue(self.controller.check())

    def test_settings_does_not_announce_scheduled_events(self):
        from PyQt6.QtWidgets import QLabel
        page = self.page()
        self.assertFalse(hasattr(page, 'special_event_status'))
        for now in (ACTIVATION - timedelta(days=1), ACTIVATION,
                    CHRISTMAS_EVENT.activation):
            self.now = now
            self.controller.check()
            labels = [label.text() for label in page.findChildren(QLabel)]
            self.assertFalse(any('Special Event' in text or 'Upcoming one-time' in text
                                 or '11:00 PM UK time' in text for text in labels))
