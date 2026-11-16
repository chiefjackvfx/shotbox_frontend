"""One-time local Special Events, independent of the system timezone."""
from dataclasses import dataclass
from datetime import datetime, timezone

from PyQt6.QtCore import QObject, QTimer, Qt, QSignalBlocker
from PyQt6.QtWidgets import QApplication

from themes import (DEFAULT_THEME, CHRISTMAS_THEME, apply_theme,
                    refresh_theme_headers)

ACTIVATION = datetime(2026, 11, 18, 23, tzinfo=timezone.utc)
EVENT_THEME = "gta6_vice_sunset.qss"
COMPLETION_KEY = "special_events.gta6_2026_11_18_applied"
ACTIVATION_LABEL = "Wednesday 18 November 2026, 11:00 PM UK time"


@dataclass(frozen=True)
class SpecialEvent:
    name: str
    activation: datetime
    theme: str
    completion_key: str
    activation_label: str


GTA_EVENT = SpecialEvent("GTA VI", ACTIVATION, EVENT_THEME, COMPLETION_KEY, ACTIVATION_LABEL)
CHRISTMAS_EVENT = SpecialEvent(
    "Christmas", datetime(2026, 12, 1, tzinfo=timezone.utc), CHRISTMAS_THEME,
    "special_events.christmas_2026_applied", "Tuesday 1 December 2026, 12:00 AM UK time")
SPECIAL_EVENTS = (GTA_EVENT, CHRISTMAS_EVENT)



class SpecialEventController(QObject):
    def __init__(self, settings, parent=None, *, clock=None, theme_applier=None):
        super().__init__(parent)
        self.settings = settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.theme_applier = theme_applier or (
            lambda filename: apply_theme(filename, fallback=False))
        self.page = None
        self._checking = False
        self.timer = QTimer(self)
        self.timer.setInterval(30_000)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.check)

    def bind_settings_page(self, page):
        self.page = page
        self._refresh_countdown()

    def _refresh_countdown(self):
        now = self.clock()
        refresh_theme_headers(now)

    def start(self):
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._application_state_changed)
            app.aboutToQuit.connect(self.timer.stop)
        self.check()
        # Keep checking after activation so the party countdown rolls over daily.
        self.timer.start()

    def _application_state_changed(self, state):
        if state == Qt.ApplicationState.ApplicationActive:
            self.check()

    def check(self):
        """Apply and persist once; leave failed attempts eligible for retry."""
        if self._checking:
            return False
        now = self.clock()
        due = [event for event in SPECIAL_EVENTS if now >= event.activation
               and self.settings.get(event.completion_key, False) is not True]
        if not due:
            self._refresh_countdown()
            return False

        self._checking = True
        previous_theme = self.settings.get("theme_file", DEFAULT_THEME)
        # Late launches receive the newest due event in a single transaction.
        event = due[-1]
        previous_markers = {item.completion_key: self.settings.get(item.completion_key, False)
                            for item in due}
        previous_actual = getattr(QApplication.instance(), "_shotbox_theme", previous_theme)
        applied = False
        try:
            if self.page is not None:
                self.page._flush_autosave()
                if self.page._autosave_pending:
                    return False
                previous_theme = self.settings.get("theme_file", DEFAULT_THEME)
                previous_actual = getattr(QApplication.instance(), "_shotbox_theme", previous_theme)
            if self.theme_applier(event.theme) != event.theme:
                raise RuntimeError("The Special Event theme could not be loaded")
            applied = True
            self.settings.set("theme_file", event.theme, save=False)
            for item in due:
                self.settings.set(item.completion_key, True, save=False)
            if not self.settings.save():
                raise OSError("Could not save Special Event settings")
        except (OSError, UnicodeError, RuntimeError) as exc:
            self.settings.set("theme_file", previous_theme, save=False)
            for key, value in previous_markers.items():
                self.settings.set(key, value, save=False)
            if applied:
                try:
                    self.theme_applier(previous_actual)
                except (OSError, UnicodeError, RuntimeError) as restore_error:
                    print(f"[Special Event] Could not restore theme: {restore_error}")
            print(f"[Special Event] Will retry: {exc}")
            return False
        finally:
            self._checking = False
            self._refresh_countdown()

        if self.page is not None:
            with QSignalBlocker(self.page.theme_combo):
                self.page.theme_combo.setCurrentIndex(self.page.theme_combo.findData(event.theme))
        return True
