"""Frameless artist picker shared by desktop startup and the design preview."""


from PyQt6.QtCore import QEvent, QEventLoop, QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QVBoxLayout, QWidget,
)

from themes import set_widget_style, theme_colour


PICKER_WIDTH = 640
PICKER_HEIGHT = 380
DIAMETER = 100
GAP = 24


class ArtistCircle(QPushButton):
    def __init__(self, artist_id, username, name):
        words = (name.strip() or username).split()
        initials = words[0][0] + (words[-1][0] if len(words) > 1 else "")
        super().__init__(initials.upper())
        self.artist_id = artist_id
        self.display_name = name or username
        self.setAccessibleName(f"{name or username} ({username})")
        self.setToolTip(self.accessibleName())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(DIAMETER + 10, DIAMETER + 10)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCheckable(True)
        self.setAutoDefault(True)

    def hitButton(self, point):
        offset = QPointF(point) - QRectF(self.rect()).center()
        return QPointF.dotProduct(offset, offset) <= (DIAMETER / 2) ** 2

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        disc = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        selected = self.isChecked()
        accent = QColor(theme_colour("#ff6b35"))
        fill = accent if selected else QColor("#373737")
        ink = QColor(theme_colour("#1a1d23")) if selected else QColor("#d4d4d4")
        if self.underMouse():
            fill = fill.lighter(116)
        if self.isDown():
            fill = fill.darker(120)
        painter.setPen(QPen(accent if selected else QColor("#606060"), 1))
        painter.setBrush(fill)
        painter.drawEllipse(disc)
        if selected:
            painter.setPen(QPen(accent, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(disc.adjusted(-3, -3, 3, 3))
        if self.hasFocus():
            painter.setPen(QPen(ink, 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(disc.adjusted(3, 3, -3, -3))
        font = QFont(self.font())
        font.setPixelSize(32)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(ink)
        painter.drawText(disc, Qt.AlignmentFlag.AlignCenter, self.text())


class ProfileSplash(QDialog):
    def __init__(self, profiles, memory):
        super().__init__()
        self.artist_id = None
        self.selection_loop = None
        self.keep_open_after_choice = False
        self.settings = memory
        self.returning_artist = None
        self.hovered_artist = None
        self.showing_load_progress = False
        self.browse_timer = QTimer(self)
        self.browse_timer.setSingleShot(True)
        self.browse_timer.timeout.connect(self.update_browsing)
        self.setWindowTitle("Shotbox · Choose your profile")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setObjectName("LoadingDialog")
        set_widget_style(self, "QDialog#LoadingDialog { border: 0; }")
        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.setSpacing(0)

        self.title_bar = QFrame()
        self.title_bar.setObjectName("profile_title_bar")
        set_widget_style(self.title_bar, "QFrame#profile_title_bar { background: #252931; }")
        self.title_bar.installEventFilter(self)
        header = QHBoxLayout(self.title_bar)
        header.setContentsMargins(22, 10, 14, 10)
        header.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(QApplication.windowIcon().pixmap(34, 34))
        brand = QLabel("SHOTBOX")
        set_widget_style(brand, "QLabel { font-size: 20px; font-weight: bold; }")
        title = QLabel("Choose profile")
        set_widget_style(title, "QLabel { color: #adb4bf; font-size: 15px; }")
        for label in (logo, brand, title):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            header.addWidget(label)
        header.addStretch()
        close_button = QPushButton("×")
        close_button.setAccessibleName("Close profile picker")
        close_button.setToolTip("Close")
        close_button.setFixedSize(38, 38)
        close_button.setAutoDefault(False)
        close_button.clicked.connect(self.reject)
        set_widget_style(close_button, """
            QPushButton {
                background: transparent; border: 1px solid transparent;
                border-radius: 3px; padding: 0; font-size: 21px;
            }
            QPushButton:hover, QPushButton:pressed { background: #404754; }
            QPushButton:focus { border-color: #d4d4d4; }
        """)
        header.addWidget(close_button)
        window_layout.addWidget(self.title_bar)

        layout = QVBoxLayout()
        layout.setContentsMargins(32, 18, 32, 24)
        layout.setSpacing(8)
        window_layout.addLayout(layout, 1)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        roster = QWidget()
        roster.setObjectName("roster")
        row = QHBoxLayout(roster)
        row.setContentsMargins(0, 0, 0, 0)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.setSpacing(GAP)
        self.row = row
        self.scroll = scroll
        set_widget_style(scroll, "QScrollArea, QWidget#roster { background: #1e2228; border: 0; }")
        scroll.setWidget(roster)
        layout.addWidget(scroll, 1)
        self.browse_name = QLabel()
        self.browse_name.setTextFormat(Qt.TextFormat.PlainText)
        self.browse_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.browse_name.setWordWrap(True)
        self.browse_name.setMinimumHeight(self.browse_name.fontMetrics().height())
        set_widget_style(self.browse_name, "QLabel { color: #9ca3af; font-size: 15px; font-weight: normal; }")
        layout.addWidget(self.browse_name)
        self.progress = QProgressBar()
        self.progress.setAccessibleName("Startup progress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        status = QHBoxLayout()
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        set_widget_style(self.status_label, "QLabel { font-size: 15px; }")
        status.addWidget(self.status_label, 1)
        layout.addLayout(status)
        self.progress.setFixedHeight(8)
        layout.addWidget(self.progress)
        for control in (self.status_label, self.progress):
            control.setVisible(bool(self.returning_artist))
        self.auto_login = QCheckBox("Open last profile automatically after 3 seconds")
        self.auto_login.setChecked(self.settings.value("auto_login", True, type=bool))
        self.auto_login.toggled.connect(self.set_auto_login)
        layout.addWidget(self.auto_login)
        self.login_timer = QTimer(self)
        self.login_timer.setSingleShot(True)
        self.login_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.login_timer.setInterval(3000)
        self.login_timer.timeout.connect(lambda: self.choose(self.returning_artist.artist_id))
        self.retry_button = QPushButton("Retry")
        self.retry_button.hide()
        layout.addWidget(self.retry_button)
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(PICKER_WIDTH, available.width()), min(PICKER_HEIGHT, available.height()))
        self.resize(self.minimumSize())
        self.loading_logo = QLabel()
        self.loading_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_logo.setPixmap(QApplication.windowIcon().pixmap(80, 80))
        row.addWidget(self.loading_logo)
        if profiles is not None:
            self.populate_profiles(profiles)

    def populate_profiles(self, profiles):
        row = self.row
        row.removeWidget(self.loading_logo)
        self.loading_logo.hide()
        scroll = self.scroll
        roster = scroll.widget()
        last_id = self.settings.value("last_artist", "", type=str)
        for artist_id, username, name in profiles:
            circle = ArtistCircle(artist_id, username, name)
            circle.clicked.connect(lambda checked, user=artist_id: self.choose(user))
            circle.installEventFilter(self)
            row.addWidget(circle)
            if artist_id == last_id:
                circle.setChecked(True)
                self.returning_artist = circle
        if row.count():
            row.itemAt(0).widget().setFocus()
        if not row.count():
            row.addWidget(QLabel("No VFX artist profiles available."))
        scroll_height = roster.sizeHint().height()
        if roster.sizeHint().width() + 64 > self.width():
            scroll_height += scroll.horizontalScrollBar().sizeHint().height()
        scroll.setMinimumHeight(scroll_height)
        artist = self.returning_artist
        if artist:
            self.status_label.setText(f"Last used · {artist.display_name}")
            artist.setFocus()
        for control in (self.status_label, self.progress):
            control.setVisible(artist is not None)

        self.update_login_timer()

    def set_auto_login(self, enabled):
        self.settings.setValue("auto_login", enabled)
        self.update_login_timer()

    def update_login_timer(self):
        self.login_timer.stop()
        if (self.auto_login.isChecked() and self.returning_artist is not None
                and self.isVisible() and self.artist_id is None
                and (not self.keep_open_after_choice or self.selection_loop is not None)):
            self.login_timer.start()

    def set_load_progress(self, text, completed=0, total=0):
        self.progress.show()
        if not self.showing_load_progress:
            self.showing_load_progress = True
            self.progress.setTextVisible(False)
            overlay = QHBoxLayout(self.progress)
            overlay.setContentsMargins(4, 0, 4, 0)
            self.load_label = QLabel()
            set_widget_style(self.load_label, "QLabel { color: #ffffff; font-size: 14px; }")
            self.load_label.setTextFormat(Qt.TextFormat.PlainText)
            self.load_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            overlay.addWidget(self.load_label)
            self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.progress.setFixedHeight(32)
            set_widget_style(self.progress, """
                QProgressBar { color: #ffffff; background: #252931; text-align: center; }
                QProgressBar::chunk { background: #884225; }
            """)
        self.progress.setRange(0, total)
        self.progress.setValue(completed)
        metrics = self.load_label.fontMetrics()
        width = max(100, self.width() - 80)
        self.load_label.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, width))
        self.progress.setAccessibleName(text)
        self.progress.setToolTip(text)

    def eventFilter(self, watched, event):
        if watched is self.title_bar and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                window = self.windowHandle()
                if window is not None and window.startSystemMove():
                    return True
        if isinstance(watched, ArtistCircle):
            event_type = event.type()
            if event_type == QEvent.Type.Enter:
                self.hovered_artist = watched
            elif event_type == QEvent.Type.Leave and self.hovered_artist is watched:
                self.hovered_artist = None
            if event_type in (QEvent.Type.Enter, QEvent.Type.Leave,
                              QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                # Resolve hover and focus together after Qt finishes the transition.
                self.browse_timer.start(0)
        return super().eventFilter(watched, event)

    def update_browsing(self):
        if self.artist_id is not None or not self.isVisible():
            return
        focused = self.focusWidget()
        if not isinstance(focused, ArtistCircle):
            focused = None
        alternatives = [
            artist for artist in (self.hovered_artist, focused)
            if artist is not None and artist is not self.returning_artist
        ]
        self.browse_name.setText(alternatives[0].display_name if alternatives else "")

    def showEvent(self, event):
        super().showEvent(event)
        self.update_login_timer()

    def hideEvent(self, event):
        self.login_timer.stop()
        self.browse_timer.stop()
        self.hovered_artist = None
        super().hideEvent(event)

    def choose(self, artist_id):
        if self.artist_id is not None:
            return
        self.login_timer.stop()
        self.auto_login.hide()
        self.artist_id = artist_id
        if self.selection_loop is None and not self.keep_open_after_choice:
            self.accept()
            return
        for circle in self.findChildren(ArtistCircle):
            circle.setChecked(circle.artist_id == artist_id)
            circle.setEnabled(False)
            if circle.isChecked():
                self.status_label.setText(f"Opening as {circle.display_name}")
        self.browse_name.hide()
        self.status_label.show()
        self.progress.show()
        self.set_load_progress("Loading artist settings…")
        if self.selection_loop is not None:
            self.selection_loop.quit()

    def select_for_startup(self):
        """Return the selection while keeping this window on screen for loading."""
        if self.artist_id is not None:
            return self.artist_id
        self.selection_loop = QEventLoop(self)
        self.rejected.connect(self.selection_loop.quit)
        self.show()
        self.update_browsing()
        self.update_login_timer()
        self.selection_loop.exec()
        self.selection_loop = None
        self.repaint()
        return self.artist_id
