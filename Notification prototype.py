"""
ShotBox Notification System - Single File
For rapid development and easy integration.

Usage:
    from shotbox_notifications import NotificationSystem

    notif_system = NotificationSystem()
    notif_system.show_notification("jsmith", "Updated status", "SH_010_0020", "IP → Review")
"""

import sys
import os
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton,
                             QVBoxLayout, QHBoxLayout, QFrame, QCheckBox,
                             QSlider, QComboBox, QGroupBox)
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QRect, QEasingCurve
from PyQt6.QtGui import QFont


# ============================================================================
# FALLBACK STYLESHEET (If QSS file not found)
# ============================================================================

FALLBACK_STYLE = """
/* === NOTIFICATION WIDGETS === */
QWidget {
    background-color: #1A1D23;
    color: #D4D4D4;
    font-family: "Consolas", "JetBrainsMono", "SF Mono", monospace;
    font-size: 12px;
}

QFrame {
    background-color: #1E2228;
    border: 2px solid #2F3542;
    border-radius: 2px;
}

QLabel {
    background-color: transparent;
    color: #D4D4D4;
}

QPushButton {
    background-color: #2F3542;
    color: #D4D4D4;
    border: 2px solid #404754;
    border-radius: 2px;
    padding: 6px 12px;
    font-size: 11px;
    font-weight: bold;
    text-transform: uppercase;
}

QPushButton:hover {
    background-color: #FF6B35;
    border-color: #FF6B35;
    color: #FFFFFF;
}

QPushButton:pressed {
    background-color: #D85A28;
    border-color: #D85A28;
}

QCheckBox {
    color: #D4D4D4;
    font-size: 11px;
    spacing: 6px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 2px solid #2F3542;
    border-radius: 2px;
    background-color: #1E2228;
}

QCheckBox::indicator:checked {
    background-color: #FF6B35;
    border-color: #D85A28;
}

QCheckBox::indicator:hover {
    border-color: #FF6B35;
}

QComboBox {
    background-color: #2F3542;
    color: #D4D4D4;
    border: 2px solid #404754;
    border-radius: 2px;
    padding: 6px 10px;
    font-size: 11px;
}

QComboBox:hover {
    border-color: #FF6B35;
}

QComboBox::drop-down {
    border: none;
    background-color: #404754;
    width: 20px;
}

QComboBox QAbstractItemView {
    background-color: #2F3542;
    border: 2px solid #FF6B35;
    selection-background-color: #FF6B35;
    selection-color: #FFFFFF;
    color: #D4D4D4;
}

QSlider::groove:horizontal {
    border: 1px solid #2F3542;
    background: #1E2228;
    height: 6px;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #FF6B35;
    border: 2px solid #D85A28;
    width: 16px;
    height: 16px;
    border-radius: 2px;
}

QSlider::handle:horizontal:hover {
    background: #FF8357;
}

QGroupBox {
    border: 2px solid #2F3542;
    border-radius: 2px;
    margin-top: 12px;
    padding-top: 10px;
    color: #D4D4D4;
    font-weight: bold;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 5px;
    color: #FF6B35;
}
"""


# ============================================================================
# SETTINGS
# ============================================================================

class NotificationSettings:
    """Global settings for notification system"""
    def __init__(self):
        self.animations_enabled = True
        self.lifetime = 5000  # milliseconds
        self.do_not_disturb = False
        self.dark_mode = True  # ShotBox is dark by default
        self.size = "normal"  # "compact", "normal", "large"
        self.subtle_mode = False

    def get_colors(self):
        """Return ShotBox color scheme"""
        # ShotBox only uses dark mode
        return {
            'bg': '#1E2228',
            'border': '#2F3542',
            'border_hover': '#FF6B35',
            'text_primary': '#FFFFFF',
            'text_secondary': '#9CA3AF',
            'text_tertiary': '#6B7280',
            'icon': '#FF6B35',
            'close_btn': '#6B7280',
            'close_hover': '#FF6B35'
        }

    def get_size(self):
        """Return dimensions based on size setting"""
        if self.size == "compact" or self.subtle_mode:
            return (300, 70)
        elif self.size == "large":
            return (380, 120)
        else:  # normal
            return (340, 100)


# ============================================================================
# NOTIFICATION MANAGER
# ============================================================================

