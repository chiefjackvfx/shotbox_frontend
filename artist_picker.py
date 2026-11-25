"""Artist picker shared by desktop startup and the design preview."""


from functools import cache
from math import cos, hypot, pi, radians, sin
from random import Random
from zlib import crc32

from PyQt6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QEventLoop, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QConicalGradient, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QStyle, QVBoxLayout, QWidget,
)

from themes import set_widget_style, theme_colour


PICKER_WIDTH = 640
PICKER_HEIGHT = 380
DIAMETER = 100
TILE_WIDTH = DIAMETER + 18
TILE_HEIGHT = DIAMETER + 40
GAP = 24
LOGIN_MS = 3000


def motion_enabled(widget):
    style = widget.style()
    return (style.styleHint(QStyle.StyleHint.SH_Widget_Animate)
            and style.styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration) > 0)


@cache
def circle_grain():
    side = 128
    noise = Random(17).randbytes(side * side)
    tile = QImage(noise, side, side, side, QImage.Format.Format_Indexed8)
    palette = []
    for sample in range(256):
        delta = sample - 128
        tone = 255 if delta > 0 else 0
        palette.append(QColor(tone, tone, tone, abs(delta) // 36).rgba())
    tile.setColorTable(palette)
    return QBrush(tile)


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
        self.countdown_fraction = None
        self.highlight = QVariantAnimation(self)
        self.highlight.setStartValue(0.0)
        self.highlight.setEndValue(1.0)
        self.highlight.setDuration(160)
        self.highlight.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.highlight.valueChanged.connect(lambda _: self.update())
        colours = ("#514552", "#394f57", "#4c5041", "#494b62", "#59473c")
        self.fill = QColor(colours[crc32(username.encode()) % len(colours)]).darker(110)
        self.setFixedSize(TILE_WIDTH, TILE_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCheckable(True)
        self.setAutoDefault(True)

    def set_highlighted(self, active):
        direction = (QAbstractAnimation.Direction.Forward if active
                     else QAbstractAnimation.Direction.Backward)
        self.highlight.setDirection(direction)
        target = self.highlight.duration() if active else 0
        if not motion_enabled(self):
            self.highlight.stop()
            self.highlight.setCurrentTime(target)
        elif (self.highlight.currentTime() != target
              and self.highlight.state() != QAbstractAnimation.State.Running):
            self.highlight.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        emphasis = self.highlight.currentValue()
        lift = 2 * emphasis
        disc = QRectF((self.width() - DIAMETER) / 2, 8 - lift, DIAMETER, DIAMETER)
        selected = self.isChecked()
        accent = QColor(theme_colour("#ff6b35"))
        fill = self.fill.lighter(100 + round(27 * emphasis))
        ink = QColor("#d3cfc9").lighter(100 + round(17 * emphasis))
        if self.isDown():
            fill = fill.darker(120)
        painter.setPen(QPen(accent if selected else QColor("#606060"), 1))
        painter.setBrush(fill)
        painter.drawEllipse(disc)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(circle_grain())
        painter.drawEllipse(disc.adjusted(0.5, 0.5, -0.5, -0.5))
        if selected:
            painter.setPen(QPen(QColor("#62534d"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring = disc.adjusted(-4, -4, 4, 4)
            painter.drawEllipse(ring)
            if self.countdown_fraction is not None:
                painter.setPen(QPen(accent, 3, cap=Qt.PenCapStyle.RoundCap))
                painter.drawArc(ring, 90 * 16, -round(5760 * self.countdown_fraction))
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
        painter.setFont(self.font())
        name = painter.fontMetrics().elidedText(
            self.display_name, Qt.TextElideMode.ElideRight, self.width() - 4)
        painter.drawText(QRectF(0, DIAMETER + 12, self.width(), 26),
                         Qt.AlignmentFlag.AlignCenter, name)


class ProfileIris(QWidget):
    """Reveal the workspace through a native window mask or a clipped snapshot."""

    def __init__(self, window, picker, *, chromatic=False):
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        platform = QApplication.platformName()
        live_mask = platform == "xcb"
        in_picker = platform == "windows"
        # Capture before attaching the iris. On Windows render the rim inside
        # the picker, avoiding a synchronous grab of native dashboard children.
        capture_dashboard = not in_picker and (chromatic or not live_mask)
        source = window.grab() if capture_dashboard else None
        parent = window if live_mask else picker
        super().__init__(parent, Qt.WindowType.Widget if live_mask or in_picker else flags)
        self.in_picker = in_picker
        self.chromatic = chromatic
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, not live_mask and not self.in_picker)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet("background: transparent;")
        self.window_to_reveal = window
        self.live_mask = live_mask
        self.snapshot = None if live_mask else source
        self.chromatic_channels = self.split_channels(source) if chromatic and source is not None else ()
        self.original_mask = window.mask()
        self.picker = picker
        self.place_over_workspace()
        self.origin_in_picker = QPointF(picker.rect().center())
        for circle in picker.findChildren(ArtistCircle):
            if circle.artist_id == picker.artist_id and circle.visibleRegion():
                center = QPointF(circle.width() / 2, 8 + DIAMETER / 2
                                 - 2 * circle.highlight.currentValue())
                self.origin_in_picker = circle.mapTo(picker, center)
                break
        self.accent = QColor(theme_colour("#ff6b35"))
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(1.0)
        self.animation.setDuration(620)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.animation.valueChanged.connect(self.advance_iris)
        picker.rejected.connect(self.animation.stop)
        self.animation.finished.connect(self.finish_animation)

    def place_over_workspace(self):
        window = self.window_to_reveal
        picker = self.picker
        if self.in_picker:
            self.setGeometry(picker.rect())
            self.workspace = QRectF(self.rect())
            self.picker_offset = QPointF()
            return
        workspace = QRect(window.mapToGlobal(QPoint()), window.size())
        area = workspace if self.live_mask else workspace.united(QRect(picker.mapToGlobal(QPoint()), picker.size()))
        self.setGeometry(window.rect() if self.live_mask else area)
        self.workspace = QRectF(workspace.translated(-area.topLeft()))
        self.picker_offset = QPointF(picker.mapToGlobal(QPoint()) - area.topLeft())

    @property
    def origin(self):
        return self.picker_offset + self.origin_in_picker

    def inner_radius(self, progress):
        center = self.origin
        corners = (self.workspace.topLeft(), self.workspace.topRight(),
                   self.workspace.bottomLeft(), self.workspace.bottomRight())
        return (max(hypot(p.x() - center.x(), p.y() - center.y()) for p in corners) + 12) * progress

    def start(self):
        if self.live_mask:
            # An empty QRegion removes the mask. An off-window pixel hides
            # the client while the window manager assigns its final position.
            self.window_to_reveal.setMask(QRegion(-2, -2, 1, 1))
            self.setMask(QRegion(-2, -2, 1, 1))
            self.window_to_reveal.show()
            QTimer.singleShot(0, self.begin_animation)
        else:
            self.begin_animation()

    def begin_animation(self):
        if not self.picker.isVisible():
            return
        if self.live_mask:
            self.place_over_workspace()
            self.clearMask()
            self.raise_()
        self.show()
        self.animation.start()

    def advance_iris(self, progress):
        if self.live_mask:
            self.place_over_workspace()
            center = self.origin - self.workspace.topLeft()
            radius = self.inner_radius(progress)
            # Include the chosen rim in the native window shape.
            if not self.chromatic:
                radius /= 0.95
                radius += min(radius * 0.32, 40.0) * (1 - progress) * 0.6 + 2
            else:
                radius += min(radius * 0.16, 8 * sin(pi * progress)) * 1.03 + 2
            rect = QRectF(center.x() - radius, center.y() - radius, 2 * radius, 2 * radius)
            aperture = QRegion(rect.toAlignedRect(), QRegion.RegionType.Ellipse)
            if not self.original_mask.isEmpty():
                aperture &= self.original_mask
            self.window_to_reveal.setMask(aperture if not aperture.isEmpty() else QRegion(-2, -2, 1, 1))
        self.update()

    def finish_animation(self):
        if self.live_mask:
            self.window_to_reveal.setMask(self.original_mask)
            self.hide()
            QTimer.singleShot(250, Qt.TimerType.PreciseTimer, self.picker.accept)
            self.deleteLater()
        else:
            self.show_workspace()

    def show_workspace(self):
        # Submit the clean final frame before mapping the native main window.
        if self.in_picker:
            self.window_to_reveal.show()
            self.picker.accept()
        else:
            self.repaint()
            self.window_to_reveal.installEventFilter(self)
            self.window_to_reveal.show()

    def eventFilter(self, watched, event):
        if watched is self.window_to_reveal and event.type() == QEvent.Type.Paint:
            watched.removeEventFilter(self)
            # Hold the final iris frame as the live dashboard settles.
            QTimer.singleShot(250, Qt.TimerType.PreciseTimer, self.parentWidget().accept)
        return super().eventFilter(watched, event)

    @staticmethod
    def split_channels(source):
        # Soften the capture once. The three textures can then refract at
        # different scales around the selected profile on every frame.
        size = source.deviceIndependentSize()
        soft = source.scaled(max(1, round(size.width() / 2)), max(1, round(size.height() / 2)),
                             Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        channels = []
        for colour in (QColor(235, 0, 0), QColor(0, 248, 0), QColor(0, 0, 255)):
            channel = soft.copy()
            with QPainter(channel) as tint:
                tint.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
                tint.fillRect(QRectF(QPointF(), channel.deviceIndependentSize()), colour)
            channels.append(channel)
        return channels

    def paint_chromatic_edge(self, painter, center, radius, progress):
        strength = sin(pi * progress)
        width = min(radius, 4 + 48 * strength)
        band = QPainterPath()
        band.addEllipse(center, radius, radius)
        band.addEllipse(center, radius - width, radius - width)
        painter.save()
        painter.setClipPath(band)
        painter.fillPath(band, Qt.GlobalColor.black)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        # Dispersion follows the radius, so opposite sides of the glass
        # split in opposite directions, including at the top and bottom.
        for channel, displacement in zip(self.chromatic_channels, (0, 9, 21)):
            zoom = 1 + min(0.24, displacement * strength / radius)
            target = QRectF(center + (self.workspace.topLeft() - center) * zoom,
                            self.workspace.size() * zoom)
            painter.drawPixmap(target, channel, QRectF(channel.rect()))
        painter.restore()

        bevel = QRadialGradient(center, radius)
        for depth, colour in ((0, QColor(16, 72, 194, 0)),
                              (0.25, QColor(29, 84, 211, 55)),
                              (0.64, QColor(45, 85, 219, 70)),
                              (0.82, QColor(112, 215, 255, 95)),
                              (0.95, QColor(8, 44, 143, 192)),
                              (1, QColor(93, 191, 255, 180))):
            bevel.setColorAt(1 - (1 - depth) * width / radius, colour)
        painter.fillPath(band, bevel)

        separation = min(radius * 0.16, 8 * strength)
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for shift, colour in ((-separation, "#c25aff"), (separation, "#32dfff")):
            origin = center + QPointF(shift, -shift * 0.24)
            painter.setPen(QPen(QColor(colour), 2))
            painter.drawEllipse(origin, radius, radius)

        # Two narrow reflections roll along opposite sides of the glass.
        light = QConicalGradient(center, 110 - 45 * progress)
        for stop, colour in ((0, "#6488cc"), (0.06, "#edffff"), (0.11, "#79caff"),
                             (0.30, "#254885"), (0.54, "#81bfff"), (0.58, "#ddfaff"),
                             (0.66, "#558bdd"), (0.84, "#263d82"), (1, "#6488cc")):
            light.setColorAt(stop, QColor(colour))
        painter.setPen(QPen(QBrush(light), 2))
        painter.drawEllipse(center, radius, radius)
        painter.restore()

    def paint_blades(self, painter, center, radius, progress):
        # Each leaf shares its swept seam with the next. Retract the tips
        # into the rim near the end so the last corners clear cleanly.
        depth = min(radius * 0.32, 40.0) * (1 - progress)
        outer = radius + depth * 0.6
        inner = radius - depth
        twist = radians(32)
        tip = QPointF(inner * cos(twist), -inner * sin(twist))
        next_tip = QPointF(inner * cos(twist + radians(60)),
                           -inner * sin(twist + radians(60)))
        leaf = QPainterPath()
        leaf.moveTo(outer, 0)
        leaf.arcTo(QRectF(-outer, -outer, outer * 2, outer * 2), 0, 60)
        leaf.lineTo(next_tip)
        leaf.quadTo(QPointF(radius * cos(twist + radians(30)),
                            -radius * sin(twist + radians(30))), tip)
        leaf.closeSubpath()
        painter.save()
        painter.translate(center)
        painter.rotate(-20 + 55 * progress)
        for index in range(6):
            shade = 49 + index % 3 * 8
            painter.fillPath(leaf, QColor(shade, shade + 3, shade + 7))
            painter.setPen(QPen(QColor(188, 188, 180, 110), 1))
            painter.drawLine(QPointF(outer, 0), tip)
            painter.rotate(-60)
        painter.restore()

    def paint_flare(self, painter, center, radius):
        phase = self.animation.currentTime() / self.animation.duration()
        angle = 210 - 290 * phase
        radians_angle = radians(angle)
        head = center + QPointF(radius * cos(radians_angle), -radius * sin(radians_angle))
        sweep = QConicalGradient(center, angle)
        sweep.setColorAt(0, QColor("#fff4cc"))
        sweep.setColorAt(0.035, QColor("#ffc160"))
        sweep.setColorAt(0.22, QColor(255, 92, 20, 0))
        sweep.setColorAt(0.985, QColor(255, 92, 20, 0))
        sweep.setColorAt(1, QColor("#fff4cc"))
        painter.save()
        strength = sin(pi * phase) ** 2
        for width, opacity in ((20, 0.14), (9, 0.34), (3, 1.0)):
            painter.setOpacity(strength * opacity)
            painter.setPen(QPen(QBrush(sweep), width))
            painter.drawEllipse(center, radius, radius)
        # Stretch a single warm glint horizontally as it rides the rim.
        painter.translate(head)
        painter.scale(1, 0.085)
        spread = 26 + 87 * strength
        flare = QRadialGradient(QPointF(), spread)
        flare.setColorAt(0, QColor("#fff9e4"))
        flare.setColorAt(0.12, QColor(255, 202, 108, 238))
        flare.setColorAt(0.46, QColor(255, 125, 48, 108))
        flare.setColorAt(1, QColor(255, 91, 25, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(flare)
        painter.drawEllipse(QPointF(), spread, spread)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = self.origin
        progress = self.animation.currentValue()
        if progress >= 1.0:
            if self.snapshot is not None:
                painter.drawPixmap(self.workspace.topLeft(), self.snapshot)
            return
        inner = self.inner_radius(progress)
        radius = inner / 0.95
        if self.snapshot is not None:
            mask = QPainterPath()
            mask.addEllipse(center, inner, inner)
            painter.save()
            painter.setClipPath(mask)
            painter.drawPixmap(self.workspace.topLeft(), self.snapshot)
            painter.restore()
        if inner > 0 and self.chromatic:
            self.paint_chromatic_edge(painter, center, inner, progress)
        elif radius > 0:
            self.paint_blades(painter, center, radius, progress)
            painter.setPen(QPen(self.accent, 3))
            painter.drawEllipse(center, radius, radius)
            self.paint_flare(painter, center, radius)
            # A finer trailing rim gives the aperture a ripple as it opens.
            painter.setOpacity(0.45 * (1 - progress))
            painter.setPen(QPen(self.accent, 1))
            painter.drawEllipse(center, inner, inner)


class ProfileSplash(QDialog):
    profile_chosen = pyqtSignal(str)

    def __init__(self, profiles, memory):
        super().__init__()
        self.artist_id = None
        self.selection_loop = None
        self.keep_open_after_choice = False
        self.waiting_for_choice = False
        self.settings = memory
        self.returning_artist = None
        self.hovered_artist = None
        self.showing_load_progress = False
        self.browse_timer = QTimer(self)
        self.browse_timer.setSingleShot(True)
        self.browse_timer.timeout.connect(self.update_browsing)
        self.setWindowTitle("Shotbox · Choose your profile")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
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
            QPushButton { background: transparent; border: 1px solid transparent;
                          border-radius: 3px; padding: 0; font-size: 21px; }
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
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        roster = QWidget()
        roster.setObjectName("roster")
        self.roster_layout = QVBoxLayout(roster)
        self.roster_layout.setContentsMargins(0, 0, 0, 0)
        self.roster_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.roster_layout.setSpacing(8)
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
        self.remaining_ms = LOGIN_MS
        self.ring_timer = QTimer(self)
        self.ring_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.ring_timer.timeout.connect(self.refresh_ring)
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
        self.roster_layout.addWidget(self.loading_logo)
        if profiles is not None:
            self.populate_profiles(profiles)

    def populate_profiles(self, profiles):
        self.roster_layout.removeWidget(self.loading_logo)
        self.loading_logo.hide()
        profiles = list(profiles)
        available = self.screen().availableGeometry()
        # Allow space for a vertical scrollbar on unusually large rosters.
        bar_width = self.scroll.verticalScrollBar().sizeHint().width()
        max_columns = max(1, (available.width() - 64 - bar_width + GAP) // (TILE_WIDTH + GAP))
        preferred_columns = len(profiles) if len(profiles) <= 4 else (len(profiles) + 1) // 2
        columns = max(1, min(preferred_columns, max_columns))
        last_id = self.settings.value("last_artist", "", type=str)
        for index, (artist_id, username, name) in enumerate(profiles):
            if index % columns == 0:
                row = QHBoxLayout()
                row.setSpacing(GAP)
                row.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.roster_layout.addLayout(row)
            circle = ArtistCircle(artist_id, username, name)
            circle.clicked.connect(lambda checked, user=artist_id: self.choose(user))
            circle.installEventFilter(self)
            row.addWidget(circle)
            if index == 0:
                circle.setFocus()
            if artist_id == last_id:
                circle.setChecked(True)
                self.returning_artist = circle
        if not profiles:
            self.roster_layout.addWidget(QLabel("No VFX artist profiles available."))
        rows = (len(profiles) + columns - 1) // columns
        content_height = rows * TILE_HEIGHT + max(0, rows - 1) * self.roster_layout.spacing()
        content_width = columns * TILE_WIDTH + (columns - 1) * GAP
        # Reserve room for the loading message, progress and retry controls.
        roster_height = min(content_height, max(1, available.height() - 250))
        self.scroll.setMinimumHeight(roster_height)
        width = content_width + 64 + (bar_width if roster_height < content_height else 0)
        self.setMinimumWidth(min(available.width(), max(self.minimumWidth(), width)))
        self.resize(self.minimumWidth(), min(available.height(), max(self.height(), PICKER_HEIGHT + roster_height - TILE_HEIGHT)))
        if self.isVisible():
            self.move(max(available.left(), min(self.x(), available.right() + 1 - self.width())),
                      max(available.top(), min(self.y(), available.bottom() + 1 - self.height())))
        artist = self.returning_artist
        if artist:
            artist.setFocus()

        self.update_login_timer()

    def set_auto_login(self, enabled):
        self.settings.setValue("auto_login", enabled)
        self.update_login_timer()

    def update_login_timer(self):
        artist = self.returning_artist
        if artist is None:
            return
        enabled = (self.auto_login.isChecked() and self.isVisible() and self.artist_id is None
                   and (not self.keep_open_after_choice or self.waiting_for_choice or self.selection_loop is not None))
        browsing = any(isinstance(item, ArtistCircle) and item is not artist
                       for item in (self.hovered_artist, self.focusWidget()))
        if enabled and not browsing:
            if not self.login_timer.isActive():
                self.login_timer.start(self.remaining_ms)
                refresh_hz = self.screen().refreshRate() or 60
                frame_ms = max(1, round(1000 / refresh_hz))
                self.ring_timer.start(frame_ms if motion_enabled(self) else 1000)
            self.browse_name.setText(f"Opening as {artist.display_name}…")
        else:
            if self.login_timer.isActive():
                self.remaining_ms = max(1, self.login_timer.remainingTime())
            self.login_timer.stop()
            self.ring_timer.stop()
            if not enabled:
                self.remaining_ms = LOGIN_MS
            self.browse_name.setText("Auto-open paused" if enabled
                                     else f"Last used · {artist.display_name}")
        self.refresh_ring()

    def refresh_ring(self):
        artist = self.returning_artist
        remaining = self.login_timer.remainingTime()
        if remaining < 0:
            remaining = self.remaining_ms
        artist.countdown_fraction = (1 - remaining / LOGIN_MS
                                     if self.auto_login.isChecked() else None)
        artist.update()

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
                handle = self.windowHandle()
                if handle and handle.startSystemMove():
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
        for circle in self.findChildren(ArtistCircle):
            circle.set_highlighted(circle is self.hovered_artist or circle is focused)
        self.update_login_timer()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_login_timer()

    def hideEvent(self, event):
        self.login_timer.stop()
        self.ring_timer.stop()
        self.remaining_ms = LOGIN_MS
        self.browse_timer.stop()
        self.hovered_artist = None
        super().hideEvent(event)

    def choose(self, artist_id):
        if self.artist_id is not None:
            return
        self.login_timer.stop()
        self.ring_timer.stop()
        if self.returning_artist:
            self.returning_artist.countdown_fraction = None
            self.returning_artist.update()
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
        self.profile_chosen.emit(artist_id)
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
        self.selection_loop.exec()
        self.selection_loop = None
        self.repaint()
        return self.artist_id
