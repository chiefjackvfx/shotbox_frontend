"""Regression coverage for the picker-to-dashboard handoff."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QPoint, QSettings, Qt, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow

import http_help
from artist_picker import ArtistCircle, ProfileIris, ProfileSplash
from main import show_after_workspace_load
from page_nukedash import page_nukedash
from settings import SettingsManager
from widgets import ShotCard


class LoginHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_picker_stays_centered_as_the_roster_expands(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        memory = QSettings(str(directory / "picker.ini"), QSettings.Format.IniFormat)
        area = self.app.primaryScreen().availableGeometry()
        center = area.center()
        picker = ProfileSplash(None, memory, center)
        self.addCleanup(picker.close)
        picker.show()
        self.assertEqual(picker.geometry().center(), center)
        picker.populate_profiles([(str(i), str(i), str(i)) for i in range(6)])
        self.assertEqual(picker.geometry().center(), center)

    def test_configurable_login_delay_is_remembered_and_allows_override(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        path = str(directory / "picker.ini")
        profiles = [("1", "jack", "Jack"), ("2", "cronk", "Cronk")]

        def open_picker():
            picker = ProfileSplash(profiles, QSettings(path, QSettings.Format.IniFormat))
            self.addCleanup(picker.close)
            picker.show()
            return picker

        first = open_picker()
        self.assertTrue(first.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(first.auto_login.isChecked())
        self.assertFalse(first.login_timer.isActive())
        self.assertEqual(first.login_delay.value(), 1)
        first.login_delay.setValue(3)
        first.settings.setValue("last_artist", "1")
        first.close()

        picker = open_picker()
        self.assertEqual(picker.login_delay.value(), 3)
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
        QTest.qWait(150)
        alternative = returning.findChildren(ArtistCircle)[1]
        alternative.setFocus()
        QTest.qWait(50)
        self.assertFalse(returning.login_timer.isActive())
        paused = returning.returning_artist.countdown_fraction
        self.assertGreater(paused, 0)
        self.assertEqual(returning.browse_name.text(), "Auto-open paused")
        QTest.qWait(200)
        self.assertEqual(returning.returning_artist.countdown_fraction, paused)
        returning.returning_artist.setFocus()
        QTest.qWait(2350)
        self.assertIsNone(returning.artist_id)
        QTest.qWait(650)
        self.assertEqual(returning.artist_id, "1")

    def test_editing_login_delay_pauses_and_enter_resumes_countdown(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        memory = QSettings(str(directory / "picker.ini"), QSettings.Format.IniFormat)
        memory.setValue("last_artist", "1")
        picker = ProfileSplash([("1", "jack", "Jack")], memory)
        self.addCleanup(picker.close)
        picker.show()
        self.assertEqual(picker.login_timer.interval(), 1000)
        picker.login_delay.setFocus()
        QTest.qWait(50)
        self.assertFalse(picker.login_timer.isActive())
        picker.login_delay.setValue(2)
        QTest.keyClick(picker.login_delay, Qt.Key.Key_Return)
        QTest.qWait(50)
        self.assertTrue(picker.login_timer.isActive())
        self.assertEqual(picker.login_timer.interval(), 2000)

    def test_larger_roster_wraps_and_every_profile_is_visible(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        memory = QSettings(str(directory / "picker.ini"), QSettings.Format.IniFormat)
        memory.setValue("last_artist", "6")
        picker = ProfileSplash(None, memory)
        self.addCleanup(picker.close)
        picker.show()
        picker.populate_profiles([(str(i), f"user{i}", f"Artist {i}") for i in range(7)])
        picker.set_load_progress("Loading workspace…")
        QTest.qWait(30)
        circles = picker.findChildren(ArtistCircle)
        self.assertEqual(len({circle.y() for circle in circles}), 2)
        self.assertFalse(picker.scroll.horizontalScrollBar().isVisible())
        self.assertFalse(picker.scroll.verticalScrollBar().isVisible())
        self.assertTrue(all(circle.visibleRegion().contains(circle.rect()) for circle in circles))
        picker.returning_artist.click()
        self.assertEqual(picker.artist_id, "6")

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

    @patch.object(QApplication, "platformName", return_value="xcb")
    def test_retry_then_wait_for_restored_tasks_before_showing_dashboard(self, _platform):
        app = QApplication.instance() or QApplication([])
        directory = self.enterContext(TemporaryDirectory())
        settings = SettingsManager(str(Path(directory) / "prefs.yaml"))
        settings.set("login_animation", "none", save=False)
        iris = self.enterContext(patch("main.ProfileIris"))
        self.enterContext(patch("main.motion_enabled", return_value=True))
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
        iris.assert_not_called()
        self.assertFalse(dash._task_materialize_queue)
        self.assertFalse(any(visible for _, visible in updates))
        self.assertTrue(any("shots" in text for text, _ in updates))
        self.assertTrue(any("tasks" in text for text, _ in updates))
        self.assertEqual(len(dash.findChild(ShotCard)._task_widgets_by_id), 21)

    def test_inner_circle_reveals_workspace_before_main_window_is_shown(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        memory = QSettings(str(directory / "picker.ini"), QSettings.Format.IniFormat)
        picker = ProfileSplash([("1", "avery", "Avery Morgan")], memory)
        picker.keep_open_after_choice = True
        picker.move(35, 70)
        picker.show()
        picker.choose("1")
        window = QMainWindow()
        window.setGeometry(180, 110, 700, 500)
        window.setStyleSheet("background-color: #13765c")
        window.page_nukedash = MagicMock()
        window.page_nukedash._awaiting_workspace = False
        window._settings_manager = MagicMock()
        self.addCleanup(window.close)
        self.addCleanup(self.app.setQuitOnLastWindowClosed, self.app.quitOnLastWindowClosed())
        with (patch("main.motion_enabled", return_value=True),
              patch.object(QApplication, "platformName", return_value="offscreen")):
            show_after_workspace_load(window, picker)
        overlay = picker.findChild(ProfileIris)
        overlay.animation.pause()
        overlay.animation.setCurrentTime(0)
        self.assertFalse(window.isVisible())
        self.assertTrue(picker.isVisible())
        self.assertEqual(overlay.grab().toImage().pixelColor(5, 5).alpha(), 0)
        overlay.animation.setCurrentTime(265)
        frame = overlay.grab().toImage()
        self.assertEqual(frame.pixelColor(overlay.origin.toPoint()).name(), "#13765c")
        corner = overlay.workspace.bottomRight().toPoint() - QPoint(4, 4)
        self.assertEqual(frame.pixelColor(corner).alpha(), 0)
        self.assertFalse(window.isVisible())
        window.setUpdatesEnabled(False)
        overlay.animation.resume()
        QTest.qWait(overlay.animation.duration() + 100)
        self.assertTrue(window.isVisible())
        self.assertTrue(picker.isVisible())
        self.assertTrue(overlay.isVisible())
        last_frame = overlay.grab().toImage()
        self.assertEqual(last_frame.pixelColor(corner).name(), "#13765c")
        window.setUpdatesEnabled(True)
        window.repaint()
        QTest.qWait(150)
        self.assertTrue(overlay.isVisible())
        QTest.qWait(180)
        self.assertIsNone(window._settings_manager.startup_picker)

    def test_windows_handoff_shows_independent_dashboard_for_each_animation(self):
        for animation in ("iris", "chromatic", "none"):
            with self.subTest(animation=animation):
                memory = MagicMock()
                memory.value.side_effect = lambda key, default, **_: default
                picker = ProfileSplash([("1", "avery", "Avery Morgan")], memory)
                picker.keep_open_after_choice = True
                picker.show()
                picker.choose("1")
                window = QMainWindow()
                self.addCleanup(window.close)
                self.addCleanup(self.app.setQuitOnLastWindowClosed, self.app.quitOnLastWindowClosed())
                window.page_nukedash = MagicMock()
                window.page_nukedash._awaiting_workspace = True
                window._settings_manager = MagicMock()
                window._settings_manager.get.return_value = animation
                with (patch.object(QApplication, "platformName", return_value="windows"),
                      patch.object(window, "grab", side_effect=AssertionError("captured hidden Windows dashboard")),
                      patch("main.motion_enabled", return_value=True)):
                    show_after_workspace_load(window, picker)
                    if animation != "none":
                        overlay = picker.findChild(ProfileIris)
                        self.assertIsNone(overlay.snapshot)
                        self.assertFalse(overlay.isWindow())
                        overlay.animation.setCurrentTime(310)
                        overlay.repaint()
                        self.assertFalse(window.isVisible())
                        window.setUpdatesEnabled(False)
                        overlay.animation.setCurrentTime(overlay.animation.duration())
                    QTest.qWait(400)
                self.assertTrue(window.isVisible())
                self.assertTrue(window.mask().isEmpty())
                self.assertIsNone(window.windowHandle().transientParent())
                self.assertIsNone(window._settings_manager.startup_picker)
                window.close()

    def test_native_iris_reveals_one_live_window_in_place(self):
        if self.app.platformName() != "xcb":
            self.skipTest("Requires native window shape support")
        memory = MagicMock()
        memory.value.side_effect = lambda key, default, **_: default
        picker = ProfileSplash([("1", "avery", "Avery Morgan")], memory)
        picker.keep_open_after_choice = True
        picker.move(100, 100)
        picker.show()
        picker.choose("1")
        window = QMainWindow()
        window.setStyleSheet("background: #13765c")
        window.move(50, 50)
        window.resize(800, 550)
        self.addCleanup(window.close)
        self.addCleanup(picker.close)
        overlay = ProfileIris(window, picker)
        overlay.start()
        QTest.qWait(100)
        overlay.animation.pause()
        overlay.animation.setCurrentTime(265)
        self.assertIsNone(overlay.snapshot)
        self.assertTrue(window.isVisible())
        self.assertTrue(window.mask().contains((overlay.origin - overlay.workspace.topLeft()).toPoint()))
        self.assertFalse(window.mask().contains(QPoint(2, 2)))
        live_position = window.mapToGlobal(QPoint())
        QTest.qWait(80)
        if self.app.platformName() == "xcb":
            frame = window.screen().grabWindow(0).toImage()
            self.assertEqual(frame.pixelColor(overlay.mapToGlobal(overlay.origin.toPoint())).name(),
                             "#13765c")
        overlay.animation.resume()
        QTest.qWait(750)
        self.assertTrue(window.mask().isEmpty())
        self.assertFalse(picker.isVisible())
        self.assertEqual(window.mapToGlobal(QPoint()), live_position)

    def test_chromatic_login_captures_before_attaching_native_overlay(self):
        memory = MagicMock()
        memory.value.side_effect = lambda key, default, **_: default
        picker = ProfileSplash([("1", "avery", "Avery Morgan")], memory)
        picker.keep_open_after_choice = True
        picker.show()
        picker.choose("1")
        window = QMainWindow()
        window._settings_manager = MagicMock()
        window._settings_manager.get.return_value = "chromatic"
        window.page_nukedash = MagicMock()
        window.page_nukedash._awaiting_workspace = False
        self.addCleanup(window.close)
        self.addCleanup(self.app.setQuitOnLastWindowClosed, self.app.quitOnLastWindowClosed())
        grab = window.grab

        def grab_workspace():
            self.assertIsNone(window.findChild(ProfileIris))
            return grab()

        with (patch.object(QApplication, "platformName", return_value="xcb"),
              patch.object(window, "grab", side_effect=grab_workspace) as capture,
              patch("main.motion_enabled", return_value=True)):
            show_after_workspace_load(window, picker)
        capture.assert_called_once()
        overlay = window.findChild(ProfileIris)
        self.assertEqual(len(overlay.chromatic_channels), 3)
        self.assertTrue(all(not channel.isNull() for channel in overlay.chromatic_channels))
        QTest.qWait(overlay.animation.duration() + 400)
        self.assertTrue(window.isVisible())
        self.assertTrue(window.mask().isEmpty())
        self.assertIsNone(window._settings_manager.startup_picker)

    def test_closing_picker_during_roster_fetch_ignores_late_response(self):
        from threading import Event
        from artist_session import fetch_roster

        directory = Path(self.enterContext(TemporaryDirectory()))
        picker = ProfileSplash(None, QSettings(str(directory / "picker.ini"), QSettings.Format.IniFormat))
        self.addCleanup(picker.close)
        release = Event()
        finished = Event()
        completed = []

        def get(*args, **kwargs):
            release.wait(2)
            finished.set()
            return MagicMock()

        with patch("artist_session.requests.get", side_effect=get):
            fetch_roster("http://studio", picker, completed.append)
            self.assertTrue(picker.isVisible())
            self.assertFalse(finished.is_set())
            picker.reject()
            self.assertEqual(completed, [None])
            release.set()
            self.assertTrue(finished.wait(1))
            QTest.qWait(60)
            self.assertEqual(completed, [None])

    def test_windows_builds_only_the_selected_artists_workspace(self):
        from concurrent.futures import Future
        from artist_session import start_artist_session
        from settings import get_settings_manager

        directory = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch("artist_session.QSettings", side_effect=lambda *_:
                                QSettings(str(directory / "picker.ini"), QSettings.Format.IniFormat)))
        self.enterContext(patch("artist_session.get_settings_file_path", return_value=str(directory / "legacy")))
        self.enterContext(patch("settings._settings_manager", None))
        self.enterContext(patch.object(QApplication, "platformName", return_value="windows"))
        self.enterContext(patch("artist_session.StartupShots"))
        request = self.enterContext(patch("artist_session.requests.get"))
        request.return_value.json.return_value = {"revision": "v1", "settings": {}}
        def roster(server, picker, done):
            picker.show()
            result = Future()
            result.set_result([dict(id=i, username=name, first_name=name, last_name="")
                               for i, name in ((1, "cronk"), (2, "temp"))])
            done(result)

        self.enterContext(patch("artist_session.fetch_roster", side_effect=roster))
        window = QMainWindow()
        window.activate_staged_session = MagicMock()
        self.addCleanup(window.close)
        def build():
            window._settings_manager = get_settings_manager()
            return window

        completed = []
        start_artist_session(build, completed.append)
        request.assert_not_called()
        picker = self.app.pending_artist_picker
        self.addCleanup(picker.close)
        picker.choose("2")
        QTest.qWait(50)
        self.assertEqual(completed, [window])
        request.assert_called_once()
        self.assertEqual(window._settings_manager.artist["username"], "temp")
        self.assertIsNone(self.app.pending_artist_picker)

    @patch.object(QApplication, "platformName", return_value="xcb")
    def test_ready_workspace_waits_for_click_and_switch_discards_it(self, _platform):
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
        completed = []
        start_artist_session(Window, completed.append)
        for _ in range(300):
            if completed:
                break
            QTest.qWait(20)
        self.assertEqual(len(completed), 1)
        window = completed[0]
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