class NotificationManager:
    """Manages positioning and lifecycle of active notifications"""
    def __init__(self):
        self.active_notifications = []
        self.spacing = 10

    def add_notification(self, notification):
        self.active_notifications.append(notification)
        self.reposition_all()

    def remove_notification(self, notification):
        if notification in self.active_notifications:
            self.active_notifications.remove(notification)
            self.reposition_all()

    def reposition_all(self):
        """Reposition all active notifications in a stack"""
        screen = QApplication.primaryScreen().geometry()
        width, _ = NotificationSystem._instance.settings.get_size() if NotificationSystem._instance else (340, 100)
        x = screen.width() - width - 20

        current_y = screen.height() - 60

        for notif in reversed(self.active_notifications):
            target_y = current_y - notif.height()
            notif.animate_to_position(x, target_y)
            current_y = target_y - self.spacing


# ============================================================================
# NOTIFICATION WIDGET
# ============================================================================

class NotificationWidget(QWidget):
    """Individual notification popup"""
    def __init__(self, username, action, shot_code, detail, settings):
        super().__init__(None)
        self.settings = settings
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint |
                           Qt.WindowType.WindowStaysOnTopHint |
                           Qt.WindowType.Tool |
                           Qt.WindowType.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.is_closing = False

        colors = self.settings.get_colors()

        # Container frame
        self.container = QFrame(self)
        self.container.setStyleSheet(f"""
            QFrame {{
                background-color: {colors['bg']};
                border: 2px solid {colors['border']};
                border-radius: 2px;
            }}
        """)

        main_layout = QVBoxLayout(self.container)

        # Adjust padding based on size
        if self.settings.subtle_mode or self.settings.size == "compact":
            main_layout.setContentsMargins(8, 6, 8, 6)
            main_layout.setSpacing(2)
            font_size_normal = 8
            font_size_shot = 9
            font_size_detail = 7
        elif self.settings.size == "large":
            main_layout.setContentsMargins(12, 10, 12, 10)
            main_layout.setSpacing(5)
            font_size_normal = 10
            font_size_shot = 11
            font_size_detail = 9
        else:
            main_layout.setContentsMargins(10, 8, 10, 8)
            main_layout.setSpacing(4)
            font_size_normal = 9
            font_size_shot = 10
            font_size_detail = 8

        # Top row
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        # Status icon
        if not self.settings.subtle_mode:
            status_icon = QLabel("●")
            status_icon.setStyleSheet(f"color: {colors['icon']}; font-size: 16px;")
            top_row.addWidget(status_icon)

        # Username and timestamp
        user_time = QLabel(f"{username.upper()} · 2m ago")
        user_time.setFont(QFont("Consolas", font_size_normal, QFont.Weight.Bold))
        user_time.setStyleSheet(f"color: {colors['text_primary']};")
        top_row.addWidget(user_time)

        top_row.addStretch()

        # Close button
        close_btn = QPushButton("×")
        close_btn.setFixedSize(16, 16)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                color: {colors['close_btn']};
                font-size: 18px;
                font-weight: bold;
                padding: 0px;
            }}
            QPushButton:hover {{
                color: {colors['close_hover']};
            }}
        """)
        close_btn.clicked.connect(self.fade_out)
        top_row.addWidget(close_btn)

        main_layout.addLayout(top_row)

        # Action line
        action_label = QLabel(action)
        action_label.setFont(QFont("Consolas", font_size_normal))
        action_label.setStyleSheet(f"color: {colors['text_primary']};")
        main_layout.addWidget(action_label)

        # Shot code
        if not self.settings.subtle_mode:
            shot_label = QLabel(shot_code)
            shot_label.setFont(QFont("Consolas", font_size_shot, QFont.Weight.Bold))
            shot_label.setStyleSheet(f"color: {colors['text_primary']};")
            main_layout.addWidget(shot_label)

        # Detail line
        detail_label = QLabel(detail)
        detail_label.setFont(QFont("Consolas", font_size_detail))
        detail_label.setStyleSheet(f"color: {colors['text_secondary']};")
        main_layout.addWidget(detail_label)

        # Container layout
        container_layout = QVBoxLayout(self)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(self.container)

        # Size from settings
        width, height = self.settings.get_size()
        self.setFixedSize(width, height)

    def animate_to_position(self, x, y):
        """Animate notification to a new position"""
        if not self.settings.animations_enabled:
            self.setGeometry(x, y, self.width(), self.height())
            return

        if hasattr(self, 'pos_anim'):
            self.pos_anim.stop()

        self.pos_anim = QPropertyAnimation(self, b"geometry")
        self.pos_anim.setDuration(250)
        self.pos_anim.setStartValue(self.geometry())
        self.pos_anim.setEndValue(QRect(x, y, self.width(), self.height()))
        self.pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.pos_anim.start()

    def showEvent(self, event):
        """Handle show event"""
        super().showEvent(event)
        QTimer.singleShot(self.settings.lifetime, self.fade_out)

    def show_notification(self, manager):
        """Show the notification with slide-up animation"""
        screen = QApplication.primaryScreen().geometry()
        x = screen.width() - self.width() - 20
        start_y = screen.height() + 50

        self.setGeometry(x, start_y, self.width(), self.height())
        self.show()
        self.lower()
        manager.add_notification(self)

    def fade_out(self):
        """Fade out and slide right"""
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
        self.anim_slide.setEndValue(QRect(end_x, current_geo.y(),
                                          current_geo.width(), current_geo.height()))
        self.anim_slide.setEasingCurve(QEasingCurve.Type.InCubic)

        self.anim_fade = QPropertyAnimation(self, b"windowOpacity")
        self.anim_fade.setDuration(300)
        self.anim_fade.setStartValue(1.0)
        self.anim_fade.setEndValue(0.0)
        self.anim_fade.finished.connect(self.cleanup)

        self.anim_slide.start()
        self.anim_fade.start()

    def cleanup(self):
        """Remove from manager and close"""
        if hasattr(self, 'manager'):
            self.manager.remove_notification(self)
        self.close()


# ============================================================================
# NOTIFICATION SYSTEM (MAIN CLASS)
# ============================================================================

class NotificationSystem:
    """Main notification system for ShotBox"""

    _instance = None

    def __init__(self, qss_path=None):
        """
        Initialize notification system

        Args:
            qss_path: Path to QSS stylesheet file (optional)
        """
        self.settings = NotificationSettings()
        self.manager = NotificationManager()
        NotificationSystem._instance = self

        # Try to load QSS file, fall back to inline style
        if qss_path and os.path.exists(qss_path):
            try:
                with open(qss_path, 'r') as f:
                    QApplication.instance().setStyleSheet(f.read())
                print(f"✓ Loaded stylesheet: {qss_path}")
            except Exception as e:
                print(f"⚠ Failed to load QSS: {e}")
                print("Using fallback styling")
                QApplication.instance().setStyleSheet(FALLBACK_STYLE)
        else:
            if qss_path:
                print(f"⚠ QSS not found: {qss_path}")
                print("Using fallback styling")
            QApplication.instance().setStyleSheet(FALLBACK_STYLE)

    def show_notification(self, username, action, shot_code, detail):
        """Show a notification popup"""
        if self.settings.do_not_disturb:
            return

        notif = NotificationWidget(username, action, shot_code, detail, self.settings)
        notif.manager = self.manager
        notif.show_notification(self.manager)

    def set_animations(self, enabled):
        """Enable or disable animations"""
        self.settings.animations_enabled = enabled

    def set_lifetime(self, milliseconds):
        """Set notification lifetime in milliseconds"""
        self.settings.lifetime = milliseconds

    def set_do_not_disturb(self, enabled):
        """Enable or disable do not disturb mode"""
        self.settings.do_not_disturb = enabled

    def set_size(self, size):
        """Set size: compact, normal, or large"""
        if size in ["compact", "normal", "large"]:
            self.settings.size = size

    def set_subtle_mode(self, enabled):
        """Enable or disable extra subtle mode"""
        self.settings.subtle_mode = enabled

    def clear_all_notifications(self):
        """Close all active notifications"""
        for notif in list(self.manager.active_notifications):
            notif.fade_out()


# ============================================================================
# DEMO APPLICATION
# ============================================================================

def run_demo():
    """Run demo application with test controls"""
    import random

    app = QApplication(sys.argv)

    # Try to load ShotBox QSS, fall back to inline
    qss_path = "shotbox_style.qss"  # Change this to your QSS path
    notif_system = NotificationSystem(qss_path)

    # Demo window
    demo = QWidget()
    demo.setWindowTitle("ShotBox Notifications Demo")
    demo.setGeometry(100, 100, 400, 550)

    layout = QVBoxLayout()

    # Title
    title = QLabel("SHOTBOX NOTIFICATIONS")
    title.setFont(QFont("Consolas", 12, QFont.Weight.Bold))
    title.setAlignment(Qt.AlignmentFlag.AlignCenter)
    title.setStyleSheet("color: #FF6B35; letter-spacing: 2px;")
    layout.addWidget(title)

    subtitle = QLabel("Auto-cycling every 10 seconds")
    subtitle.setFont(QFont("Consolas", 8))
    subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
    subtitle.setStyleSheet("color: #6B7280;")
    layout.addWidget(subtitle)

    layout.addSpacing(10)

    # Settings
    settings_group = QGroupBox("SETTINGS")
    settings_layout = QVBoxLayout()

    anim_check = QCheckBox("Enable Animations")
    anim_check.setChecked(True)
    anim_check.toggled.connect(notif_system.set_animations)
    settings_layout.addWidget(anim_check)

    dnd_check = QCheckBox("Do Not Disturb")
    dnd_check.toggled.connect(notif_system.set_do_not_disturb)
    settings_layout.addWidget(dnd_check)

    subtle_check = QCheckBox("Extra Subtle Mode")
    subtle_check.toggled.connect(notif_system.set_subtle_mode)
    settings_layout.addWidget(subtle_check)

    settings_layout.addSpacing(10)

    # Lifetime
    lifetime_row = QHBoxLayout()
    lifetime_label = QLabel("Lifetime:")
    lifetime_value = QLabel("5s")
    lifetime_value.setStyleSheet("color: #FF6B35;")
    lifetime_row.addWidget(lifetime_label)
    lifetime_row.addWidget(lifetime_value)
    lifetime_row.addStretch()
    settings_layout.addLayout(lifetime_row)

    lifetime_slider = QSlider(Qt.Orientation.Horizontal)
    lifetime_slider.setMinimum(2)
    lifetime_slider.setMaximum(15)
    lifetime_slider.setValue(5)

    def update_lifetime(value):
        notif_system.set_lifetime(value * 1000)
        lifetime_value.setText(f"{value}s")

    lifetime_slider.valueChanged.connect(update_lifetime)
    settings_layout.addWidget(lifetime_slider)

    settings_layout.addSpacing(10)

    # Size
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

    settings_group.setLayout(settings_layout)
    layout.addWidget(settings_group)

    layout.addSpacing(10)

    # Triggers
    trigger_group = QGroupBox("MANUAL TRIGGERS")
    trigger_layout = QVBoxLayout()

    # Dummy data
    usernames = ["jsmith", "mchen", "agarcia", "tlee", "rkumar", "swilson"]
    shot_codes = ["SH_010_0020", "SH_015_0130", "SH_022_0045", "SH_008_0210"]
    actions = [
        ("Updated status", "In Progress → Review"),
        ("Started working on", "Task: Compositing"),
        ("Changed frame range", "1001-1048 → 1001-1096"),
        ("Added note", '"Needs more smoke"'),
        ("Completed task", "Compositing finished"),
    ]

    def show_random():
        notif_system.show_notification(
            random.choice(usernames),
            *random.choice(actions),
            random.choice(shot_codes)
        )

    def show_double():
        for _ in range(2):
            show_random()

    def show_triple():
        for _ in range(3):
            show_random()

    btn1 = QPushButton("TRIGGER SINGLE")
    btn1.clicked.connect(show_random)
    trigger_layout.addWidget(btn1)

    btn2 = QPushButton("TRIGGER DOUBLE")
    btn2.clicked.connect(show_double)
    trigger_layout.addWidget(btn2)

    btn3 = QPushButton("TRIGGER TRIPLE")
    btn3.clicked.connect(show_triple)
    trigger_layout.addWidget(btn3)

    btn4 = QPushButton("CLEAR ALL")
    btn4.clicked.connect(notif_system.clear_all_notifications)
    trigger_layout.addWidget(btn4)

    trigger_group.setLayout(trigger_layout)
    layout.addWidget(trigger_group)

    # Status
    status = QLabel("Ready")
    status.setAlignment(Qt.AlignmentFlag.AlignCenter)
    status.setStyleSheet("color: #FF6B35; font-size: 10px;")
    layout.addWidget(status)

    layout.addStretch()
    demo.setLayout(layout)

    # Auto-cycle
    def auto_cycle():
        if not notif_system.settings.do_not_disturb:
            rand = random.random()
            if rand < 0.6:
                show_random()
            elif rand < 0.85:
                show_double()
            else:
                show_triple()

    timer = QTimer()
    timer.timeout.connect(auto_cycle)
    timer.start(10000)

    QTimer.singleShot(1000, show_random)

    demo.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run_demo()