import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from datetime import datetime, timedelta
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from workday_countdown import WorkdayCountdown, hours_left_text


class WorkdayCountdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_remaining_time_rounds_up_to_minutes_and_never_goes_negative(self):
        for hour, minute, second, expected in (
            (9, 0, 0, '9h 00m left today'),
            (15, 30, 0, '2h 30m left today'),
            (17, 59, 59, '0h 01m left today'),
            (18, 0, 0, '0h 00m left today'),
            (23, 59, 59, '0h 00m left today'),
        ):
            with self.subTest(hour=hour, minute=minute, second=second):
                self.assertEqual(hours_left_text(datetime(2026, 11, 16, hour, minute, second)), expected)

    def test_live_refresh_foreground_resume_and_next_day(self):
        now = [datetime(2026, 11, 16, 17, 59, 59)]
        label = WorkdayCountdown(clock=lambda: now[0])
        try:
            self.assertTrue(label.timer.isActive())
            self.assertEqual(label.text(), '0h 01m left today')
            now[0] += timedelta(seconds=1)
            label.timer.timeout.emit()
            self.assertEqual(label.text(), '0h 00m left today')
            now[0] = datetime(2026, 11, 17, 10, 15)
            self.app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
            self.assertEqual(label.text(), '7h 45m left today')
        finally:
            label.timer.stop()
            label.deleteLater()
            self.app.processEvents()

    def test_main_window_places_countdown_immediately_before_version(self):
        from test_main_window_assignment_board_and_load_timing import build_main_window
        window = build_main_window()
        try:
            window.resize(1200, 800)
            window.show()
            self.app.processEvents()
            countdown = window.workday_countdown
            version = window.version_label
            self.assertTrue(countdown.isVisible())
            self.assertTrue(version.isVisible())
            self.assertIs(countdown.parent(), window.statusBar())
            self.assertLess(countdown.geometry().right(), version.geometry().left())
        finally:
            window.workday_countdown.timer.stop()
            window.close()
            window.deleteLater()
            self.app.processEvents()
