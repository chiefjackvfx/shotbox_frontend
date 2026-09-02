"""Quick Look-style still and video previews for NukeDash shot cards."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
import weakref

from PyQt6.QtCore import QEvent, QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QKeySequence, QMouseEvent, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PyQt6.QtMultimediaWidgets import QVideoWidget

    HAS_MULTIMEDIA = True
except ImportError:
    QAudioOutput = None
    QMediaPlayer = None
    QVideoWidget = None
    HAS_MULTIMEDIA = False


@dataclass(frozen=True)
class QuickViewMedia:
    """A snapshot of the media needed to preview one shot."""

    title: str
    filename: str
    video_path: str | None = None
    thumbnail: QPixmap | None = None

    @property
    def is_previewable(self) -> bool:
        has_video = bool(self.video_path and Path(self.video_path).is_file())
        has_still = self.thumbnail is not None and not self.thumbnail.isNull()
        return bool(has_video or has_still)


class SeekSlider(QSlider):
    """A slider that seeks directly on click and while dragging."""

    def _position_value(self, event: QMouseEvent) -> int:
        if self.orientation() == Qt.Orientation.Horizontal:
            pixel = round(event.position().x())
            span = self.width()
        else:
            pixel = round(event.position().y())
            span = self.height()
        pixel = min(max(0, pixel), max(1, span))
        return self.style().sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            pixel,
            max(1, span),
            self.invertedAppearance(),
        )

    def _move_to_event(self, event: QMouseEvent) -> None:
        value = self._position_value(event)
        self.setSliderPosition(value)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setSliderDown(True)
        self._move_to_event(event)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.isSliderDown():
            super().mouseMoveEvent(event)
            return
        self._move_to_event(event)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.isSliderDown():
            super().mouseReleaseEvent(event)
            return
        self._move_to_event(event)
        self.setSliderDown(False)
        event.accept()


class QuickViewPopup(QWidget):
    """Frameless, modeless media popup with lightweight playback controls."""

    dismissed = pyqtSignal()
    navigation_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("quick_view_popup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._media: QuickViewMedia | None = None
        self._original_thumbnail: QPixmap | None = None
        self._pending_position_ms = 0
        self._slider_dragging = False
        self._resume_after_scrub = False
        self._is_video = False
        self._session_visible = False

        self._build_ui()
        self._build_player()
        self._build_shortcuts()
        self._apply_style()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self._panel = QFrame(self)
        self._panel.setObjectName("quick_view_panel")
        root.addWidget(self._panel)

        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(14, 10, 14, 12)
        panel_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        title_column = QVBoxLayout()
        title_column.setSpacing(1)
        self.title_label = QLabel("Quick View")
        self.title_label.setObjectName("quick_view_title")
        self.filename_label = QLabel("")
        self.filename_label.setObjectName("quick_view_filename")
        title_column.addWidget(self.title_label)
        title_column.addWidget(self.filename_label)
        header.addLayout(title_column, 1)

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("quick_view_close")
        self.close_button.setFixedSize(30, 30)
        self.close_button.setToolTip("Close Quick View (Space or Esc)")
        self.close_button.clicked.connect(self.hide)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        panel_layout.addLayout(header)

        self.media_stack = QStackedWidget()
        self.media_stack.setObjectName("quick_view_media")
        self.media_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        self.image_label = QLabel("No preview available")
        self.image_label.setObjectName("quick_view_image")
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.media_stack.addWidget(self.image_label)
        panel_layout.addWidget(self.media_stack, 1)

        self.controls = QFrame()
        self.controls.setObjectName("quick_view_controls")
        controls_layout = QHBoxLayout(self.controls)
        controls_layout.setContentsMargins(8, 5, 8, 5)
        controls_layout.setSpacing(8)

        self.play_button = QPushButton("▶")
        self.play_button.setObjectName("quick_view_play")
        self.play_button.setFixedSize(32, 28)
        self.play_button.setToolTip("Play/Pause")
        self.play_button.clicked.connect(self._toggle_playback)
        controls_layout.addWidget(self.play_button)

        self.position_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.position_slider.setToolTip("Click or drag to seek")
        self.position_slider.sliderPressed.connect(self._on_slider_pressed)
        self.position_slider.sliderReleased.connect(self._on_slider_released)
        self.position_slider.sliderMoved.connect(self._on_slider_moved)
        controls_layout.addWidget(self.position_slider, 1)

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("quick_view_time")
        controls_layout.addWidget(self.time_label)

        self.mute_button = QPushButton("🔊")
        self.mute_button.setObjectName("quick_view_mute")
        self.mute_button.setFixedSize(36, 28)
        self.mute_button.setToolTip("Mute")
        self.mute_button.clicked.connect(self._toggle_mute)
        controls_layout.addWidget(self.mute_button)

        panel_layout.addWidget(self.controls)
        self.controls.hide()

    def _build_player(self) -> None:
        self.video_widget = None
        self.player = None
        self.audio_output = None
        if not HAS_MULTIMEDIA:
            return

        self.video_widget = QVideoWidget()
        self.video_widget.setObjectName("quick_view_video")
        self.video_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.media_stack.addWidget(self.video_widget)

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.5)
        self.audio_output.setMuted(False)
        self.player.setAudioOutput(self.audio_output)
        self.player.setVideoOutput(self.video_widget)
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.positionChanged.connect(self._on_position_changed)
        self.player.durationChanged.connect(self._on_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.player.mediaStatusChanged.connect(self._on_media_status_changed)
        self.player.errorOccurred.connect(self._on_player_error)

    def _build_shortcuts(self) -> None:
        self._close_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._close_space.setContext(Qt.ShortcutContext.WindowShortcut)
        self._close_space.setAutoRepeat(False)
        self._close_space.activated.connect(self.hide)

        self._close_escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._close_escape.setContext(Qt.ShortcutContext.WindowShortcut)
        self._close_escape.setAutoRepeat(False)
        self._close_escape.activated.connect(self.hide)

        self._previous_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._previous_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._previous_shortcut.setAutoRepeat(False)
        self._previous_shortcut.activated.connect(
            lambda: self.navigation_requested.emit(-1)
        )

        self._next_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self._next_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._next_shortcut.setAutoRepeat(False)
        self._next_shortcut.activated.connect(
            lambda: self.navigation_requested.emit(1)
        )

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget#quick_view_popup {
                background: transparent;
            }
            QFrame#quick_view_panel {
                background-color: #17191c;
                border: 1px solid #4c5057;
                border-radius: 10px;
            }
            QLabel#quick_view_title {
                color: #f2f3f5;
                font-size: 16px;
                font-weight: 600;
            }
            QLabel#quick_view_filename, QLabel#quick_view_time {
                color: #aeb3bb;
                font-size: 11px;
            }
            QStackedWidget#quick_view_media, QLabel#quick_view_image {
                background-color: #08090a;
                color: #8f949b;
                border-radius: 5px;
            }
            QFrame#quick_view_controls {
                background-color: #24272b;
                border-radius: 5px;
            }
            QPushButton#quick_view_close, QPushButton#quick_view_play,
            QPushButton#quick_view_mute {
                background-color: #30343a;
                color: #f2f3f5;
                border: 1px solid #4a4f57;
                border-radius: 5px;
            }
            QPushButton#quick_view_close:hover, QPushButton#quick_view_play:hover,
            QPushButton#quick_view_mute:hover {
                background-color: #454b54;
            }
            QSlider::groove:horizontal {
                height: 5px;
                background: #4a4f57;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                background: #d8dbe0;
                width: 13px;
                margin: -4px 0;
                border-radius: 6px;
            }
            """
        )

    def show_media(self, media: QuickViewMedia, position_ms: int = 0, screen=None) -> None:
        self._media = media
        self._original_thumbnail = (
            QPixmap(media.thumbnail) if media.thumbnail is not None else None
        )
        self._pending_position_ms = max(0, int(position_ms))
        self.title_label.setText(media.title or "Untitled Shot")
        self.filename_label.setText(media.filename or "Thumbnail")

        video_path = str(media.video_path or "")
        if HAS_MULTIMEDIA and self.player is not None and video_path and Path(video_path).is_file():
            self._show_video(video_path)
        else:
            self._show_thumbnail()

        self._position_on_screen(screen)
        self._session_visible = True
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.PopupFocusReason)

    def _show_video(self, video_path: str) -> None:
        self._is_video = True
        self._slider_dragging = False
        self._resume_after_scrub = False
        if self.audio_output is not None:
            self.audio_output.setVolume(0.5)
            self.audio_output.setMuted(False)
            self.mute_button.setText("🔊")
            self.mute_button.setToolTip("Mute")
        self.controls.show()
        self.position_slider.setRange(0, 0)
        self.position_slider.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        self.media_stack.setCurrentWidget(self.video_widget)
        self.player.setSource(QUrl.fromLocalFile(video_path))
        if self._pending_position_ms:
            self.player.setPosition(self._pending_position_ms)
        self.player.play()

    def _show_thumbnail(self, unavailable_text: str | None = None) -> None:
        self._is_video = False
        self._slider_dragging = False
        self._resume_after_scrub = False
        if self.player is not None:
            self.player.stop()
            self.player.setSource(QUrl())
        self.controls.hide()
        self.media_stack.setCurrentWidget(self.image_label)
        if self._original_thumbnail is not None and not self._original_thumbnail.isNull():
            self.image_label.setText("")
            self.image_label.setToolTip(unavailable_text or "")
            self._scale_thumbnail()
        else:
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(unavailable_text or "No preview available")

    def _position_on_screen(self, screen) -> None:
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(1280, max(1, int(available.width() * 0.70)))
        height = min(800, max(1, int(available.height() * 0.70)))
        self.resize(width, height)
        self.move(
            available.x() + (available.width() - width) // 2,
            available.y() + (available.height() - height) // 2,
        )

    def current_position(self) -> int:
        if self._is_video and self.player is not None:
            return max(0, int(self.player.position()))
        return 0

    def _toggle_playback(self) -> None:
        if not self._is_video or self.player is None:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _toggle_mute(self) -> None:
        if self.audio_output is None:
            return
        muted = not self.audio_output.isMuted()
        self.audio_output.setMuted(muted)
        self.mute_button.setText("🔇" if muted else "🔊")
        self.mute_button.setToolTip("Unmute" if muted else "Mute")

    def _on_slider_pressed(self) -> None:
        self._slider_dragging = True
        self._resume_after_scrub = bool(
            HAS_MULTIMEDIA
            and self.player is not None
            and self.player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        if self._resume_after_scrub:
            self.player.pause()

    def _on_slider_released(self) -> None:
        if self.player is not None:
            self.player.setPosition(self.position_slider.value())
        self._slider_dragging = False
        if self._resume_after_scrub and self.player is not None:
            self.player.play()
        self._resume_after_scrub = False

    def _on_slider_moved(self, position: int) -> None:
        duration = self.player.duration() if self.player is not None else 0
        self.time_label.setText(
            f"{self._format_time(position)} / {self._format_time(duration)}"
        )
        if self.player is not None:
            self.player.setPosition(max(0, int(position)))

    def _on_position_changed(self, position: int) -> None:
        if self._slider_dragging:
            return
        self.position_slider.setValue(max(0, int(position)))
        duration = self.player.duration() if self.player is not None else 0
        self.time_label.setText(
            f"{self._format_time(position)} / {self._format_time(duration)}"
        )

    def _on_duration_changed(self, duration: int) -> None:
        self.position_slider.setRange(0, max(0, int(duration)))
        if self._pending_position_ms:
            self.player.setPosition(min(self._pending_position_ms, max(0, int(duration))))
            self._pending_position_ms = 0
        position = self.player.position() if self.player is not None else 0
        self.time_label.setText(
            f"{self._format_time(position)} / {self._format_time(duration)}"
        )

    def _on_playback_state_changed(self, state) -> None:
        if not HAS_MULTIMEDIA:
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setText("❚❚" if playing else "▶")
        self.play_button.setToolTip("Pause" if playing else "Play")

    def _on_media_status_changed(self, status) -> None:
        if not HAS_MULTIMEDIA or self.player is None:
            return
        if status in (
            QMediaPlayer.MediaStatus.InvalidMedia,
            QMediaPlayer.MediaStatus.NoMedia,
        ) and self._is_video:
            self._show_thumbnail("Video unavailable; showing thumbnail")

    def _on_player_error(self, *_args) -> None:
        if self._is_video:
            self._show_thumbnail("Video unavailable; showing thumbnail")

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        total_seconds = max(0, int(milliseconds)) // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _scale_thumbnail(self) -> None:
        if self._original_thumbnail is None or self._original_thumbnail.isNull():
            return
        target = self.image_label.contentsRect().size()
        if target.width() <= 0 or target.height() <= 0:
            return
        self.image_label.setPixmap(
            self._original_thumbnail.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._is_video:
            self._scale_thumbnail()

    def hideEvent(self, event) -> None:
        should_emit = self._session_visible
        self._session_visible = False
        if self.player is not None:
            self.player.pause()
        super().hideEvent(event)
        if should_emit:
            self.dismissed.emit()


class QuickViewController(QObject):
    """Coordinates hovered cards, popup lifetime, and timeline navigation."""

    def __init__(
        self,
        parent=None,
        cards_provider: Callable[[], Iterable[object]] | None = None,
        popup_factory: Callable[[], QuickViewPopup] | None = None,
    ):
        super().__init__(parent)
        self._cards_provider = cards_provider or (lambda: ())
        popup_parent = parent if isinstance(parent, QWidget) else None
        self._popup_factory = popup_factory or (
            lambda: QuickViewPopup(parent=popup_parent)
        )
        self._popup: QuickViewPopup | None = None
        self._hovered_ref: weakref.ReferenceType | None = None
        self._hovered_card_id: int | None = None
        self._active_ref: weakref.ReferenceType | None = None
        self._active_card_id: int | None = None
        self._touched: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
        self._tracked_card_ids: set[int] = set()
        self._closing = False

    @property
    def is_open(self) -> bool:
        return bool(self._popup is not None and self._popup.isVisible())

    def set_hovered_card(self, card) -> None:
        self._hovered_ref = weakref.ref(card) if card is not None else None
        self._hovered_card_id = id(card) if card is not None else None

    def clear_hovered_card(self, card) -> None:
        if self.hovered_card() is card:
            self._hovered_ref = None
            self._hovered_card_id = None

    def hovered_card(self):
        return self._hovered_ref() if self._hovered_ref is not None else None

    def active_card(self):
        return self._active_ref() if self._active_ref is not None else None

    def toggle(self) -> bool:
        if self.is_open:
            self.dismiss()
            return True
        card = self.hovered_card()
        if card is None:
            return False
        return self.open_card(card)

    def open_card(self, card) -> bool:
        media = self._safe_media(card)
        if media is None or not media.is_previewable:
            return False

        self._track_card(card)
        popup = self._ensure_popup()
        position = self._touched.get(card)
        if position is None:
            position = self._begin_card(card)
        self._touched[card] = max(0, int(position or 0))
        self._active_ref = weakref.ref(card)
        self._active_card_id = id(card)
        screen = self._card_screen(card)
        popup.show_media(media, self._touched[card], screen)
        return True

    def navigate(self, direction: int) -> bool:
        if not self.is_open or direction not in (-1, 1):
            return False
        active = self.active_card()
        cards = self._eligible_cards()
        if active is None or active not in cards:
            self.dismiss()
            return False
        target_index = cards.index(active) + direction
        if target_index < 0 or target_index >= len(cards):
            return False

        self._remember_active_position()
        return self.open_card(cards[target_index])

    def dismiss(self) -> None:
        if self._closing:
            return
        if self._popup is not None and self._popup.isVisible():
            self._popup.hide()
            return
        self._finish_session()

    def _ensure_popup(self) -> QuickViewPopup:
        if self._popup is None:
            self._popup = self._popup_factory()
            self._popup.dismissed.connect(self._on_popup_dismissed)
            self._popup.navigation_requested.connect(self.navigate)
        return self._popup

    def _on_popup_dismissed(self) -> None:
        self._finish_session()

    def _finish_session(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            self._remember_active_position()
            hovered = self.hovered_card()
            for card, position in list(self._touched.items()):
                try:
                    card.end_quick_view(
                        max(0, int(position or 0)),
                        resume=card is hovered,
                    )
                except (AttributeError, RuntimeError):
                    pass
            self._touched.clear()
            self._active_ref = None
            self._active_card_id = None
        finally:
            self._closing = False

    def _remember_active_position(self) -> None:
        active = self.active_card()
        if active is None or self._popup is None:
            return
        try:
            self._touched[active] = self._popup.current_position()
        except (RuntimeError, TypeError):
            pass

    def _eligible_cards(self) -> list:
        cards = []
        try:
            candidates = list(self._cards_provider() or ())
        except Exception:
            candidates = []
        for card in candidates:
            media = self._safe_media(card)
            if media is not None and media.is_previewable:
                cards.append(card)
        return cards

    def _track_card(self, card) -> None:
        card_id = id(card)
        if card_id in self._tracked_card_ids:
            return
        self._tracked_card_ids.add(card_id)
        try:
            card.destroyed.connect(
                lambda *_args, tracked_id=card_id: self._on_card_destroyed(tracked_id)
            )
        except (AttributeError, RuntimeError):
            self._tracked_card_ids.discard(card_id)

    def _on_card_destroyed(self, card_id: int) -> None:
        self._tracked_card_ids.discard(card_id)
        if self._hovered_card_id == card_id:
            self._hovered_ref = None
            self._hovered_card_id = None
        if self._active_card_id == card_id:
            self.dismiss()

    @staticmethod
    def _safe_media(card) -> QuickViewMedia | None:
        try:
            return card.quick_view_media()
        except (AttributeError, RuntimeError):
            return None

    @staticmethod
    def _begin_card(card) -> int:
        try:
            return max(0, int(card.begin_quick_view() or 0))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 0

    @staticmethod
    def _card_screen(card):
        try:
            center = card.mapToGlobal(card.rect().center())
            return QApplication.screenAt(center) or card.screen()
        except (AttributeError, RuntimeError):
            return QApplication.primaryScreen()
