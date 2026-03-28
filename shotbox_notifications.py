"""
ShotBox Notification System
Lightweight desktop notification popups styled via app QSS.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QCheckBox,
    QComboBox,
    QSpinBox,
    QGroupBox,
)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve
from PyQt6.QtGui import QFont


@dataclass
class NotificationSettings:
    mode: str = "off"  # off, silent, on
    animations_enabled: bool = True
    lifetime_ms: int = 5000
    do_not_disturb: bool = False
    size: str = "normal"  # compact, normal, large
    subtle_mode: bool = False

    def get_size(self) -> tuple[int, int]:
        if self.size == "compact" or self.subtle_mode:
            return (300, 70)
        if self.size == "large":
            return (380, 120)
        return (340, 100)

    def font_sizes(self) -> tuple[int, int, int]:
        if self.size == "compact" or self.subtle_mode:
            return (8, 9, 7)
        if self.size == "large":
            return (10, 11, 9)
        return (9, 10, 8)


class NotificationManager:
    """Manages positioning and lifecycle of active notifications."""

    def __init__(self) -> None:
        self.active_notifications: list[NotificationWidget] = []
        self.spacing = 10

    def add_notification(self, notification: "NotificationWidget") -> None:
        self.active_notifications.append(notification)
        self.reposition_all()

    def remove_notification(self, notification: "NotificationWidget") -> None:
        if notification in self.active_notifications:
            self.active_notifications.remove(notification)
            self.reposition_all()

    def reposition_all(self) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        screen_geo = screen.availableGeometry()
        width, _ = NotificationSystem.instance.settings.get_size()
        x = screen_geo.x() + screen_geo.width() - width - 20
        current_y = screen_geo.y() + screen_geo.height() - 20

        for notif in reversed(self.active_notifications):
            target_y = current_y - notif.height()
            notif.animate_to_position(x, target_y)
            current_y = target_y - self.spacing


class NotificationWidget(QWidget):
    """Individual notification popup."""

    def __init__(
        self,
        username: str,
        action: str,
        shot_code: str,
        detail: str,
        settings: NotificationSettings,
        time_ago: Optional[str] = None,
    ) -> None:
        super().__init__(None)
        self.settings = settings
        self.manager: Optional[NotificationManager] = None
        self.is_closing = False

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        font_normal, font_shot, font_detail = self.settings.font_sizes()

        # Container frame
        self.container = QFrame(self)
        self.container.setObjectName("notification_container")

        main_layout = QVBoxLayout(self.container)
        if self.settings.subtle_mode or self.settings.size == "compact":
            main_layout.setContentsMargins(8, 6, 8, 6)
            main_layout.setSpacing(2)
        elif self.settings.size == "large":
            main_layout.setContentsMargins(12, 10, 12, 10)
            main_layout.setSpacing(5)
        else:
            main_layout.setContentsMargins(10, 8, 10, 8)
            main_layout.setSpacing(4)

        # Top row
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        user_text = username.upper()
        if time_ago:
            user_text = f"{user_text} · {time_ago}"
        user_time = QLabel(user_text)
        user_time.setObjectName("notification_user")
        user_time.setFont(QFont("Consolas", font_normal, QFont.Weight.Bold))
        top_row.addWidget(user_time)

        top_row.addStretch()

        close_btn = QPushButton("×")
        close_btn.setObjectName("notification_close")
        close_btn.setFixedSize(16, 16)
        close_btn.clicked.connect(self.fade_out)
        top_row.addWidget(close_btn)

        main_layout.addLayout(top_row)

        action_label = QLabel(action)
        action_label.setObjectName("notification_action")
        action_label.setFont(QFont("Consolas", font_normal))
        main_layout.addWidget(action_label)

        if not self.settings.subtle_mode and shot_code:
            shot_label = QLabel(shot_code)
            shot_label.setObjectName("notification_shot")
            shot_label.setFont(QFont("Consolas", font_shot, QFont.Weight.Bold))
            main_layout.addWidget(shot_label)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setObjectName("notification_detail")
            detail_label.setFont(QFont("Consolas", font_detail))
            main_layout.addWidget(detail_label)

        container_layout = QVBoxLayout(self)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(self.container)

        width, height = self.settings.get_size()
        self.setFixedSize(width, height)

    def animate_to_position(self, x: int, y: int) -> None:
        if not self.settings.animations_enabled:
            self.setGeometry(x, y, self.width(), self.height())
            return

        if hasattr(self, "pos_anim"):
            self.pos_anim.stop()

        self.pos_anim = QPropertyAnimation(self, b"geometry")
        self.pos_anim.setDuration(250)
        self.pos_anim.setStartValue(self.geometry())
        self.pos_anim.setEndValue(QRect(x, y, self.width(), self.height()))
        self.pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pos_anim.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(self.settings.lifetime_ms, self.fade_out)

    def show_notification(self, manager: NotificationManager) -> None:
        screen = QApplication.primaryScreen()
        if not screen:
            return
        screen_geo = screen.availableGeometry()
        x = screen_geo.x() + screen_geo.width() - self.width() - 20
        start_y = screen_geo.y() + screen_geo.height() + 50

        self.setGeometry(x, start_y, self.width(), self.height())
        self.show()
        self.raise_()
        manager.add_notification(self)

    def fade_out(self) -> None:
        if self.is_closing:
            return
        self.is_closing = True

        if not self.settings.animations_enabled:
            self.cleanup()
            return

        current_geo = self.geometry()
        end_x = current_geo.x() + 30

        self.anim_slide = QPropertyAnimation(self, b"geometry")
        self.anim_slide.setDuration(300)
        self.anim_slide.setStartValue(current_geo)
        self.anim_slide.setEndValue(
            QRect(end_x, current_geo.y(), current_geo.width(), current_geo.height())
        )
        self.anim_slide.setEasingCurve(QEasingCurve.Type.InCubic)

        self.anim_fade = QPropertyAnimation(self, b"windowOpacity")
        self.anim_fade.setDuration(300)
        self.anim_fade.setStartValue(1.0)
        self.anim_fade.setEndValue(0.0)
        self.anim_fade.finished.connect(self.cleanup)

        self.anim_slide.start()
        self.anim_fade.start()

    def cleanup(self) -> None:
        if self.manager:
            self.manager.remove_notification(self)
        self.close()


class NotificationSystem:
    """Main notification system for ShotBox."""

    instance: "NotificationSystem"

    def __init__(self) -> None:
        self.settings = NotificationSettings()
        self.manager = NotificationManager()
        NotificationSystem.instance = self

    def set_mode(self, mode: str) -> None:
        if mode not in ("off", "silent", "on"):
            mode = "off"
        self.settings.mode = mode

    def set_animations(self, enabled: bool) -> None:
        self.settings.animations_enabled = bool(enabled)

    def set_lifetime_ms(self, milliseconds: int) -> None:
        self.settings.lifetime_ms = max(500, int(milliseconds))

    def set_do_not_disturb(self, enabled: bool) -> None:
        self.settings.do_not_disturb = bool(enabled)

    def set_size(self, size: str) -> None:
        if size in ("compact", "normal", "large"):
            self.settings.size = size

    def set_subtle_mode(self, enabled: bool) -> None:
        self.settings.subtle_mode = bool(enabled)

    def is_enabled(self) -> bool:
        return self.settings.mode in ("silent", "on") and not self.settings.do_not_disturb

    def show_notification(
        self,
        username: str,
        action: str,
        shot_code: str,
        detail: str,
        time_ago: Optional[str] = None,
    ) -> None:
        if not self.is_enabled():
            return

        if self.settings.mode == "on":
            QApplication.beep()

        notif = NotificationWidget(
            username=username,
            action=action,
            shot_code=shot_code,
            detail=detail,
            settings=self.settings,
            time_ago=time_ago,
        )
        notif.manager = self.manager
        notif.show_notification(self.manager)

    def clear_all_notifications(self) -> None:
        for notif in list(self.manager.active_notifications):
            notif.fade_out()


def run_demo() -> None:
    import os
    import random
    import sys

    app = QApplication(sys.argv)

    # Optional: load app QSS for demo styling
    script_dir = os.path.dirname(os.path.abspath(__file__))
    qss_path = os.path.join(script_dir, "ui", "dark_v01.qss")
    if os.path.exists(qss_path):
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except Exception:
            pass

    notif_system = NotificationSystem()
    notif_system.set_mode("on")

    demo = QWidget()
    demo.setWindowTitle("ShotBox Notifications Demo")
    demo.setMinimumSize(420, 520)

    layout = QVBoxLayout(demo)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)

    title = QLabel("SHOTBOX NOTIFICATIONS")
    title.setObjectName("notification_demo_title")
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(title)

    settings_group = QGroupBox("Settings")
    settings_layout = QVBoxLayout(settings_group)

    anim_check = QCheckBox("Enable Animations")
    anim_check.setChecked(True)
    anim_check.toggled.connect(notif_system.set_animations)
    settings_layout.addWidget(anim_check)

    dnd_check = QCheckBox("Do Not Disturb")
    dnd_check.toggled.connect(notif_system.set_do_not_disturb)
    settings_layout.addWidget(dnd_check)

    subtle_check = QCheckBox("Subtle Mode")
    subtle_check.toggled.connect(notif_system.set_subtle_mode)
    settings_layout.addWidget(subtle_check)

    size_row = QHBoxLayout()
    size_label = QLabel("Size:")
    size_combo = QComboBox()
    size_combo.addItems(["Compact", "Normal", "Large"])
    size_combo.setCurrentText("Normal")
    size_combo.currentTextChanged.connect(lambda t: notif_system.set_size(t.lower()))
    size_row.addWidget(size_label)
    size_row.addWidget(size_combo)
    size_row.addStretch()
    settings_layout.addLayout(size_row)

    lifetime_row = QHBoxLayout()
    lifetime_label = QLabel("Lifetime (s):")
    lifetime_spin = QSpinBox()
    lifetime_spin.setRange(2, 30)
    lifetime_spin.setValue(5)
    lifetime_spin.valueChanged.connect(lambda v: notif_system.set_lifetime_ms(v * 1000))
    lifetime_row.addWidget(lifetime_label)
    lifetime_row.addWidget(lifetime_spin)
    lifetime_row.addStretch()
    settings_layout.addLayout(lifetime_row)

    layout.addWidget(settings_group)

    trigger_group = QGroupBox("Triggers")
    trigger_layout = QVBoxLayout(trigger_group)

    usernames = ["jsmith", "mchen", "agarcia", "tlee", "rkumar"]
    shot_codes = ["SH_010_0020", "SH_015_0130", "SH_022_0045", "SH_008_0210"]
    actions = [
        ("Updated status", "In Progress -> Review"),
        ("Started work", "Task: Compositing"),
        ("Changed frame range", "1001-1048 -> 1001-1096"),
        ("Added note", "Needs more smoke"),
        ("Completed task", "Compositing finished"),
    ]

    def show_random():
        action, detail = random.choice(actions)
        notif_system.show_notification(
            random.choice(usernames),
            action,
            random.choice(shot_codes),
            detail,
            time_ago="Just now",
        )

    btn_single = QPushButton("Trigger Single")
    btn_single.clicked.connect(show_random)
    trigger_layout.addWidget(btn_single)

    btn_double = QPushButton("Trigger Double")
    btn_double.clicked.connect(lambda: [show_random() for _ in range(2)])
    trigger_layout.addWidget(btn_double)

    btn_triple = QPushButton("Trigger Triple")
    btn_triple.clicked.connect(lambda: [show_random() for _ in range(3)])
    trigger_layout.addWidget(btn_triple)

    btn_clear = QPushButton("Clear All")
    btn_clear.clicked.connect(notif_system.clear_all_notifications)
    trigger_layout.addWidget(btn_clear)

    layout.addWidget(trigger_group)
    layout.addStretch()

    demo.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_demo()
