"""Regression coverage for the picker-to-dashboard handoff."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow

import http_help
from artist_picker import ProfileSplash
from main import show_after_workspace_load
from page_nukedash import page_nukedash
from settings import SettingsManager
from widgets import ShotCard


class LoginHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_optional_three_second_login_remembers_setting_and_allows_override(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        path = str(directory / "picker.ini")
        profiles = [("1", "jack", "Jack"), ("2", "cronk", "Cronk")]

        def open_picker():
            picker = ProfileSplash(profiles, QSettings(path, QSettings.Format.IniFormat))
            self.addCleanup(picker.close)
            picker.show()
            return picker

        first = open_picker()
        self.assertTrue(first.auto_login.isChecked())
        self.assertFalse(first.login_timer.isActive())
        first.settings.setValue("last_artist", "1")
        first.close()

        picker = open_picker()
        self.assertTrue(picker.auto_login.isChecked())
        picker.auto_login.click()
        self.assertFalse(picker.login_timer.isActive())
        picker.close()
        picker = open_picker()
        self.assertFalse(picker.auto_login.isChecked())
        self.assertFalse(picker.login_timer.isActive())
        picker.auto_login.click()
        picker.choose("2")
        self.assertFalse(picker.login_timer.isActive())
        self.assertEqual(picker.artist_id, "2")

        returning = open_picker()
        QTest.qWait(2500)
        self.assertIsNone(returning.artist_id)
        QTest.qWait(650)
        self.assertEqual(returning.artist_id, "1")

    def test_choice_during_preload_is_kept_for_startup(self):
        memory = MagicMock()
        memory.value.side_effect = lambda key, default, **_: "1" if key == "last_artist" else default
        picker = ProfileSplash([("1", "jack", "Jack"), ("2", "cronk", "Cronk")], memory)
        self.addCleanup(picker.close)
        picker.keep_open_after_choice = True
        picker.show()
        picker.choose("2")
        self.assertTrue(picker.isVisible())
        watchdog = QTimer(picker)
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(picker.reject)
        watchdog.start(500)
        self.assertEqual(picker.select_for_startup(), "2")
        self.assertTrue(watchdog.isActive())
        watchdog.stop()

    def test_retry_then_wait_for_restored_tasks_before_showing_dashboard(self):
        app = QApplication.instance() or QApplication([])
        directory = self.enterContext(TemporaryDirectory())
        settings = SettingsManager(str(Path(directory) / "prefs.yaml"))
        settings.artist = {"id": 1}
        memory = MagicMock()
        memory.value.side_effect = lambda key, default, **_: default
        picker = ProfileSplash([("1", "avery", "Avery Morgan")], memory)
        QTimer.singleShot(0, lambda: picker.choose("1"))
        self.assertEqual(picker.select_for_startup(), "1")
        self.assertTrue(picker.isVisible())
        settings.startup_picker = picker
        tasks = [{"id": i, "title": f"Task {i}"} for i in range(1, 22)]
        shot = {"id": 1, "title": "SH001", "tasks": tasks}
        timeline = {"id": 1, "title": "Timeline", "shots": [shot]}
        jobs = [{"title": "Job", "id": 1, "timelines": [timeline]}]
        self.enterContext(patch("page_nukedash.get_settings_manager", return_value=settings))
        self.enterContext(patch.object(http_help.DjangoAPI, "getAPI", side_effect=[ValueError("offline"), jobs]))
        self.enterContext(patch.object(http_help.DjangoAPI, "get_users", return_value=[]))
        self.enterContext(patch.object(page_nukedash, "_start_lock_maintenance_timers"))
        self.enterContext(patch("widgets.HAS_MULTIMEDIA", False))
        window = QMainWindow()
        window._settings_manager = settings
        dash = page_nukedash()
        window.setCentralWidget(dash)
        window.page_nukedash = dash
        self.addCleanup(window.close)
        self.addCleanup(dash.shutdown)
        self.addCleanup(app.setQuitOnLastWindowClosed, app.quitOnLastWindowClosed())
        updates = []
        dash.startup_progress.connect(lambda text, *_: updates.append((text, window.isVisible())))
        show_after_workspace_load(window, picker)
        for _ in range(300):
            QTest.qWait(10)
            if picker.retry_button.isVisible():
                break
        self.assertTrue(picker.retry_button.isVisible())
        self.assertFalse(window.isVisible())
        picker.retry_button.click()
        for _ in range(500):
            QTest.qWait(10)
            if window.isVisible():
                break
        self.assertTrue(window.isVisible())
        self.assertFalse(dash._task_materialize_queue)
        self.assertFalse(any(visible for _, visible in updates))
        self.assertTrue(any("shots" in text for text, _ in updates))
        self.assertTrue(any("tasks" in text for text, _ in updates))
        self.assertEqual(len(dash.findChild(ShotCard)._task_widgets_by_id), 21)

    def test_ready_workspace_waits_for_click_and_switch_discards_it(self):
        from PyQt6.QtCore import QSettings
        from artist_session import start_artist_session
        from settings import get_settings_manager
        from unittest.mock import Mock

        app = QApplication.instance() or QApplication([])
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("artist_session.QSettings", side_effect=lambda *_:
                                QSettings(str(directory / "picker.ini"), QSettings.Format.IniFormat)))
        self.enterContext(patch("artist_session.get_settings_file_path", return_value=str(directory / "legacy")))
        self.enterContext(patch("settings._settings_manager", None))
        roster = [{"id": i, "username": name, "first_name": name, "last_name": ""}
                  for i, name in [(1, "Avery"), (2, "Jules")]]

        shot = {"id": 4, "title": "SH004", "tasks": [{"id": 8, "title": "Comp"}]}
        jobs = [{"id": 1, "title": "Job", "timelines": [{"id": 1, "title": "Cut", "shots": [shot]}]}]

        def get(url, **kwargs):
            reply = MagicMock()
            if url.endswith("artist-profiles"):
                reply.json.return_value = roster
            elif url.endswith("/api/"):
                reply.__enter__.return_value.json.return_value = jobs
            else:
                reply.json.return_value = {"revision": "v1", "settings": {}}
            return reply

        self.enterContext(patch("artist_session.requests.get", side_effect=get))
        self.enterContext(patch.object(http_help.DjangoAPI, "get_users", return_value=[]))
        self.enterContext(patch.object(http_help.DjangoAPI, "getAPI", return_value=jobs))
        self.enterContext(patch("widgets.HAS_MULTIMEDIA", False))
        prepared = []

        from activity_page import ActivityPage
        from main import MainWindow
        self.enterContext(patch.object(http_help.DjangoAPI, "get_recent_activity", return_value=[]))
        activity_stopped = []

        class Window(MainWindow):
            def __init__(self):
                QMainWindow.__init__(self)
                self._settings_manager = get_settings_manager()
                self.page_nukedash = page_nukedash()
                self.setCentralWidget(self.page_nukedash)
                self.page_timeline_review = Mock()
                if not prepared:
                    self.page_activity = ActivityPage(self)
                self.activate_staged_session = Mock()
                prepared.append(self)

            def closeEvent(self, event):
                super().closeEvent(event)
                if hasattr(self, "page_activity"):
                    activity_stopped.append(not self.page_activity._activity_thread.isRunning())

        observations = []
        select_for_startup = ProfileSplash.select_for_startup

        def choose(picker):
            def click():
                card = prepared[-1].findChild(ShotCard)
                rows = list(card._task_widgets_by_id) if card else []
                observations.append((picker.artist_id, picker.isVisible(), picker.load_label.text(), rows))
                picker.choose("2")
            QTimer.singleShot(5200, click)
            return select_for_startup(picker)

        self.enterContext(patch.object(ProfileSplash, "select_for_startup", choose))
        window = start_artist_session(Window)
        from PyQt6.QtCore import QEvent
        self.addCleanup(app.sendPostedEvents, None, QEvent.Type.DeferredDelete)
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        self.addCleanup(window._settings_manager.startup_picker.deleteLater)
        self.addCleanup(window._settings_manager.startup_picker.close)
        self.assertEqual(len(prepared), 2)
        self.assertEqual(activity_stopped, [True])
        self.assertEqual(observations, [(None, True, "Workspace ready · Choose your profile", [8])])
        prepared[0].activate_staged_session.assert_not_called()
        window.activate_staged_session.assert_called_once()
        self.assertEqual(window._settings_manager.artist["id"], 2)
