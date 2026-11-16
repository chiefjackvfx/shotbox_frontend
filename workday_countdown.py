"""Compact status-bar countdown to 6 PM in the user's local time."""
from datetime import datetime
from math import ceil

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication, QLabel

from themes import set_widget_style


def hours_left_text(now):
    end = now.replace(hour=18, minute=0, second=0, microsecond=0)
    minutes = max(0, ceil((end - now).total_seconds() / 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m left today"


class WorkdayCountdown(QLabel):
    def __init__(self, parent=None, *, clock=None):
        super().__init__(parent)
        self.clock = clock or datetime.now
        self.setObjectName("workday_countdown")
        self.setToolTip("Time remaining until 6:00 PM, using your computer's local time.")
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setContentsMargins(0, 0, 12, 0)
        set_widget_style(self, "color: #9CA3AF; font-size: 11px;")
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._application_state_changed)
            app.aboutToQuit.connect(self.timer.stop)
        self.refresh()
        self.timer.start()

    def refresh(self):
        self.setText(hours_left_text(self.clock()))

    def _application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self.refresh()
