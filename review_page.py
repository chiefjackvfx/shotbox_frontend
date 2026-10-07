# review_page.py
"""
ShotBox Review Page

A video review and annotation system with:
- Video playback with Nuke-style controls
- Drawing annotations (circle, box, arrow, freehand, text)
- Task list sidebar
- Navigation between shots

Structure:
- ReviewPageUI: Widget creation and layout
- ReviewPageLogic: Signals, state management, playback control
- ReviewPage: Main widget combining UI and logic
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QSlider, QSpinBox, QDoubleSpinBox, QComboBox,
    QScrollArea, QSplitter, QToolButton, QButtonGroup,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QColorDialog, QSpacerItem, QStackedWidget, QInputDialog, QApplication, QDialog,
    QAbstractSpinBox, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, QThreadPool, pyqtSignal, pyqtSlot, QSize, QPoint, QRect, QEvent
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPixmap, QImage, QRegion,
    QMouseEvent, QPaintEvent, QResizeEvent, QKeySequence, QShortcut
)
from pathlib import Path
import re
import time
from collections import deque
import av
import numpy as np
import http_help
import filesIO
from widgets import TaskWidget
from task_create_dialog import TaskCreateDialog
from review_icons import review_icon, set_review_button_icon
from review_media import (EXRSequenceReader, ReviewFrameTask, MovieCacheReader,
                          discover_renders, movie_display_image)
from review_ocio import OCIOPanel
from review_session import ReviewSessionBar, ReviewPresentationWindow
from presentation_widgets import FlexibleLabel
from readable_combo_box import ReadableComboBox


# =============================================================================
# ANNOTATION CANVAS - Overlay for drawing on video
# =============================================================================

class AnnotationCanvas(QWidget):
    """
    Transparent overlay widget for drawing annotations on top of video.
    Supports: circle, rectangle, arrow, freehand, text
    """
    
    # Annotation modes
    MODE_NONE = "none"
    MODE_CIRCLE = "circle"
    MODE_RECTANGLE = "rectangle"
    MODE_ARROW = "arrow"
    MODE_FREEHAND = "freehand"
    MODE_TEXT = "text"
    SCOPE_FRAME = "frame"
    SCOPE_RANGE = "range"
    SCOPE_FULL = "full"
    
    annotation_changed = pyqtSignal()  # Emitted when annotations are modified
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        
        # Current drawing state
        self.current_mode = self.MODE_NONE
        self.current_color = QColor(255, 100, 100)  # Default red
        self.current_thickness = 3
        self.scope_mode = self.SCOPE_FRAME
        self.scope_range_start = 0
        self.scope_range_end = 0
        
        # Drawing in progress
        self.is_drawing = False
        self.draw_start_point = QPoint()
        self.draw_current_point = QPoint()
        self.freehand_points = []  # For freehand mode
        
        # Completed annotations for all frames/scopes
        # Each annotation: {"type": str, "color": QColor, "thickness": int, "data": ..., "scope": str}
        self.annotations_all = []
        self.annotations = []
        self.current_frame = 0
    
    def set_mode(self, mode: str):
        """Set the current annotation mode."""
        self.current_mode = mode
        if mode == self.MODE_NONE:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
    
    def set_color(self, color: QColor):
        """Set the current drawing color."""
        self.current_color = color
    
    def set_thickness(self, thickness: int):
        """Set the current line thickness."""
        self.current_thickness = thickness

    def set_scope_mode(self, scope: str):
        """Set the current annotation scope."""
        self.scope_mode = scope

    def set_scope_range(self, start_frame: int, end_frame: int):
        """Set the annotation frame range for range scope."""
        if start_frame > end_frame:
            start_frame, end_frame = end_frame, start_frame
        self.scope_range_start = max(0, start_frame)
        self.scope_range_end = max(0, end_frame)
    
    def set_frame(self, frame_number: int):
        """Switch to a different frame's annotations."""
        self.current_frame = frame_number
        self._refresh_visible_annotations()
    
    def clear_current_frame(self):
        """Clear annotations in the current scope."""
        if self.scope_mode == self.SCOPE_FRAME:
            self.annotations_all = [
                ann for ann in self.annotations_all
                if not (ann.get("scope") == self.SCOPE_FRAME and ann.get("frame") == self.current_frame)
            ]
        elif self.scope_mode == self.SCOPE_RANGE:
            range_key = (self.scope_range_start, self.scope_range_end)
            self.annotations_all = [
                ann for ann in self.annotations_all
                if not (ann.get("scope") == self.SCOPE_RANGE and ann.get("range") == range_key)
            ]
        elif self.scope_mode == self.SCOPE_FULL:
            self.annotations_all = [
                ann for ann in self.annotations_all
                if ann.get("scope") != self.SCOPE_FULL
            ]
        self._refresh_visible_annotations()
        self.annotation_changed.emit()
    
    def clear_all_annotations(self):
        """Clear all annotations on all frames."""
        self.annotations_all = []
        self.annotations = []
        self.update()
        self.annotation_changed.emit()
    
    def undo_last_annotation(self):
        """Remove the last annotation on the current frame."""
        for index in range(len(self.annotations_all) - 1, -1, -1):
            ann = self.annotations_all[index]
            scope = ann.get("scope")
            if scope == self.SCOPE_FRAME:
                if ann.get("frame") == self.current_frame:
                    self.annotations_all.pop(index)
                    break
            elif scope == self.SCOPE_RANGE:
                if ann.get("range") == (self.scope_range_start, self.scope_range_end):
                    self.annotations_all.pop(index)
                    break
            elif scope == self.SCOPE_FULL:
                self.annotations_all.pop(index)
                break
        self._refresh_visible_annotations()
        self.annotation_changed.emit()
    
    def get_composite_image(self) -> QPixmap:
        """Get a pixmap of the current annotations (for saving as thumbnail)."""
        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self._draw_annotations(painter)
        painter.end()
        return pixmap
    
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.current_mode != self.MODE_NONE:
            self.is_drawing = True
            self.draw_start_point = event.pos()
            self.draw_current_point = event.pos()
            
            if self.current_mode == self.MODE_FREEHAND:
                self.freehand_points = [event.pos()]
            
            self.update()
    
    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_drawing:
            self.draw_current_point = event.pos()
            
            if self.current_mode == self.MODE_FREEHAND:
                self.freehand_points.append(event.pos())
            
            self.update()
    
    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drawing:
            self.draw_current_point = event.pos()
            self.is_drawing = False
            
            # Create annotation based on mode
            annotation = {
                "type": self.current_mode,
                "color": QColor(self.current_color),
                "thickness": self.current_thickness,
                "canvas_size": QSize(self.size()),
            }
            scope = self.scope_mode
            annotation["scope"] = scope
            if scope == self.SCOPE_FRAME:
                annotation["frame"] = self.current_frame
            elif scope == self.SCOPE_RANGE:
                start = min(self.scope_range_start, self.scope_range_end)
                end = max(self.scope_range_start, self.scope_range_end)
                annotation["range"] = (start, end)
            
            if self.current_mode == self.MODE_CIRCLE:
                annotation["data"] = {
                    "center": self.draw_start_point,
                    "radius_point": self.draw_current_point
                }
            elif self.current_mode == self.MODE_RECTANGLE:
                annotation["data"] = {
                    "top_left": self.draw_start_point,
                    "bottom_right": self.draw_current_point
                }
            elif self.current_mode == self.MODE_ARROW:
                annotation["data"] = {
                    "start": self.draw_start_point,
                    "end": self.draw_current_point
                }
            elif self.current_mode == self.MODE_FREEHAND:
                annotation["data"] = {
                    "points": self.freehand_points.copy()
                }
                self.freehand_points = []
            elif self.current_mode == self.MODE_TEXT:
                text, ok = QInputDialog.getText(self, "Add Note", "Text:")
                if ok and text.strip():
                    annotation["data"] = {
                        "position": self.draw_current_point,
                        "text": text.strip()
                    }
                    self.annotations_all.append(annotation)
                    self.annotation_changed.emit()
                    self._refresh_visible_annotations()
                return
            
            self.annotations_all.append(annotation)
            self.annotation_changed.emit()
            self._refresh_visible_annotations()

    def _refresh_visible_annotations(self):
        """Refresh visible annotations based on the current frame."""
        self.annotations = [
            ann for ann in self.annotations_all
            if self._annotation_visible_on_frame(ann, self.current_frame)
        ]
        self.update()

    def _annotation_visible_on_frame(self, annotation: dict, frame_number: int) -> bool:
        scope = annotation.get("scope", self.SCOPE_FRAME)
        if scope == self.SCOPE_FRAME:
            return annotation.get("frame") == frame_number
        if scope == self.SCOPE_RANGE:
            start, end = annotation.get("range", (frame_number, frame_number))
            return start <= frame_number <= end
        if scope == self.SCOPE_FULL:
            return True
        return False
    
    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw completed annotations
        self._draw_annotations(painter)
        
        # Draw in-progress annotation
        if self.is_drawing:
            self._draw_in_progress(painter)
        
        painter.end()
    
    def _draw_annotations(self, painter: QPainter):
        """Draw all completed annotations."""
        for annotation in self.annotations:
            painter.save()
            original_size = annotation.get("canvas_size", self.size())
            if original_size.width() > 0 and original_size.height() > 0:
                painter.scale(self.width() / original_size.width(), self.height() / original_size.height())
            pen = QPen(annotation["color"])
            pen.setWidth(annotation["thickness"])
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            
            data = annotation["data"]
            annotation_type = annotation["type"]
            
            if annotation_type == self.MODE_CIRCLE:
                center = data["center"]
                radius_point = data["radius_point"]
                radius = int(((radius_point.x() - center.x())**2 + 
                             (radius_point.y() - center.y())**2)**0.5)
                painter.drawEllipse(center, radius, radius)
            
            elif annotation_type == self.MODE_RECTANGLE:
                rect = QRect(data["top_left"], data["bottom_right"])
                painter.drawRect(rect)
            
            elif annotation_type == self.MODE_ARROW:
                self._draw_arrow(painter, data["start"], data["end"])
            
            elif annotation_type == self.MODE_FREEHAND:
                points = data["points"]
                if len(points) > 1:
                    for i in range(len(points) - 1):
                        painter.drawLine(points[i], points[i + 1])
            
            elif annotation_type == self.MODE_TEXT:
                painter.drawText(data["position"], data["text"])
            painter.restore()
    
    def _draw_in_progress(self, painter: QPainter):
        """Draw the annotation currently being created."""
        pen = QPen(self.current_color)
        pen.setWidth(self.current_thickness)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        if self.current_mode == self.MODE_CIRCLE:
            center = self.draw_start_point
            radius = int(((self.draw_current_point.x() - center.x())**2 + 
                         (self.draw_current_point.y() - center.y())**2)**0.5)
            painter.drawEllipse(center, radius, radius)
        
        elif self.current_mode == self.MODE_RECTANGLE:
            rect = QRect(self.draw_start_point, self.draw_current_point)
            painter.drawRect(rect)
        
        elif self.current_mode == self.MODE_ARROW:
            self._draw_arrow(painter, self.draw_start_point, self.draw_current_point)
        
        elif self.current_mode == self.MODE_FREEHAND:
            if len(self.freehand_points) > 1:
                for i in range(len(self.freehand_points) - 1):
                    painter.drawLine(self.freehand_points[i], self.freehand_points[i + 1])
    
    def _draw_arrow(self, painter: QPainter, start: QPoint, end: QPoint):
        """Draw an arrow from start to end point."""
        painter.drawLine(start, end)
        
        # Calculate arrowhead
        import math
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        arrow_size = 15
        
        # Arrowhead points
        p1 = QPoint(
            int(end.x() - arrow_size * math.cos(angle - math.pi / 6)),
            int(end.y() - arrow_size * math.sin(angle - math.pi / 6))
        )
        p2 = QPoint(
            int(end.x() - arrow_size * math.cos(angle + math.pi / 6)),
            int(end.y() - arrow_size * math.sin(angle + math.pi / 6))
        )
        
        painter.drawLine(end, p1)
        painter.drawLine(end, p2)


# =============================================================================
# VIDEO VIEWER - Video widget with annotation overlay
# =============================================================================

class VideoFrameWidget(QWidget):
    """Widget that paints decoded video frames."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_image = QImage()
        self._click_origin = None
        self.setMinimumSize(320, 240)
        self.setToolTip("Click to play/pause when no drawing tool is selected")

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_origin = event.position().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._click_origin is not None:
            origin = self._click_origin
            self._click_origin = None
            if (event.position().toPoint() - origin).manhattanLength() < QApplication.startDragDistance():
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_frame_image(self, image: QImage):
        self._frame_image = image
        self.update()

    def current_frame_image(self) -> QImage:
        """Return the current decoded frame image."""
        if self._frame_image.isNull():
            return QImage()
        return self._frame_image.copy()

    def video_rect(self) -> QRect:
        """Return the target rect for the video, preserving aspect ratio."""
        if self._frame_image.isNull():
            return self.rect()
        scaled = self._frame_image.size().scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        return QRect(x, y, scaled.width(), scaled.height())

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(10, 10, 10))
        if not self._frame_image.isNull():
            target_rect = self.video_rect()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target_rect, self._frame_image)
        painter.end()


class PyAVFrameReader:
    """Decode frames from video files using PyAV with frame-accurate seeks."""

    def __init__(self):
        self.container = None
        self.stream = None
        self.fps = 24.0
        self.total_frames = 0
        self.duration_ms = 0
        self._current_frame_index = 0
        self._decoder_iter = None
        self._decoder_frame_index = None
        self._video_stream_index = None

    def open(self, file_path: str):
        self.close()
        self.container = av.open(file_path)
        self.stream = next((s for s in self.container.streams if s.type == "video"), None)
        if self.stream is None:
            raise ValueError("No video stream found")
        # Let FFmpeg distribute high-resolution movie decoding across cores.
        self.stream.thread_type = "AUTO"

        rate = self.stream.average_rate or self.stream.base_rate
        if rate:
            self.fps = float(rate)
        else:
            self.fps = 24.0

        duration_sec = None
        if self.stream.duration is not None:
            duration_sec = float(self.stream.duration * self.stream.time_base)
        elif self.container.duration is not None:
            duration_sec = self.container.duration / av.time_base

        if duration_sec is not None:
            self.duration_ms = int(duration_sec * 1000)
        else:
            self.duration_ms = 0

        if self.stream.frames:
            self.total_frames = int(self.stream.frames)
        elif duration_sec is not None:
            self.total_frames = max(1, int(round(duration_sec * self.fps)))
        else:
            self.total_frames = 0

        self._current_frame_index = 0
        self._decoder_iter = None
        self._decoder_frame_index = None
        self._video_stream_index = self.stream.index

    def close(self):
        if self.container:
            self.container.close()
        self.container = None
        self.stream = None
        self._decoder_iter = None
        self._decoder_frame_index = None
        self._video_stream_index = None

    def _frame_to_pts(self, frame_index: int) -> int:
        if not self.stream or self.fps <= 0:
            return 0
        time_sec = frame_index / self.fps
        return int(time_sec / float(self.stream.time_base))

    def _pts_to_frame_index(self, pts):
        if pts is None or self.fps <= 0:
            if self._decoder_frame_index is None:
                return self._current_frame_index
            return self._decoder_frame_index + 1
        return int(round(float(pts * self.stream.time_base) * self.fps))

    def _reset_decoder(self, frame_index: int):
        if not self.container or not self.stream:
            return
        pts = self._frame_to_pts(frame_index)
        self.container.seek(pts, stream=self.stream, any_frame=False, backward=True)
        self._decoder_iter = self.container.decode(video=self._video_stream_index)
        self._decoder_frame_index = None

    def seek_to_frame(self, frame_index: int):
        if not self.container or not self.stream:
            return None, self._current_frame_index
        frame_index = max(0, frame_index)
        self._reset_decoder(frame_index)
        for frame in self._decoder_iter:
            index = self._pts_to_frame_index(frame.pts)
            self._decoder_frame_index = index
            if index >= frame_index:
                self._current_frame_index = index
                return frame, index
        return None, self._current_frame_index

    def decode_next(self):
        if not self.container or not self.stream:
            return None, self._current_frame_index
        if self._decoder_iter is None:
            return self.seek_to_frame(self._current_frame_index + 1)
        for frame in self._decoder_iter:
            index = self._pts_to_frame_index(frame.pts)
            self._decoder_frame_index = index
            if index > self._current_frame_index:
                self._current_frame_index = index
                return frame, index
        return None, self._current_frame_index

    @property
    def current_frame_index(self):
        return self._current_frame_index


class VideoViewerWidget(QWidget):
    """
    Combined video player and annotation canvas.
    Uses PyAV for frame-accurate decoding with an annotation overlay.
    """

    frame_changed = pyqtSignal(int)  # Emits current frame number
    duration_changed = pyqtSignal(int)  # Emits total frames
    playback_state_changed = pyqtSignal(bool)  # Emits is_playing
    frame_rate_changed = pyqtSignal(float)
    video_loaded = pyqtSignal(str)  # Emits file path when loaded
    error_occurred = pyqtSignal(str)  # Emits error message
    source_start_changed = pyqtSignal(int)
    playback_status_changed = pyqtSignal(str)
    playback_finished = pyqtSignal()
    cache_progress_changed = pyqtSignal(int, int, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setObjectName("review_video_viewer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Use a stacked layout approach - video at bottom, canvas on top
        # We'll manually position the canvas as an overlay
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Video widget - paints decoded frames
        self.video_widget = VideoFrameWidget()
        layout.addWidget(self.video_widget)

        # Annotation canvas - overlay on top of video widget
        self.annotation_canvas = AnnotationCanvas(self.video_widget)
        self.annotation_canvas.raise_()  # Ensure it's on top
        self.video_widget.clicked.connect(self._on_video_clicked)

        # PyAV decoder and playback timer
        self.reader = PyAVFrameReader()
        self.play_timer = QTimer(self)
        self.play_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.play_timer.timeout.connect(self._on_playback_tick)

        # Playback state
        self.frame_rate = 24.0
        self.total_frames = 0
        self.current_frame = 0
        self.duration_ms = 0
        self.current_video_path = ""
        self._is_playing = False
        self._play_direction = 1
        self._volume = 0.5
        self._showing_still = False
        self._frame_cache = {}
        self._cache_order = deque()
        self._cache_size = 100000
        self._cache_bytes = 0
        self._cache_byte_limit = 1024 * 1024 * 1024
        self._cache_reader = None
        self._cache_plan = []
        self._cache_frame_bytes = 0
        self._buffering = False
        self._cache_errors = {}
        self._last_cache_progress = None
        self._ocio_processor = None
        self._decode_scale = 1
        self._sequence_reader = None
        self._decode_generation = 0
        self._sequence_busy = False
        self._sequence_target = None
        self._sequence_tasks = {}
        self._sequence_worker_limit = 3
        self.source_start_frame = 0
        self._sequence_prefetch_failed = set()
        self._playback_times = deque(maxlen=24)

        # In/out points for play range (in frames)
        self.in_point = 0
        self.out_point = 0
        self.use_play_range = False

        # Looping
        self.loop_playback = True

        # Scrubbing
        self._scrub_active = False
        self._scrub_target_frame = None
        self._scrub_last_frame = None
        self._scrub_timer = QTimer(self)
        self._scrub_timer.setInterval(16)
        self._scrub_timer.timeout.connect(self._apply_scrub_target)

    def resizeEvent(self, event: QResizeEvent):
        """Keep annotation canvas sized to match video widget."""
        super().resizeEvent(event)
        self._update_canvas_geometry()

    def showEvent(self, event):
        """Ensure canvas is properly positioned when shown."""
        super().showEvent(event)
        self._update_canvas_geometry()

    def load_video(self, file_path: str, sequence_path=None, *, start_position_seconds=0.0,
                   source_start_frame=0):
        """Load a movie or a native EXR render sequence."""
        import os

        self.clear_media()

        if not file_path:
            print("[VideoViewer] No file path provided")
            return

        if not os.path.exists(file_path):
            error_msg = f"Video file not found: {file_path}"
            print(f"[VideoViewer] {error_msg}")
            self.error_occurred.emit(error_msg)
            return

        print(f"[VideoViewer] Loading: {file_path}")
        self.current_video_path = file_path
        self._showing_still = False

        try:
            if Path(file_path).suffix.lower() == ".exr":
                self._sequence_reader = EXRSequenceReader(file_path, fps=self.frame_rate,
                                                          sequence_path=sequence_path)
                self.reader = self._sequence_reader
                self._cache_reader = self.reader
            else:
                self.reader = PyAVFrameReader()
                self.reader.open(file_path)
                self._cache_reader = MovieCacheReader(file_path, PyAVFrameReader)
        except Exception as exc:
            self.reader.close()
            self._sequence_reader = None
            self._cache_reader = None
            self.current_video_path = ""
            error_msg = f"Failed to load video: {exc}"
            print(f"[VideoViewer] {error_msg}")
            self.error_occurred.emit(error_msg)
            return

        self.frame_rate = self.reader.fps
        self.source_start_frame = (self.reader.first_frame if self._sequence_reader is not None
                                   else source_start_frame)
        self.source_start_changed.emit(self.source_start_frame)
        self.total_frames = max(1, self.reader.total_frames)
        self.duration_ms = self.reader.duration_ms
        self.in_point = 0
        self.out_point = max(0, self.total_frames - 1)
        self._update_playback_interval()
        self.frame_rate_changed.emit(self.frame_rate)

        self.duration_changed.emit(self.total_frames)
        self.video_loaded.emit(file_path)
        self.seek_to_frame(round(max(0.0, start_position_seconds) * self.frame_rate))

    def clear_media(self):
        """Invalidate background results before switching shots, sources, or stills."""
        self.pause()
        self._scrub_timer.stop()
        self._scrub_active = False
        self._decode_generation += 1
        self._sequence_target = None
        self._sequence_reader = None
        if self._cache_reader is not None and self._cache_reader is not self.reader:
            self._cache_reader.close()
        self._cache_reader = None
        self.reader.close()
        self.total_frames = 0
        self.current_frame = 0
        self.current_video_path = ""
        self._showing_still = False
        self._frame_cache.clear()
        self._cache_order.clear()
        self._cache_bytes = 0
        self._cache_plan = []
        self._cache_frame_bytes = 0
        self._cache_errors.clear()
        self._sequence_prefetch_failed.clear()
        self._playback_times.clear()
        self.source_start_frame = 0
        self.source_start_changed.emit(0)
        self.video_widget.set_frame_image(QImage())
        self.annotation_canvas.clear_all_annotations()
        self.duration_changed.emit(0)
        self.frame_changed.emit(0)
        self.playback_status_changed.emit("No media")
        self._update_cache_progress()

    def set_ocio_processor(self, processor):
        """Re-render the current source, invalidating transformed frame caches."""
        self._ocio_processor = processor
        self._decode_generation += 1
        self._frame_cache.clear()
        self._cache_order.clear()
        self._cache_bytes = 0
        self._cache_plan = []
        self._cache_frame_bytes = 0
        self._cache_errors.clear()
        self._sequence_prefetch_failed.clear()
        if self._is_playing:
            self._buffering = True
            self.play_timer.stop()
        if self.total_frames > 0 and not self._showing_still:
            self.seek_to_frame(self.current_frame)
        self._update_cache_progress()

    def _request_sequence_frame(self, index):
        cached = self._frame_cache.get(index)
        if cached is not None:
            self._sequence_target = None
            self._present_cached_frame(index, cached)
            return
        self._sequence_target = index
        self.playback_status_changed.emit(f"Loading frame {self.source_start_frame + index}…")
        key = (self._decode_generation, index)
        if key in self._sequence_tasks or len(self._sequence_tasks) >= self._sequence_worker_limit:
            return
        self._start_sequence_task(index)

    def set_decode_scale(self, scale):
        """Change display resolution without moving the review position."""
        if scale not in (1, 2, 4, 8) or scale == self._decode_scale:
            return
        self._decode_scale = scale
        self.set_ocio_processor(self._ocio_processor)

    def _start_sequence_task(self, index):
        self._sequence_busy = True
        task = ReviewFrameTask(self._decode_generation, index, self._cache_reader,
                            self._ocio_processor, self._decode_scale)
        task.signals.finished.connect(self._sequence_frame_ready)
        self._sequence_tasks[(self._decode_generation, index)] = task
        QThreadPool.globalInstance().start(task)

    @pyqtSlot(int, int, object, str)
    def _sequence_frame_ready(self, generation, index, image, error):
        self._sequence_tasks.pop((generation, index), None)
        self._sequence_busy = bool(self._sequence_tasks)
        if generation == self._decode_generation:
            if error:
                self._sequence_prefetch_failed.add(index)
                self._cache_errors[index] = error
                if self._sequence_target == index or (self._is_playing and index in self._cache_plan):
                    self._sequence_target = None
                    self.pause()
                    self.error_occurred.emit(f"Render frame failed: {error}")
                    self.frame_changed.emit(self.current_frame)
            elif index in self._cache_plan or self._sequence_target == index or index == self.current_frame:
                self._cache_frame(index, image)
                if self._sequence_target == index:
                    self._sequence_target = None
                    self._present_cached_frame(index, image)
        if self._cache_reader is not None and self._sequence_target is not None:
            self._request_sequence_frame(self._sequence_target)
        self._prefetch_sequence()
        self._update_cache_progress()
        self._resume_cached_playback()
        if self.total_frames and not self._showing_still:
            self._emit_playback_status()

    def _prefetch_sequence(self):
        """Load the active range/window, including while playback is paused."""
        if self._cache_reader is None or self._sequence_target is not None or not self.total_frames:
            return
        if not self._cache_plan:
            self._cache_plan = self._make_cache_plan(self.current_frame)
        pending = {index for generation, index in self._sequence_tasks
                   if generation == self._decode_generation}
        protected = set(self._cache_plan)
        for index in self._cache_plan:
            if len(self._sequence_tasks) >= self._sequence_worker_limit:
                break
            if index in self._frame_cache or index in pending or index in self._sequence_prefetch_failed:
                continue
            reserved = len(pending) + 1
            while (len(self._frame_cache) + reserved > self._cache_size or
                   self._cache_bytes + reserved * self._cache_frame_bytes > self._cache_byte_limit):
                old = next((frame for frame in self._cache_order if frame not in protected), None)
                if old is None:
                    break
                self._evict_cache_frame(old)
            else:
                self._start_sequence_task(index)
                pending.add(index)
                continue
            # A single oversized frame is still displayable. Otherwise wait for
            # reservations to finish instead of exceeding the selected RAM budget.
            if not self._frame_cache and not pending:
                self._start_sequence_task(index)
            break
        self._update_cache_progress()

    def _emit_playback_status(self):
        ready, total, _, _ = self._last_cache_progress or (0, 0, 0, 0)
        if self._buffering and self._is_playing:
            loaded = sum(index in self._frame_cache for index in self._cache_plan)
            state = f"Loading frames {loaded}/{len(self._cache_plan)}…"
        else:
            state = "Playing" if self._is_playing else "Paused"
            if self._is_playing and len(self._playback_times) > 1:
                elapsed = self._playback_times[-1] - self._playback_times[0]
                if elapsed > 0:
                    state += f" · {(len(self._playback_times) - 1) / elapsed:.1f} fps"
        if total:
            state += f" · {ready}/{total} cached"
        self.playback_status_changed.emit(state)

    def is_showing_still(self) -> bool:
        return self._showing_still

    def show_still(self, image: QImage):
        """Display a still image without changing playback state."""
        if image is None or image.isNull():
            return
        self.clear_media()
        self._showing_still = True
        self.video_widget.set_frame_image(image)
        self._update_canvas_geometry()
        self.playback_status_changed.emit("Thumbnail")

    def is_playing(self) -> bool:
        return self._is_playing

    def play(self):
        """Load frames first, then start forward playback from the cache."""
        self._start_cached_playback(1)

    def play_backward(self):
        """Load frames first, then start reverse playback from the cache."""
        self._start_cached_playback(-1)

    def pause(self):
        """Cancel playback intent while allowing frame loading to continue."""
        self.play_timer.stop()
        self._buffering = False
        if self._is_playing:
            self._is_playing = False
            self.playback_state_changed.emit(False)
            self._playback_times.clear()
            self._emit_playback_status()

    def toggle_playback(self):
        """Toggle between play and pause."""
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def _on_video_clicked(self):
        """Toggle video playback while keeping stills and drawing tools intact."""
        if self._showing_still or self.annotation_canvas.current_mode != AnnotationCanvas.MODE_NONE:
            return
        self.toggle_playback()

    def stop(self):
        """Stop playback and return to start."""
        self.pause()
        self.seek_to_frame(0)

    def seek_to_frame(self, frame: int):
        """Seek to a specific frame."""
        if self.total_frames <= 0:
            return
        frame = max(0, min(frame, self.total_frames - 1))
        self._cache_plan = self._make_cache_plan(frame)
        cached = self._frame_cache.get(frame)
        if cached is not None:
            self._sequence_target = None
            self._present_cached_frame(frame, cached)
            self._resume_cached_playback()
            return
        if frame in self._cache_errors:
            self.pause()
            self.error_occurred.emit(f"Render frame failed: {self._cache_errors[frame]}")
            self.frame_changed.emit(self.current_frame)
            return
        if self._sequence_reader is not None:
            self._request_sequence_frame(frame)
            return
        try:
            decoded_frame, index = self.reader.seek_to_frame(frame)
        except Exception as exc:
            error_msg = f"Frame seek failed: {exc}"
            print(f"[VideoViewer] {error_msg}")
            self.error_occurred.emit(error_msg)
            self.frame_changed.emit(self.current_frame)
            return
        if decoded_frame is None:
            self.error_occurred.emit(f"Frame {self.source_start_frame + frame} is unavailable.")
            self.frame_changed.emit(self.current_frame)
            return
        self._present_frame(decoded_frame, index)

    def seek_to_position_ms(self, position_ms: int):
        """Seek to a specific position in milliseconds."""
        if self.frame_rate <= 0:
            return
        position_ms = max(0, min(position_ms, self.duration_ms))
        frame = int((position_ms / 1000.0) * self.frame_rate)
        self.seek_to_frame(frame)

    def step_forward(self, frames=1):
        self.pause()
        if self.total_frames:
            high = self.out_point if self.use_play_range else self.total_frames - 1
            self.seek_to_frame(min(self.current_frame + frames, high))

    def step_backward(self, frames=1):
        self.pause()
        if self.total_frames:
            low = self.in_point if self.use_play_range else 0
            self.seek_to_frame(max(self.current_frame - frames, low))

    def go_to_start(self):
        """Go to first frame (or in point if using play range)."""
        if self.use_play_range:
            self.seek_to_frame(self.in_point)
        else:
            self.seek_to_frame(0)

    def go_to_end(self):
        """Go to last frame (or out point if using play range)."""
        if self.use_play_range:
            self.seek_to_frame(self.out_point)
        else:
            self.seek_to_frame(max(0, self.total_frames - 1))

    def set_in_point(self, frame: int):
        """Set the in point for play range."""
        self.in_point = max(0, min(frame, self.out_point))
        if self.use_play_range:
            self.preload_frames()

    def set_out_point(self, frame: int):
        """Set the out point for play range."""
        self.out_point = min(max(0, self.total_frames - 1), max(frame, self.in_point))
        if self.use_play_range:
            self.preload_frames()

    def set_play_range_enabled(self, enabled: bool):
        """Enable or disable play range limiting."""
        self.use_play_range = enabled
        self.preload_frames()

    def set_frame_rate(self, fps: float):
        """Change playback speed without altering the source's frame count."""
        if fps > 0:
            self.frame_rate = fps
            self.duration_ms = int(self.total_frames * 1000 / fps)
            self._update_playback_interval()
            self.frame_rate_changed.emit(fps)

    def set_volume(self, volume: float):
        """Store volume (audio playback not implemented)."""
        self._volume = max(0.0, min(1.0, volume))

    def set_loop_playback(self, enabled: bool):
        """Enable or disable loop playback."""
        self.loop_playback = enabled

    def begin_scrub(self):
        """Enter live scrubbing mode for responsive seeking."""
        if self._scrub_active:
            return
        self._scrub_active = True
        self._scrub_target_frame = None
        self._scrub_last_frame = None
        self._scrub_timer.start()

    def end_scrub(self, final_frame: int, resume_playback: bool):
        """Exit scrubbing mode and settle on the requested frame."""
        if not self._scrub_active:
            return
        self._scrub_timer.stop()
        self._scrub_active = False
        self._scrub_target_frame = None
        self._scrub_last_frame = None
        self.seek_to_frame(final_frame)
        if resume_playback:
            self.play()
        else:
            self.pause()

    def scrub_to_frame(self, frame: int):
        """Handle seeking during scrubbing or normal pause."""
        if self._scrub_active:
            self._scrub_target_frame = frame
        else:
            self.seek_to_frame(frame)

    def _apply_scrub_target(self):
        """Update the frame while scrubbing."""
        if self._scrub_active and self._scrub_target_frame is not None:
            if self._scrub_target_frame == self._scrub_last_frame:
                return
            self._scrub_last_frame = self._scrub_target_frame
            self.seek_to_frame(self._scrub_target_frame)

    def _on_playback_tick(self):
        """Present cached images only; decoding and OCIO run during loading."""
        if self.total_frames <= 0:
            self.pause()
            return
        low, high = self._cache_bounds()
        if not self.loop_playback and self._sequence_target is None and (
                self.current_frame >= high if self._play_direction >= 0 else self.current_frame <= low):
            self.pause()
            self.playback_finished.emit()
            return
        if self._buffering or self._sequence_target is not None:
            return
        target = self.current_frame + self._play_direction
        if target < low or target > high:
            target = low if self._play_direction >= 0 else high
        image = self._frame_cache.get(target)
        if image is None:
            self._cache_plan = self._make_cache_plan(target)
            self._buffering = True
            self.play_timer.stop()
            self._playback_times.clear()
            self._prefetch_sequence()
            self._resume_cached_playback()
            self._emit_playback_status()
            return
        self._present_cached_frame(target, image)

    def _present_frame(self, frame, index: int):
        image = self._frame_to_qimage(frame)
        if image is None:
            return
        self.video_widget.set_frame_image(image)
        self._update_canvas_geometry()
        self.current_frame = index
        self.annotation_canvas.set_frame(self.current_frame)
        self.frame_changed.emit(self.current_frame)
        self._cache_frame(index, image)
        self._record_presented_frame()
        self._prefetch_sequence()
        self._resume_cached_playback()

    def _present_cached_frame(self, index: int, image: QImage):
        self.video_widget.set_frame_image(image)
        self._update_canvas_geometry()
        self.current_frame = index
        self.annotation_canvas.set_frame(self.current_frame)
        self.frame_changed.emit(self.current_frame)
        self._record_presented_frame()
        self._prefetch_sequence()

    def _record_presented_frame(self):
        if self._is_playing:
            self._playback_times.append(time.monotonic())
        self._emit_playback_status()

    def _frame_to_qimage(self, frame):
        try:
            return movie_display_image(frame, self._ocio_processor, self._decode_scale)
        except Exception as exc:
            self.pause()
            self.error_occurred.emit(f"Frame conversion failed: {exc}")
            return None

    def _cache_bounds(self):
        return (self.in_point, self.out_point) if self.use_play_range else (0, self.total_frames - 1)

    def _make_cache_plan(self, anchor):
        low, high = self._cache_bounds()
        if high < low:
            return []
        capacity = min(self._cache_size, high - low + 1)
        if self._cache_frame_bytes:
            capacity = min(capacity, max(1, self._cache_byte_limit // self._cache_frame_bytes))
        else:
            capacity = 1
        anchor = min(high, max(low, anchor))
        plan = []
        for offset in range(capacity):
            index = anchor + offset * self._play_direction
            if self.loop_playback:
                index = low + (index - low) % (high - low + 1)
            elif index < low or index > high:
                break
            plan.append(index)
        return plan

    def _evict_cache_frame(self, index):
        self._cache_order.remove(index)
        self._cache_bytes -= self._frame_cache.pop(index).sizeInBytes()

    def _update_cache_progress(self):
        low, high = self._cache_bounds()
        ready = sum(low <= index <= high for index in self._frame_cache)
        progress = (ready, max(0, high - low + 1), self._cache_bytes, self._cache_byte_limit)
        if progress != self._last_cache_progress:
            self._last_cache_progress = progress
            self.cache_progress_changed.emit(*progress)

    def preload_frames(self):
        """Load the selected range/window without changing the displayed frame."""
        if not self.total_frames or self._cache_reader is None:
            return
        self._cache_plan = self._make_cache_plan(self.current_frame)
        for index in self._cache_plan:
            self._sequence_prefetch_failed.discard(index)
            self._cache_errors.pop(index, None)
        if self._is_playing:
            self._buffering = True
            self.play_timer.stop()
            self._playback_times.clear()
        self._prefetch_sequence()
        self._resume_cached_playback()
        self._emit_playback_status()

    def set_cache_limit_mb(self, megabytes):
        self._cache_byte_limit = max(1, int(megabytes)) * 1024 * 1024
        self._cache_plan = self._make_cache_plan(self.current_frame)
        while self._cache_bytes > self._cache_byte_limit and len(self._cache_order) > 1:
            old = next((index for index in self._cache_order if index not in self._cache_plan), self._cache_order[0])
            self._evict_cache_frame(old)
        self.preload_frames()
        self._update_cache_progress()

    def _start_cached_playback(self, direction):
        if not self.total_frames:
            return
        self._play_direction = direction
        low, high = self._cache_bounds()
        if self.current_frame < low or self.current_frame > high:
            self.seek_to_frame(low if direction > 0 else high)
        if not self._is_playing:
            self._is_playing = True
            self.playback_state_changed.emit(True)
        self._buffering = True
        self.play_timer.stop()
        self._playback_times.clear()
        self._cache_plan = self._make_cache_plan(self.current_frame)
        self._prefetch_sequence()
        self._resume_cached_playback()
        self._emit_playback_status()

    def _resume_cached_playback(self):
        if not self._is_playing or not self._buffering:
            return
        failed = next((index for index in self._cache_plan if index in self._cache_errors), None)
        if failed is not None:
            self.pause()
            self.error_occurred.emit(f"Render frame failed: {self._cache_errors[failed]}")
            self.frame_changed.emit(self.current_frame)
            return
        if self._sequence_target is None and self._cache_plan and all(index in self._frame_cache for index in self._cache_plan):
            self._buffering = False
            self._playback_times.clear()
            self._update_playback_interval()
            self.play_timer.start()

    def _update_playback_interval(self):
        if self.frame_rate > 0:
            interval = max(1, int(round(1000.0 / self.frame_rate)))
        else:
            interval = 40
        self.play_timer.setInterval(interval)

    def _update_canvas_geometry(self):
        """Keep annotation canvas aligned to the visible video area."""
        if not self.video_widget:
            return
        target_rect = self.video_widget.video_rect()
        if self.annotation_canvas.geometry() != target_rect:
            self.annotation_canvas.setGeometry(target_rect)
            self.annotation_canvas.raise_()

    def _cache_frame(self, index, image):
        if index in self._frame_cache:
            return
        if image.sizeInBytes() > self._cache_frame_bytes:
            self._cache_frame_bytes = image.sizeInBytes()
            self._cache_plan = self._make_cache_plan(self._sequence_target if self._sequence_target is not None
                                                     else self.current_frame)
        self._frame_cache[index] = image
        self._cache_order.append(index)
        self._cache_bytes += image.sizeInBytes()
        while (len(self._cache_order) > self._cache_size or self._cache_bytes > self._cache_byte_limit) and len(self._cache_order) > 1:
            old = next((frame for frame in self._cache_order if frame not in self._cache_plan), self._cache_order[0])
            self._evict_cache_frame(old)
        self._update_cache_progress()

    def capture_annotated_frame(self) -> QImage:
        """Capture the current frame with annotations composited."""
        base_image = self.video_widget.current_frame_image()
        if base_image.isNull():
            return QImage()
        overlay_pixmap = self.annotation_canvas.get_composite_image()
        if overlay_pixmap.isNull():
            return base_image
        overlay_image = overlay_pixmap.toImage().scaled(
            base_image.size(),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        composite = QImage(base_image)
        painter = QPainter(composite)
        painter.drawImage(0, 0, overlay_image)
        painter.end()
        return composite

# =============================================================================
# PLAYBACK BAR - Nuke-style timeline controls
# =============================================================================

class PlaybackBar(QWidget):
    """
    Nuke-style playback controls with timeline scrubber.
    Features: play/pause, frame stepping, in/out points, frame counter.
    """
    
    play_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    frame_changed = pyqtSignal(int)
    step_forward_clicked = pyqtSignal(int)  # frames to step
    step_backward_clicked = pyqtSignal(int)
    go_to_start_clicked = pyqtSignal()
    go_to_end_clicked = pyqtSignal()
    in_point_changed = pyqtSignal(int)
    out_point_changed = pyqtSignal(int)
    play_range_toggled = pyqtSignal(bool)
    frame_rate_changed = pyqtSignal(float)
    scrub_started = pyqtSignal()
    scrub_finished = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(90)
        self.setObjectName("playback_bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        self.total_frames = 100
        self.current_frame = 0
        self.in_point = 0
        self.out_point = 100
        self.is_playing = False
        self.frame_offset = 0
        
        self._setup_ui()

    def _setup_ui(self):
        """Create the playback bar UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 6, 10, 6)
        main_layout.setSpacing(6)
        
        # === Timeline slider row ===
        timeline_layout = QHBoxLayout()
        timeline_layout.setSpacing(4)
        
        # In point spinbox
        self.spinbox_in_point = QSpinBox()
        self.spinbox_in_point.setFixedWidth(70)
        self.spinbox_in_point.setRange(0, 99999)
        self.spinbox_in_point.setValue(0)
        self.spinbox_in_point.setToolTip("In Point")
        self.spinbox_in_point.valueChanged.connect(self._on_in_point_changed)
        timeline_layout.addWidget(self.spinbox_in_point)
        
        # Timeline slider
        self.slider_timeline = QSlider(Qt.Orientation.Horizontal)
        self.slider_timeline.setRange(0, 100)
        self.slider_timeline.setValue(0)
        self.slider_timeline.setTickPosition(QSlider.TickPosition.NoTicks)
        self.slider_timeline.setTracking(True)
        self.slider_timeline.sliderMoved.connect(self._on_slider_moved)
        self.slider_timeline.sliderPressed.connect(self._on_slider_pressed)
        self.slider_timeline.sliderReleased.connect(self._on_slider_released)
        timeline_layout.addWidget(self.slider_timeline, 1)
        
        # Out point spinbox
        self.spinbox_out_point = QSpinBox()
        self.spinbox_out_point.setFixedWidth(70)
        self.spinbox_out_point.setRange(0, 99999)
        self.spinbox_out_point.setValue(100)
        self.spinbox_out_point.setToolTip("Out Point")
        self.spinbox_out_point.valueChanged.connect(self._on_out_point_changed)
        timeline_layout.addWidget(self.spinbox_out_point)
        
        main_layout.addLayout(timeline_layout)
        
        # === Controls row ===
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(4)
        
        # Play range toggle
        self.button_play_range = QPushButton()
        self.button_play_range.setFixedSize(36, 32)
        self.button_play_range.setCheckable(True)
        self.button_play_range.setToolTip("Use In/Out Range")
        set_review_button_icon(self.button_play_range, "range")
        self.button_play_range.toggled.connect(self.play_range_toggled.emit)
        controls_layout.addWidget(self.button_play_range)
        
        controls_layout.addSpacing(10)
        
        # Go to start
        self.button_go_to_start = QPushButton()
        self.button_go_to_start.setFixedSize(36, 32)
        self.button_go_to_start.setToolTip("Go to Start")
        set_review_button_icon(self.button_go_to_start, "start")
        self.button_go_to_start.clicked.connect(self.go_to_start_clicked.emit)
        controls_layout.addWidget(self.button_go_to_start)
        
        # Step backward 10
        self.button_step_back_10 = QPushButton()
        self.button_step_back_10.setFixedSize(36, 32)
        self.button_step_back_10.setToolTip("Step Back 10 Frames")
        set_review_button_icon(self.button_step_back_10, "back_10")
        self.button_step_back_10.clicked.connect(lambda: self.step_backward_clicked.emit(10))
        controls_layout.addWidget(self.button_step_back_10)
        
        # Step backward 1
        self.button_step_back_1 = QPushButton()
        self.button_step_back_1.setFixedSize(36, 32)
        self.button_step_back_1.setToolTip("Step Back 1 Frame")
        set_review_button_icon(self.button_step_back_1, "step_back")
        self.button_step_back_1.clicked.connect(lambda: self.step_backward_clicked.emit(1))
        controls_layout.addWidget(self.button_step_back_1)
        
        # Play/Pause
        self.button_play_pause = QPushButton()
        self.button_play_pause.setFixedSize(54, 32)
        self.button_play_pause.setCheckable(True)
        self.button_play_pause.setToolTip("Play/Pause")
        set_review_button_icon(self.button_play_pause, "play")
        self.button_play_pause.clicked.connect(self._on_play_pause_clicked)
        controls_layout.addWidget(self.button_play_pause)
        
        # Step forward 1
        self.button_step_forward_1 = QPushButton()
        self.button_step_forward_1.setFixedSize(36, 32)
        self.button_step_forward_1.setToolTip("Step Forward 1 Frame")
        set_review_button_icon(self.button_step_forward_1, "step_forward")
        self.button_step_forward_1.clicked.connect(lambda: self.step_forward_clicked.emit(1))
        controls_layout.addWidget(self.button_step_forward_1)
        
        # Step forward 10
        self.button_step_forward_10 = QPushButton()
        self.button_step_forward_10.setFixedSize(36, 32)
        self.button_step_forward_10.setToolTip("Step Forward 10 Frames")
        set_review_button_icon(self.button_step_forward_10, "forward_10")
        self.button_step_forward_10.clicked.connect(lambda: self.step_forward_clicked.emit(10))
        controls_layout.addWidget(self.button_step_forward_10)
        
        # Go to end
        self.button_go_to_end = QPushButton()
        self.button_go_to_end.setFixedSize(36, 32)
        self.button_go_to_end.setToolTip("Go to End")
        set_review_button_icon(self.button_go_to_end, "end")
        self.button_go_to_end.clicked.connect(self.go_to_end_clicked.emit)
        controls_layout.addWidget(self.button_go_to_end)
        
        controls_layout.addSpacing(20)
        
        # Current frame display
        self.label_current_frame = QLabel("Frame:")
        controls_layout.addWidget(self.label_current_frame)
        
        self.spinbox_current_frame = QSpinBox()
        self.spinbox_current_frame.setFixedWidth(80)
        self.spinbox_current_frame.setRange(0, 99999)
        self.spinbox_current_frame.setValue(0)
        self.spinbox_current_frame.valueChanged.connect(self._on_frame_spinbox_changed)
        controls_layout.addWidget(self.spinbox_current_frame)
        
        # Total frames label
        self.label_total_frames = QLabel("/ 0")
        self.label_total_frames.setFixedWidth(60)
        controls_layout.addWidget(self.label_total_frames)

        controls_layout.addWidget(QLabel("FPS:"))
        self.spinbox_fps = QDoubleSpinBox()
        self.spinbox_fps.setRange(1, 120)
        self.spinbox_fps.setDecimals(2)
        self.spinbox_fps.setValue(24)
        self.spinbox_fps.setFixedWidth(84)
        self.spinbox_fps.setToolTip("Playback frame rate, including native EXR sequences")
        self.spinbox_fps.valueChanged.connect(self.frame_rate_changed.emit)
        controls_layout.addWidget(self.spinbox_fps)

        self.button_loop = QPushButton("Loop")
        self.button_loop.setCheckable(True)
        self.button_loop.setChecked(True)
        self.button_loop.setToolTip("Repeat the shot or selected In/Out range")
        controls_layout.addWidget(self.button_loop)
        
        controls_layout.addStretch()
        
        main_layout.addLayout(controls_layout)
    
    def set_total_frames(self, total: int):
        """Set the total number of frames."""
        self.total_frames = max(0, total)
        last = max(0, total - 1)
        for control in (self.slider_timeline, self.spinbox_current_frame,
                        self.spinbox_in_point, self.spinbox_out_point):
            control.blockSignals(True)
        self.slider_timeline.setRange(0, last)
        self.slider_timeline.setValue(0)
        for control in (self.spinbox_current_frame, self.spinbox_in_point, self.spinbox_out_point):
            control.setRange(self.frame_offset, self.frame_offset + last)
        self.spinbox_current_frame.setValue(self.frame_offset)
        self.spinbox_in_point.setValue(self.frame_offset)
        self.spinbox_out_point.setValue(self.frame_offset + last)
        self.in_point, self.out_point = 0, last
        self.label_total_frames.setText(f"/ {self.frame_offset + last}" if total else "/ —")
        for control in (self.slider_timeline, self.spinbox_current_frame,
                        self.spinbox_in_point, self.spinbox_out_point):
            control.blockSignals(False)
        self.setEnabled(total > 0)

    def set_frame_offset(self, offset):
        self.frame_offset = offset
    
    def set_current_frame(self, frame: int):
        """Set the current frame (from external source)."""
        self.current_frame = frame
        self.slider_timeline.blockSignals(True)
        self.slider_timeline.setValue(frame)
        self.slider_timeline.blockSignals(False)
        self.spinbox_current_frame.blockSignals(True)
        self.spinbox_current_frame.setValue(frame + self.frame_offset)
        self.spinbox_current_frame.blockSignals(False)
    
    def set_playing(self, is_playing: bool):
        """Update play/pause button state."""
        self.is_playing = is_playing
        self.button_play_pause.setIcon(review_icon("pause" if is_playing else "play"))
        self.button_play_pause.setChecked(is_playing)

    def set_frame_rate(self, fps):
        blocked = self.spinbox_fps.blockSignals(True)
        self.spinbox_fps.setValue(fps)
        self.spinbox_fps.blockSignals(blocked)
    
    def _on_play_pause_clicked(self):
        """Handle play/pause button click."""
        # The viewer confirms playback; an unloaded video cannot become active.
        self.button_play_pause.setChecked(self.is_playing)
        if self.is_playing:
            self.pause_clicked.emit()
        else:
            self.play_clicked.emit()
    
    def _on_slider_moved(self, value: int):
        """Handle slider drag."""
        self.frame_changed.emit(value)
    
    def _on_slider_pressed(self):
        """Handle slider click (jump to position)."""
        self.scrub_started.emit()
        self.frame_changed.emit(self.slider_timeline.value())

    def _on_slider_released(self):
        """Handle slider release after dragging."""
        self.frame_changed.emit(self.slider_timeline.value())
        self.scrub_finished.emit()
    
    def _on_frame_spinbox_changed(self, value: int):
        """Handle frame spinbox change."""
        self.frame_changed.emit(value - self.frame_offset)
    
    def _on_in_point_changed(self, value: int):
        """Handle in point change."""
        self.in_point = value - self.frame_offset
        if self.in_point > self.out_point:
            self.spinbox_out_point.setValue(value)
        self.in_point_changed.emit(self.in_point)
    
    def _on_out_point_changed(self, value: int):
        """Handle out point change."""
        self.out_point = value - self.frame_offset
        if self.out_point < self.in_point:
            self.spinbox_in_point.setValue(value)
        self.out_point_changed.emit(self.out_point)


# =============================================================================
# ANNOTATION TOOLBAR - Drawing tools and color selection
# =============================================================================

class AnnotationToolbar(QWidget):
    """
    Toolbar for annotation tools: circle, rectangle, arrow, freehand, text.
    Plus color presets and thickness selection.
    """
    
    tool_selected = pyqtSignal(str)  # Emits tool mode
    color_selected = pyqtSignal(object)  # Emits QColor
    thickness_selected = pyqtSignal(int)
    scope_selected = pyqtSignal(str)
    range_selected = pyqtSignal(int, int)
    clear_clicked = pyqtSignal()
    undo_clicked = pyqtSignal()
    save_thumbnail_clicked = pyqtSignal()
    save_annotated_preview_clicked = pyqtSignal()
    
    # Preset colors
    COLOR_PRESETS = [
        QColor(255, 80, 80),    # Red
        QColor(80, 255, 80),    # Green
        QColor(80, 80, 255),    # Blue
        QColor(255, 255, 80),   # Yellow
        QColor(255, 128, 0),    # Orange
        QColor(255, 255, 255),  # White
    ]
    
    THICKNESS_PRESETS = [2, 3, 5, 8]
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("review_annotation_toolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumHeight(90)
        
        self.current_tool = AnnotationCanvas.MODE_NONE
        self.current_color = self.COLOR_PRESETS[0]
        self.current_thickness = 3
        self.current_scope = AnnotationCanvas.SCOPE_FRAME
        self.frame_offset = 0
        self.range_start = 0
        self.range_end = 0
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Create the annotation toolbar UI."""
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 4, 8, 4)
        outer_layout.setSpacing(4)

        row_tools = QHBoxLayout()
        row_tools.setSpacing(4)
        outer_layout.addLayout(row_tools)

        row_options = QHBoxLayout()
        row_options.setSpacing(4)
        outer_layout.addLayout(row_options)
        
        # === Drawing tools ===
        self.button_group_tools = QButtonGroup(self)
        self.button_group_tools.setExclusive(True)
        
        # Select (no tool)
        self.button_select = QPushButton()
        self.button_select.setFixedSize(36, 32)
        self.button_select.setCheckable(True)
        self.button_select.setChecked(True)
        self.button_select.setToolTip("Select (No Drawing)")
        set_review_button_icon(self.button_select, "select")
        self.button_group_tools.addButton(self.button_select)
        row_tools.addWidget(self.button_select)
        
        # Circle tool
        self.button_circle = QPushButton()
        self.button_circle.setFixedSize(36, 32)
        self.button_circle.setCheckable(True)
        self.button_circle.setToolTip("Draw Circle")
        set_review_button_icon(self.button_circle, "circle")
        self.button_group_tools.addButton(self.button_circle)
        row_tools.addWidget(self.button_circle)
        
        # Rectangle tool
        self.button_rectangle = QPushButton()
        self.button_rectangle.setFixedSize(36, 32)
        self.button_rectangle.setCheckable(True)
        self.button_rectangle.setToolTip("Draw Rectangle")
        set_review_button_icon(self.button_rectangle, "rectangle")
        self.button_group_tools.addButton(self.button_rectangle)
        row_tools.addWidget(self.button_rectangle)
        
        # Arrow tool
        self.button_arrow = QPushButton()
        self.button_arrow.setFixedSize(36, 32)
        self.button_arrow.setCheckable(True)
        self.button_arrow.setToolTip("Draw Arrow")
        set_review_button_icon(self.button_arrow, "arrow")
        self.button_group_tools.addButton(self.button_arrow)
        row_tools.addWidget(self.button_arrow)
        
        # Freehand tool
        self.button_freehand = QPushButton()
        self.button_freehand.setFixedSize(36, 32)
        self.button_freehand.setCheckable(True)
        self.button_freehand.setToolTip("Freehand Draw")
        set_review_button_icon(self.button_freehand, "freehand")
        self.button_group_tools.addButton(self.button_freehand)
        row_tools.addWidget(self.button_freehand)
        
        # Text tool
        self.button_text = QPushButton()
        self.button_text.setFixedSize(36, 32)
        self.button_text.setCheckable(True)
        self.button_text.setToolTip("Add Text")
        set_review_button_icon(self.button_text, "text")
        self.button_group_tools.addButton(self.button_text)
        row_tools.addWidget(self.button_text)
        
        # Connect tool buttons
        self.button_select.clicked.connect(lambda: self._on_tool_clicked(AnnotationCanvas.MODE_NONE))
        self.button_circle.clicked.connect(lambda: self._on_tool_clicked(AnnotationCanvas.MODE_CIRCLE))
        self.button_rectangle.clicked.connect(lambda: self._on_tool_clicked(AnnotationCanvas.MODE_RECTANGLE))
        self.button_arrow.clicked.connect(lambda: self._on_tool_clicked(AnnotationCanvas.MODE_ARROW))
        self.button_freehand.clicked.connect(lambda: self._on_tool_clicked(AnnotationCanvas.MODE_FREEHAND))
        self.button_text.clicked.connect(lambda: self._on_tool_clicked(AnnotationCanvas.MODE_TEXT))
        
        row_tools.addSpacing(20)
        
        # === Color presets ===
        self.label_color = QLabel("Color:")
        row_tools.addWidget(self.label_color)
        
        self.button_group_colors = QButtonGroup(self)
        self.button_group_colors.setExclusive(True)
        self.color_buttons = []
        
        for i, color in enumerate(self.COLOR_PRESETS):
            button = QPushButton()
            button.setFixedSize(24, 24)
            button.setCheckable(True)
            button.setProperty("review_swatch", "true")
            button.setStyleSheet(f"QPushButton {{ background-color: {color.name()}; }}")
            button.setToolTip(f"Color: {color.name()}")
            button.setAccessibleName(button.toolTip())
            if i == 0:
                button.setChecked(True)
            self.button_group_colors.addButton(button)
            self.color_buttons.append(button)
            button.clicked.connect(lambda checked, c=color: self._on_color_clicked(c))
            row_tools.addWidget(button)
        
        row_tools.addStretch()
        
        # === Thickness presets ===
        self.label_thickness = QLabel("Size:")
        row_options.addWidget(self.label_thickness)
        
        self.combo_thickness = ReadableComboBox(compact=True)
        self.combo_thickness.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        for thickness in self.THICKNESS_PRESETS:
            self.combo_thickness.addItem(f"{thickness}px", thickness)
        self.combo_thickness.setCurrentIndex(1)  # Default to 3px
        self.combo_thickness.currentIndexChanged.connect(self._on_thickness_changed)
        row_options.addWidget(self.combo_thickness)
        
        row_options.addSpacing(20)

        # === Scope selection ===
        self.label_scope = QLabel("Scope:")
        row_options.addWidget(self.label_scope)

        self.combo_scope = ReadableComboBox(compact=True)
        self.combo_scope.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.combo_scope.addItem("Frame", AnnotationCanvas.SCOPE_FRAME)
        self.combo_scope.addItem("Range", AnnotationCanvas.SCOPE_RANGE)
        self.combo_scope.addItem("Full", AnnotationCanvas.SCOPE_FULL)
        self.combo_scope.currentIndexChanged.connect(self._on_scope_changed)
        row_options.addWidget(self.combo_scope)

        self.label_range = QLabel("Range:")
        row_options.addWidget(self.label_range)

        self.spin_range_start = QSpinBox()
        self.spin_range_start.setFixedWidth(70)
        self.spin_range_start.setRange(0, 99999)
        self.spin_range_start.setValue(0)
        self.spin_range_start.valueChanged.connect(self._on_range_start_changed)
        row_options.addWidget(self.spin_range_start)

        self.label_range_sep = QLabel("–")
        row_options.addWidget(self.label_range_sep)

        self.spin_range_end = QSpinBox()
        self.spin_range_end.setFixedWidth(70)
        self.spin_range_end.setRange(0, 99999)
        self.spin_range_end.setValue(0)
        self.spin_range_end.valueChanged.connect(self._on_range_end_changed)
        row_options.addWidget(self.spin_range_end)

        self._update_scope_controls()
        
        row_options.addSpacing(20)
        
        # === Actions ===
        self.button_undo = QPushButton("Undo")
        self.button_undo.setFixedHeight(30)
        self.button_undo.setToolTip("Undo Last Annotation")
        self.button_undo.clicked.connect(self.undo_clicked.emit)
        row_options.addWidget(self.button_undo)
        
        self.button_clear = QPushButton("Clear")
        self.button_clear.setFixedHeight(30)
        self.button_clear.setToolTip("Clear Annotations in Current Scope")
        self.button_clear.clicked.connect(self.clear_clicked.emit)
        row_options.addWidget(self.button_clear)
        
        row_options.addStretch()
        
        # === Save as thumbnail ===
        self.button_save_thumbnail = QPushButton("Save as Thumbnail")
        self.button_save_thumbnail.setFixedHeight(30)
        self.button_save_thumbnail.setToolTip("Save Current Frame with Annotations as Shot Thumbnail")
        self.button_save_thumbnail.clicked.connect(self.save_thumbnail_clicked.emit)
        row_options.addWidget(self.button_save_thumbnail)

        self.button_save_annotated_preview = QPushButton("Save Annotated Preview")
        self.button_save_annotated_preview.setFixedHeight(30)
        self.button_save_annotated_preview.setEnabled(False)
        self.button_save_annotated_preview.setToolTip(
            "Annotated movie export is unavailable; use Save as Thumbnail")
        self.button_save_annotated_preview.clicked.connect(self.save_annotated_preview_clicked.emit)
        row_options.addWidget(self.button_save_annotated_preview)
    
    def _on_tool_clicked(self, mode: str):
        """Handle tool button click."""
        self.current_tool = mode
        self.tool_selected.emit(mode)
    
    def _on_color_clicked(self, color: QColor):
        """Handle color button click."""
        self.current_color = color
        self.color_selected.emit(color)
    
    def _on_thickness_changed(self, index: int):
        """Handle thickness combo change."""
        thickness = self.combo_thickness.currentData()
        self.current_thickness = thickness
        self.thickness_selected.emit(thickness)

    def _on_scope_changed(self, index: int):
        """Handle scope combo change."""
        scope = self.combo_scope.currentData()
        self.current_scope = scope
        self.scope_selected.emit(scope)
        self._update_scope_controls()
        if scope == AnnotationCanvas.SCOPE_RANGE:
            self.range_selected.emit(self.range_start, self.range_end)

    def _on_range_start_changed(self, value: int):
        """Handle range start change."""
        self.range_start = value
        if self.range_start > self.range_end:
            self.spin_range_end.setValue(self.range_start)
            return
        self.range_selected.emit(self.range_start, self.range_end)

    def _on_range_end_changed(self, value: int):
        """Handle range end change."""
        self.range_end = value
        if self.range_end < self.range_start:
            self.spin_range_start.setValue(self.range_end)
            return
        self.range_selected.emit(self.range_start, self.range_end)

    def _update_scope_controls(self):
        is_range = self.current_scope == AnnotationCanvas.SCOPE_RANGE
        for widget in (
            self.label_range,
            self.spin_range_start,
            self.label_range_sep,
            self.spin_range_end,
        ):
            widget.setEnabled(is_range)

    def set_frame_range_limit(self, total_frames: int):
        max_frame = self.frame_offset + max(0, total_frames - 1)
        self.spin_range_start.blockSignals(True)
        self.spin_range_end.blockSignals(True)
        self.spin_range_start.setRange(self.frame_offset, max_frame)
        self.spin_range_end.setRange(self.frame_offset, max_frame)
        self.spin_range_start.setValue(self.frame_offset)
        self.spin_range_end.setValue(max_frame)
        self.range_start, self.range_end = self.frame_offset, max_frame
        self.spin_range_start.blockSignals(False)
        self.spin_range_end.blockSignals(False)
        self.range_selected.emit(self.range_start, self.range_end)

    def set_frame_offset(self, offset):
        self.frame_offset = offset


# =============================================================================
# TASK LIST SIDEBAR - List of tasks for current shot
# =============================================================================

class TaskListSidebar(QWidget):
    """
    Sidebar showing tasks for the current shot.
    Includes add task button at the bottom.
    """
    
    task_selected = pyqtSignal(dict)  # Emits selected task data
    add_task_clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("review_task_sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMinimumWidth(180)
        self.setMaximumWidth(260)
        
        self.tasks = []
        self.task_widgets = []
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Create the task list sidebar UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)
        
        # Header
        self.label_header = QLabel("Tasks")
        self.label_header.setObjectName("review_task_heading")
        main_layout.addWidget(self.label_header)
        
        # Scroll area for tasks
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        
        # Container for task items
        self.tasks_container = QWidget()
        self.tasks_layout = QVBoxLayout(self.tasks_container)
        self.tasks_layout.setContentsMargins(0, 0, 0, 0)
        self.tasks_layout.setSpacing(4)
        self.tasks_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.scroll_area.setWidget(self.tasks_container)
        main_layout.addWidget(self.scroll_area, 1)
        
        # Add task button
        self.button_add_task = QPushButton("+ Add Task")
        self.button_add_task.setFixedHeight(32)
        self.button_add_task.clicked.connect(self.add_task_clicked.emit)
        main_layout.addWidget(self.button_add_task)
    
    def set_tasks(self, tasks: list):
        """Set the list of tasks to display."""
        self.tasks = tasks
        self._rebuild_task_list()
    
    def _rebuild_task_list(self):
        """Rebuild the task list widgets."""
        # Clear existing widgets and spacers
        while self.tasks_layout.count():
            item = self.tasks_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.task_widgets = []
        
        # Create new task widgets
        for task in self.tasks:
            task_widget = self._create_task_item(task)
            self.tasks_layout.addWidget(task_widget)
            self.task_widgets.append(task_widget)
        
        # Add stretch at end
        self.tasks_layout.addStretch()
    
    def _create_task_item(self, task: dict) -> QWidget:
        """Create a task card widget."""
        task_widget = TaskWidget(task)
        task_widget.setCursor(Qt.CursorShape.PointingHandCursor)
        task_widget.mousePressEvent = lambda event, t=task: self.task_selected.emit(t)
        return task_widget


# =============================================================================
# SHOT NAVIGATION - Previous/Next shot buttons
# =============================================================================

class ShotNavigationOverlay(QWidget):
    """
    Overlay with previous/next shot buttons on the sides of the video viewer.
    """
    
    previous_shot_clicked = pyqtSignal()
    next_shot_clicked = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("review_navigation")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        
        self._setup_ui()
        self._update_mask()
    
    def _setup_ui(self):
        """Create the navigation overlay UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        
        # Previous shot button
        self.button_previous_shot = QPushButton()
        self.button_previous_shot.setFixedSize(40, 36)
        self.button_previous_shot.setToolTip("Previous Shot")
        set_review_button_icon(self.button_previous_shot, "previous")
        self.button_previous_shot.clicked.connect(self.previous_shot_clicked.emit)
        layout.addWidget(self.button_previous_shot, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        layout.addStretch()
        
        # Next shot button
        self.button_next_shot = QPushButton()
        self.button_next_shot.setFixedSize(40, 36)
        self.button_next_shot.setToolTip("Next Shot")
        set_review_button_icon(self.button_next_shot, "next")
        self.button_next_shot.clicked.connect(self.next_shot_clicked.emit)
        layout.addWidget(self.button_next_shot, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_mask()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._update_mask)

    def _update_mask(self):
        """Limit mouse handling to the button areas so drawing can pass through."""
        region = QRegion()
        for button in (self.button_previous_shot, self.button_next_shot):
            rect = button.geometry()
            if not rect.isNull():
                region = region.united(QRegion(rect))
        if region.isEmpty():
            self.clearMask()
        else:
            self.setMask(region)
    
    def set_navigation_enabled(self, has_previous: bool, has_next: bool):
        """Enable/disable navigation buttons based on available shots."""
        self.button_previous_shot.setEnabled(has_previous)
        self.button_next_shot.setEnabled(has_next)


# =============================================================================
# SHOT INFO HEADER - Shows current shot title and position
# =============================================================================

class ShotInfoHeader(QWidget):
    """
    Header bar showing current shot info: title, position in list.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("review_shot_header")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(64)
        self.preview_paths = []
        self.render_options = []
        self._media_mode = "video"
        
        self._setup_ui()
    
    def _setup_ui(self):
        """Create the shot info header UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)
        
        # Shot title
        self.label_shot_title = QLabel("No Shot Loaded")
        self.label_shot_title.setObjectName("review_shot_title")
        top_row.addWidget(self.label_shot_title)
        
        top_row.addStretch()
        
        # Shot position (e.g., "2 of 5")
        self.label_shot_position = QLabel("")
        self.label_shot_position.setObjectName("review_shot_position")
        top_row.addWidget(self.label_shot_position)

        layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        self.label_shot_meta = QLabel("")
        self.label_shot_meta.setObjectName("review_shot_meta")
        bottom_row.addWidget(self.label_shot_meta)

        self.label_preview = QLabel("Media:")
        bottom_row.addWidget(self.label_preview)

        self.combo_preview = ReadableComboBox(elide_mode=Qt.TextElideMode.ElideMiddle)
        self.combo_preview.setMinimumWidth(220)
        self.combo_preview.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_preview.setMinimumContentsLength(22)
        bottom_row.addWidget(self.combo_preview, 1)

        self.button_play_render = QPushButton("Play Render/Preview")
        self.button_play_render.setFixedHeight(30)
        self.button_play_render.setToolTip("No preview or render available")
        self.button_play_render.setEnabled(False)
        bottom_row.addWidget(self.button_play_render)
        self.combo_preview.currentIndexChanged.connect(self._update_play_media_button)

        layout.addLayout(bottom_row)
    
    def set_shot_info(self, title: str, current_index: int, total_shots: int):
        """Update the shot info display."""
        self.label_shot_title.setText(title)
        if total_shots > 0:
            self.label_shot_position.setText(f"{current_index + 1} of {total_shots}")
        else:
            self.label_shot_position.setText("")

    def set_shot_metadata(self, shot: dict):
        """Update metadata fields like frame range, handles, and colorspace."""
        parts = []

        duration = shot.get("duration")
        if duration not in (None, "", 0):
            try:
                end_frame = 1000 + int(duration)
                parts.append(f"Frame: 1001-{end_frame}")
            except (ValueError, TypeError):
                pass

        handles = shot.get("handles")
        if handles not in (None, "", 0):
            parts.append(f"Handles: {handles}")

        colourspace = shot.get("colourspace") or shot.get("colorspace")
        if colourspace:
            parts.append(f"CS: {colourspace}")

        self.label_shot_meta.setText(" | ".join(parts))

    def set_preview_options(
        self,
        preview_paths: list,
        selected_path: Path | None,
        thumbnail_url: str | None = None,
        selected_preview: dict | None = None,
        render_options: list | None = None,
        media_mode: str | None = None,
    ):
        """Populate versions for the active preview or render mode."""
        self.combo_preview.blockSignals(True)
        self.combo_preview.clear()
        self.render_options = render_options or []
        self.preview_paths = list(preview_paths)
        selection = selected_preview or {}
        mode = media_mode or selection.get("type")
        if mode == "thumbnail":
            mode = selection.get("media_mode", "video")
        if mode not in ("video", "render"):
            mode = "video" if self.preview_paths else "render"
        if not media_mode and mode == "render" and not self.render_options and self.preview_paths:
            mode = "video"
        elif not media_mode and mode == "video" and not self.preview_paths and self.render_options:
            mode = "render"
        self._media_mode = mode
        self.label_preview.setText(
            "Renders:" if mode == "render" else
            "Previews:" if self.preview_paths else "Media:"
        )

        if thumbnail_url and (mode == "video" or self.render_options):
            self.combo_preview.addItem(
                "Thumbnail",
                {"type": "thumbnail", "value": thumbnail_url, "media_mode": mode},
            )

        if mode == "video":
            for preview_path in preview_paths:
                self.combo_preview.addItem(
                    f"Preview: {preview_path.name}",
                    {"type": "video", "value": str(preview_path)},
                )

        for render in self.render_options if mode == "render" else []:
            self.combo_preview.addItem(
                f"Render: {render['display_name']}",
                {"type": "render", "value": render["render_path"], "render": render},
            )

        if self.combo_preview.count() == 0:
            self.combo_preview.addItem("No renders" if mode == "render" else "No media", None)
            self.combo_preview.setEnabled(False)
            self.combo_preview.blockSignals(False)
            self._update_play_media_button()
            return

        self.combo_preview.setEnabled(True)

        matched_index = None
        if isinstance(selected_preview, dict):
            target_type = selected_preview.get("type")
            target_value = selected_preview.get("value")
            if target_type and target_value:
                for i in range(self.combo_preview.count()):
                    data = self.combo_preview.itemData(i)
                    if isinstance(data, dict) and data.get("type") == target_type and data.get("value") == target_value:
                        matched_index = i
                        break

        if matched_index is None and selected_path:
            selected_str = str(selected_path)
            for i in range(self.combo_preview.count()):
                data = self.combo_preview.itemData(i)
                if isinstance(data, dict) and data.get("type") == "video" and data.get("value") == selected_str:
                    matched_index = i
                    break

        if matched_index is not None:
            self.combo_preview.setCurrentIndex(matched_index)

        self.combo_preview.blockSignals(False)
        self._update_play_media_button()

    def play_media_target_type(self):
        """Offer the other source when available, or play the only source."""
        if self.render_options and self._media_mode != "render":
            return "render"
        if self.preview_paths:
            return "video"
        if self.render_options:
            return "render"
        return None

    def _update_play_media_button(self, index=None):
        target = self.play_media_target_type()
        self.button_play_render.setEnabled(target is not None)
        if target == "render":
            self.button_play_render.setText("Play Render")
            self.button_play_render.setToolTip("Play a render from renders/comp at the current playback time")
        elif target == "video":
            self.button_play_render.setText("Play Preview")
            self.button_play_render.setToolTip("Play the preview at the current playback time")
        else:
            self.button_play_render.setText("Play Render/Preview")
            self.button_play_render.setToolTip("No preview or render available")

    def current_preview_selection(self) -> dict | None:
        data = self.combo_preview.currentData()
        if isinstance(data, dict):
            return data
        if data is None:
            return None
        return {"type": "video", "value": str(data)}

    def current_preview_path(self) -> str | None:
        selection = self.current_preview_selection()
        if selection and selection.get("type") == "video":
            return selection.get("value")
        return None
    
    def clear(self):
        """Clear the shot info."""
        self.label_shot_title.setText("No Shot Loaded")
        self.label_shot_position.setText("")
        self.label_shot_meta.setText("")
        self.set_preview_options([], None)


# =============================================================================
# REVIEW SELECTION BAR - Job and timeline selectors
# =============================================================================

class ReviewSelectionBar(QWidget):
    """Job and timeline selector bar for the review page."""

    job_changed = pyqtSignal(int)
    timeline_changed = pyqtSignal(int)
    refresh_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("review_selection_bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(44)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        self.label_job = QLabel("Job:")
        layout.addWidget(self.label_job)

        self.combo_job = ReadableComboBox()
        self.combo_job.setMinimumWidth(200)
        self.combo_job.currentIndexChanged.connect(self._on_job_changed)
        layout.addWidget(self.combo_job, 1)

        layout.addSpacing(16)

        self.label_timeline = QLabel("Timeline:")
        layout.addWidget(self.label_timeline)

        self.combo_timeline = ReadableComboBox()
        self.combo_timeline.setMinimumWidth(200)
        self.combo_timeline.currentIndexChanged.connect(self._on_timeline_changed)
        layout.addWidget(self.combo_timeline, 1)

        self.button_ocio = QPushButton("OCIO")
        self.button_ocio.setCheckable(True)
        self.button_ocio.setFixedHeight(28)
        self.button_ocio.setToolTip("Show colour management controls")
        layout.addWidget(self.button_ocio)

        self.button_refresh = QPushButton("Refresh")
        self.button_refresh.setFixedHeight(28)
        self.button_refresh.setToolTip("Refresh from server")
        self.button_refresh.clicked.connect(self.refresh_clicked.emit)
        layout.addWidget(self.button_refresh)

    def set_jobs(self, jobs: list):
        self.combo_job.blockSignals(True)
        self.combo_job.clear()
        for job in jobs:
            job_id = job.get("id")
            title = job.get("title", f"Job {job_id}")
            self.combo_job.addItem(title, job_id)
        self.combo_job.blockSignals(False)

    def set_timelines(self, timelines: list):
        self.combo_timeline.blockSignals(True)
        self.combo_timeline.clear()
        for timeline in timelines:
            tid = timeline.get("id")
            title = timeline.get("title", f"Timeline {tid}")
            self.combo_timeline.addItem(title, tid)
        self.combo_timeline.blockSignals(False)

    def set_selected_job(self, job_id: int | None):
        if job_id is None:
            return
        for i in range(self.combo_job.count()):
            if self.combo_job.itemData(i) == job_id:
                self.combo_job.setCurrentIndex(i)
                return

    def set_selected_timeline(self, timeline_id: int | None):
        if timeline_id is None:
            return
        for i in range(self.combo_timeline.count()):
            if self.combo_timeline.itemData(i) == timeline_id:
                self.combo_timeline.setCurrentIndex(i)
                return

    def selected_job_id(self):
        return self.combo_job.currentData()

    def selected_timeline_id(self):
        return self.combo_timeline.currentData()

    def _on_job_changed(self, index: int):
        job_id = self.combo_job.itemData(index)
        if job_id is not None:
            self.job_changed.emit(job_id)

    def _on_timeline_changed(self, index: int):
        timeline_id = self.combo_timeline.itemData(index)
        if timeline_id is not None:
            self.timeline_changed.emit(timeline_id)

# =============================================================================
# REVIEW PAGE UI - Layout and widget creation
# =============================================================================

class ReviewPageUI:
    """
    Sets up all UI elements for the Review Page.
    Separates UI creation from logic.
    """
    
    def setup_ui(self, widget: QWidget):
        """
        Set up the Review Page UI on the given widget.
        All widgets become attributes of 'widget'.
        """
        widget.setObjectName("review_page")
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # Main layout - horizontal split
        main_layout = QHBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # === Left sidebar - Task list ===
        widget.task_list_sidebar = TaskListSidebar()
        main_layout.addWidget(widget.task_list_sidebar)
        
        # === Center area - Video viewer and controls ===
        center_widget = QWidget()
        widget.center_widget = center_widget
        center_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        
        # Shot info header (top)
        widget.review_selection_bar = ReviewSelectionBar()
        center_layout.addWidget(widget.review_selection_bar)

        # Shot info header (top)
        widget.shot_info_header = ShotInfoHeader()
        center_layout.addWidget(widget.shot_info_header)

        widget.session_bar = ReviewSessionBar()
        center_layout.addWidget(widget.session_bar)
        
        # Annotation toolbar
        widget.annotation_toolbar = AnnotationToolbar()
        center_layout.addWidget(widget.annotation_toolbar)

        widget.ocio_panel = OCIOPanel()
        widget.ocio_panel.hide()
        center_layout.addWidget(widget.ocio_panel)
        widget.review_selection_bar.button_ocio.toggled.connect(widget.ocio_panel.setVisible)
        
        # Video viewer with navigation overlay
        # Use a frame to contain video and overlay together
        widget.viewer_frame = QFrame()
        widget.viewer_frame.setObjectName("review_viewer_frame")
        viewer_frame_layout = QVBoxLayout(widget.viewer_frame)
        viewer_frame_layout.setContentsMargins(0, 0, 0, 0)
        viewer_frame_layout.setSpacing(0)
        
        widget.video_viewer = VideoViewerWidget()
        viewer_frame_layout.addWidget(widget.video_viewer, 1)
        
        center_layout.addWidget(widget.viewer_frame, 1)
        
        # Navigation overlay (parented to viewer_frame, positioned in logic)
        widget.shot_navigation = ShotNavigationOverlay(widget.viewer_frame)

        status_row = QWidget()
        status_row.setObjectName("review_status_row")
        status_layout = QVBoxLayout(status_row)
        status_layout.setContentsMargins(12, 4, 12, 4)
        status_layout.setSpacing(4)
        source_layout = QHBoxLayout()
        widget.review_status = FlexibleLabel()
        widget.review_status.elide_text = True
        widget.review_status.setTextFormat(Qt.TextFormat.PlainText)
        widget.review_status.setObjectName("review_status")
        source_layout.addWidget(widget.review_status, 1)
        source_layout.addWidget(QLabel("Resolution:"))
        widget.combo_resolution = ReadableComboBox(compact=True)
        for label, scale in (("Full", 1), ("Half", 2), ("Quarter", 4), ("Eighth", 8)):
            widget.combo_resolution.addItem(label, scale)
        widget.combo_resolution.setToolTip(
            "Reduce display resolution for faster playback and OCIO processing. "
            "Choose Full for detailed QC; frame position and colour settings stay the same.")
        widget.combo_resolution.currentIndexChanged.connect(
            lambda _: widget.video_viewer.set_decode_scale(widget.combo_resolution.currentData()))
        source_layout.addWidget(widget.combo_resolution)
        widget.playback_status = FlexibleLabel("No media")
        widget.playback_status.elide_text = True
        widget.playback_status.setMinimumWidth(180)
        widget.playback_status.setObjectName("review_playback_status")
        source_layout.addWidget(widget.playback_status, 1)
        status_layout.addLayout(source_layout)

        cache_layout = QHBoxLayout()
        cache_layout.addWidget(QLabel("Cache RAM:"))
        widget.combo_cache_memory = ReadableComboBox(compact=True)
        for label, megabytes in (("256 MB", 256), ("512 MB", 512), ("1 GB", 1024),
                                ("2 GB", 2048), ("4 GB", 4096), ("8 GB", 8192)):
            widget.combo_cache_memory.addItem(label, megabytes)
        widget.combo_cache_memory.setCurrentIndex(2)
        widget.combo_cache_memory.setToolTip("RAM available for loaded frames; increase it to cache longer ranges")
        widget.combo_cache_memory.currentIndexChanged.connect(widget._set_cache_memory)
        cache_layout.addWidget(widget.combo_cache_memory)
        widget.button_load_frames = QPushButton("Load Frames")
        widget.button_load_frames.setToolTip("Load the selected range into the frame cache; retry failed frames")
        widget.button_load_frames.setEnabled(False)
        widget.button_load_frames.clicked.connect(widget.video_viewer.preload_frames)
        widget.video_viewer.duration_changed.connect(lambda count: widget.button_load_frames.setEnabled(count > 0))
        cache_layout.addWidget(widget.button_load_frames)
        widget.cache_progress = QProgressBar()
        widget.cache_progress.setRange(0, 1)
        widget.cache_progress.setValue(0)
        widget.cache_progress.setFormat("No frames")
        cache_layout.addWidget(widget.cache_progress, 1)
        widget.cache_memory_label = QLabel("0 / 1024 MB")
        cache_layout.addWidget(widget.cache_memory_label)
        widget.video_viewer.cache_progress_changed.connect(widget._on_cache_progress)
        status_layout.addLayout(cache_layout)
        center_layout.addWidget(status_row)
        
        # Playback bar (bottom)
        widget.playback_bar = PlaybackBar()
        widget.playback_bar.set_total_frames(0)
        center_layout.addWidget(widget.playback_bar)
        
        main_layout.addWidget(center_widget, 1)


# =============================================================================
# REVIEW PAGE LOGIC - Signal connections and state management
# =============================================================================

class ReviewPageLogic:
    """
    Handles all logic, signals, and state management for the Review Page.
    Separates logic from UI creation.
    """
    
    def __init__(self, widget: QWidget):
        self.widget = widget
        self.shots = []
        self.current_shot_index = 0
        self.current_shot = None
        self._api = http_help.DjangoAPI()
        self._files_io = filesIO.Folders()
        self._preview_version_re = re.compile(r"_v(\d+)", re.IGNORECASE)
        self._was_playing_before_scrub = False
        self._version_position_seconds = 0.0
        self._version_playing = False
        self._version_range_seconds = None
        self._review_mode = None
        self._play_all = False
        self._play_all_loop_restore = True
    
    def connect_signals(self):
        """Connect all signals between UI components."""
        widget = self.widget
        
        # Annotation toolbar -> Canvas
        widget.annotation_toolbar.tool_selected.connect(self._on_annotation_tool)
        widget.annotation_toolbar.color_selected.connect(
            widget.video_viewer.annotation_canvas.set_color
        )
        widget.annotation_toolbar.thickness_selected.connect(
            widget.video_viewer.annotation_canvas.set_thickness
        )
        widget.annotation_toolbar.scope_selected.connect(
            widget.video_viewer.annotation_canvas.set_scope_mode
        )
        widget.annotation_toolbar.range_selected.connect(
            lambda start, end: widget.video_viewer.annotation_canvas.set_scope_range(
                start - widget.video_viewer.source_start_frame,
                end - widget.video_viewer.source_start_frame)
        )
        widget.annotation_toolbar.clear_clicked.connect(
            widget.video_viewer.annotation_canvas.clear_current_frame
        )
        widget.annotation_toolbar.undo_clicked.connect(
            widget.video_viewer.annotation_canvas.undo_last_annotation
        )
        widget.annotation_toolbar.save_thumbnail_clicked.connect(
            self._on_save_thumbnail
        )
        widget.annotation_toolbar.save_annotated_preview_clicked.connect(
            self._on_save_annotated_preview
        )
        
        # Playback bar -> Video viewer
        widget.playback_bar.play_clicked.connect(widget.video_viewer.play)
        widget.playback_bar.pause_clicked.connect(widget.video_viewer.pause)
        widget.playback_bar.frame_changed.connect(widget.video_viewer.scrub_to_frame)
        widget.playback_bar.step_forward_clicked.connect(widget.video_viewer.step_forward)
        widget.playback_bar.step_backward_clicked.connect(widget.video_viewer.step_backward)
        widget.playback_bar.go_to_start_clicked.connect(widget.video_viewer.go_to_start)
        widget.playback_bar.go_to_end_clicked.connect(widget.video_viewer.go_to_end)
        widget.playback_bar.in_point_changed.connect(widget.video_viewer.set_in_point)
        widget.playback_bar.out_point_changed.connect(widget.video_viewer.set_out_point)
        widget.playback_bar.play_range_toggled.connect(widget.video_viewer.set_play_range_enabled)
        widget.playback_bar.frame_rate_changed.connect(widget.video_viewer.set_frame_rate)
        widget.playback_bar.button_loop.toggled.connect(widget.video_viewer.set_loop_playback)
        widget.playback_bar.scrub_started.connect(self._on_scrub_started)
        widget.playback_bar.scrub_finished.connect(self._on_scrub_finished)
        
        # Video viewer -> Playback bar
        widget.video_viewer.frame_changed.connect(widget.playback_bar.set_current_frame)
        widget.video_viewer.duration_changed.connect(widget.playback_bar.set_total_frames)
        widget.video_viewer.duration_changed.connect(widget.annotation_toolbar.set_frame_range_limit)
        widget.video_viewer.playback_state_changed.connect(widget.playback_bar.set_playing)
        widget.video_viewer.frame_rate_changed.connect(widget.playback_bar.set_frame_rate)
        widget.video_viewer.source_start_changed.connect(widget.playback_bar.set_frame_offset)
        widget.video_viewer.source_start_changed.connect(widget.annotation_toolbar.set_frame_offset)
        widget.video_viewer.playback_status_changed.connect(widget.playback_status.setText)
        
        # Video viewer errors
        widget.video_viewer.error_occurred.connect(self._on_video_error)
        
        # Shot navigation
        widget.shot_navigation.previous_shot_clicked.connect(self._go_to_previous_shot)
        widget.shot_navigation.next_shot_clicked.connect(self._go_to_next_shot)
        
        # Task list
        widget.task_list_sidebar.task_selected.connect(self._on_task_selected)
        widget.task_list_sidebar.add_task_clicked.connect(self._on_add_task)

        if hasattr(widget, "shot_info_header") and hasattr(widget.shot_info_header, "combo_preview"):
            widget.shot_info_header.combo_preview.currentIndexChanged.connect(
                self._on_preview_selected
            )
        widget.shot_info_header.button_play_render.clicked.connect(self._on_play_media)
        widget.ocio_panel.processor_changed.connect(widget.video_viewer.set_ocio_processor)
        widget.ocio_panel.status_changed.connect(self._on_ocio_status)
        widget.session_bar.shot_selected.connect(self._load_shot)
        widget.session_bar.previous_shot.connect(self._go_to_previous_shot)
        widget.session_bar.next_shot.connect(self._go_to_next_shot)
        widget.session_bar.play_all_toggled.connect(self._set_play_all)
        widget.video_viewer.playback_finished.connect(self._on_playback_finished)

    def _set_play_all(self, enabled):
        viewer = self.widget.video_viewer
        button = self.widget.session_bar.button_play_all
        if enabled and viewer.total_frames:
            self._play_all = True
            self._play_all_loop_restore = viewer.loop_playback
            self.widget.playback_bar.button_loop.setChecked(False)
            self.widget.playback_bar.button_loop.setEnabled(False)
            button.setText("Stop Timeline")
            viewer.go_to_start()
            viewer.play()
        else:
            was_active = self._play_all
            self._play_all = False
            if was_active:
                viewer.pause()
                self.widget.playback_bar.button_loop.setChecked(self._play_all_loop_restore)
            self.widget.playback_bar.button_loop.setEnabled(True)
            button.blockSignals(True)
            button.setChecked(False)
            button.setText("Play Timeline")
            button.blockSignals(False)

    def _on_playback_finished(self):
        if not self._play_all:
            return
        if self.current_shot_index + 1 < len(self.shots):
            self._load_shot(self.current_shot_index + 1, continue_session=True)
            if self.widget.video_viewer.total_frames:
                self.widget.video_viewer.play()
                return
        self._set_play_all(False)

    def _on_annotation_tool(self, mode):
        if mode != AnnotationCanvas.MODE_NONE:
            self.widget.video_viewer.pause()
        self.widget.video_viewer.annotation_canvas.set_mode(mode)

    def _on_ocio_status(self, text, error):
        button = self.widget.review_selection_bar.button_ocio
        button.setText("OCIO !" if error else "OCIO")
        button.setToolTip(f"Show colour management controls\n{text}")
    
    def set_shots(self, shots: list):
        """Set the list of shots to review."""
        current_shot_id = self.current_shot.get("id") if self.current_shot else None
        current_video_path = self.current_shot.get("video_path") if self.current_shot else None
        current_preview_selection = self.current_shot.get("preview_selection") if self.current_shot else None
        self.shots = shots

        target_index = 0
        if current_shot_id is not None:
            for i, shot in enumerate(shots):
                if shot.get("id") == current_shot_id:
                    if current_video_path:
                        shot["video_path"] = current_video_path
                    if current_preview_selection:
                        shot["preview_selection"] = current_preview_selection
                    target_index = i
                    break

        self.current_shot_index = target_index
        
        if shots:
            self._load_shot(target_index)
        else:
            self._set_play_all(False)
            self.current_shot = None
            self._version_position_seconds = 0.0
            self._version_playing = False
            self._version_range_seconds = None
            self.widget.video_viewer.clear_media()
            self.widget.shot_info_header.clear()
            self.widget.task_list_sidebar.set_tasks([])
            self._set_status("No shots in this timeline.")
            self.widget.session_bar.set_shots([], 0)
        
        self._update_navigation_state()
    
    def _load_shot(self, index: int, *, continue_session=False):
        """Load a shot by index."""
        if 0 <= index < len(self.shots):
            previous_shot = self.current_shot
            previous_shot_id = previous_shot.get("id") if previous_shot else None
            self.current_shot_index = index
            self.current_shot = self.shots[index]
            shot_changed = (previous_shot_id != self.current_shot.get("id") or
                            (previous_shot_id is None and previous_shot is not self.current_shot))
            if shot_changed and self._play_all and not continue_session:
                self._set_play_all(False)
            if shot_changed:
                self._version_position_seconds = 0.0
                self._version_playing = False
                self._version_range_seconds = None
                self.widget.playback_bar.button_play_range.setChecked(False)
                self.widget.video_viewer.clear_media()
            else:
                self._remember_version_playback()
            
            # Update shot info header
            title = self.current_shot.get("title", f"Shot {index + 1}")
            self.widget.shot_info_header.set_shot_info(
                title, 
                index, 
                len(self.shots)
            )
            self.widget.shot_info_header.set_shot_metadata(self.current_shot)
            
            preview_paths, selected_path = self._get_preview_paths(self.current_shot)
            renders = discover_renders(self.current_shot.get("base_path"), self._files_io)
            saved_selection = self.current_shot.get("preview_selection")
            if self._review_mode is None:
                self._review_mode = "video" if preview_paths or self.current_shot.get("thumbnail") else "render"
            saved_mode = ((saved_selection.get("media_mode") if saved_selection.get("type") == "thumbnail"
                           else saved_selection.get("type")) if saved_selection else None)
            if self._review_mode == "render" and renders and saved_mode != "render":
                render = renders[0]
                saved_selection = {"type": "render", "value": render["render_path"], "render": render}
            self.widget.shot_info_header.set_preview_options(
                preview_paths,
                selected_path,
                thumbnail_url=self.current_shot.get("thumbnail"),
                selected_preview=saved_selection,
                render_options=renders,
                media_mode=self._review_mode,
            )
            selection = self.widget.shot_info_header.current_preview_selection()
            if selection:
                self.current_shot["preview_selection"] = selection

            self._load_selection(selection)
            
            # Load tasks
            tasks = self.current_shot.get("tasks", [])
            self.widget.task_list_sidebar.set_tasks(tasks)
            
            self._update_navigation_state()
            self.widget.session_bar.set_shots(self.shots, index)
    
    def _update_navigation_state(self):
        """Update navigation buttons based on current position."""
        has_previous = self.current_shot_index > 0
        has_next = self.current_shot_index < len(self.shots) - 1
        self.widget.shot_navigation.set_navigation_enabled(has_previous, has_next)
    
    def _go_to_previous_shot(self):
        """Navigate to the previous shot."""
        if self.current_shot_index > 0:
            self._load_shot(self.current_shot_index - 1)
    
    def _go_to_next_shot(self):
        """Navigate to the next shot."""
        if self.current_shot_index < len(self.shots) - 1:
            self._load_shot(self.current_shot_index + 1)
    
    def _on_task_selected(self, task: dict):
        """Handle task selection."""
        print(f"[Review] Task selected: {task.get('title', 'Unknown')}")
        # TODO: Could highlight task, show details, etc.

    def _on_preview_selected(self, index: int):
        """Handle preview version selection."""
        if not self.current_shot:
            return
        selection = self.widget.shot_info_header.current_preview_selection()
        if not selection:
            return
        if self._play_all:
            self._set_play_all(False)
        self._remember_version_playback()
        self.current_shot["preview_selection"] = selection
        self._review_mode = self.widget.shot_info_header._media_mode
        self._load_selection(selection)

    def _remember_version_playback(self):
        """Keep a shared comparison time, including while viewing a thumbnail."""
        viewer = self.widget.video_viewer
        if viewer.total_frames <= 0 or viewer.is_showing_still():
            return
        # An EXR seek can still be decoding when another version is selected.
        frame = viewer._sequence_target if viewer._sequence_target is not None else viewer.current_frame
        self._version_position_seconds = frame / viewer.frame_rate
        self._version_playing = viewer.is_playing()
        self._version_range_seconds = ((viewer.in_point / viewer.frame_rate, viewer.out_point / viewer.frame_rate)
                                       if viewer.use_play_range else None)

    def _load_selection(self, selection):
        if not selection:
            self.widget.video_viewer.clear_media()
            self._set_status("No renders for this shot. Use Play Preview to review the preview."
                             if self._review_mode == "render" else "No preview available for this shot.")
            return
        if selection.get("type") == "thumbnail":
            self._show_thumbnail(selection.get("value"))
            return
        path = selection.get("value")
        if not path:
            return
        is_exr = Path(path).suffix.lower() == ".exr"
        self.widget.ocio_panel.set_source(is_exr, self.current_shot.get("colourspace") if is_exr else None)
        if path == self.widget.video_viewer.current_video_path and not self.widget.video_viewer.is_showing_still():
            return
        if selection.get("type") == "video":
            self.current_shot["video_path"] = path
        source = selection.get("render", {}).get("display_name") or Path(path).name
        self.widget.shot_info_header.combo_preview.setToolTip(path)
        self._set_status(f"{'Render' if selection.get('type') == 'render' else 'Preview'}: {source}")
        self.widget.video_viewer.load_video(
            path, selection.get("render", {}).get("sequence_path"),
            start_position_seconds=self._version_position_seconds,
            source_start_frame=1001,
        )
        viewer = self.widget.video_viewer
        if self._version_range_seconds is not None and viewer.total_frames:
            low, high = (min(viewer.total_frames - 1, round(time * viewer.frame_rate))
                         for time in self._version_range_seconds)
            self.widget.playback_bar.spinbox_in_point.setValue(viewer.source_start_frame + low)
            self.widget.playback_bar.spinbox_out_point.setValue(viewer.source_start_frame + high)
            self.widget.playback_bar.button_play_range.setChecked(True)
        if self._version_playing:
            self.widget.video_viewer.play()

    def _on_play_media(self):
        if not self.current_shot:
            return
        header = self.widget.shot_info_header
        target_type = header.play_media_target_type()
        current_selection = header.current_preview_selection() or {}
        renders = discover_renders(self.current_shot.get("base_path"), self._files_io)
        paths, selected_path = self._get_preview_paths(self.current_shot)
        if target_type == "render" and renders:
            render = next((item for item in renders
                           if current_selection.get("type") == "render"
                           and item["render_path"] == current_selection.get("value")), renders[0])
            target_selection = {"type": "render", "value": render["render_path"], "render": render}
        elif target_type == "video" and paths:
            target_selection = {"type": "video", "value": str(selected_path or paths[0])}
        else:
            header.set_preview_options(paths, selected_path, self.current_shot.get("thumbnail"),
                                       selected_preview=current_selection, render_options=renders)
            self._set_status("No preview or render available for this shot.")
            return
        header.set_preview_options(paths, selected_path, self.current_shot.get("thumbnail"),
                                   selected_preview=target_selection, render_options=renders)
        self._on_preview_selected(header.combo_preview.currentIndex())
        self.widget.video_viewer.play()

    def _set_status(self, text):
        self.widget.review_status.setText(text)
        self.widget.review_status.setToolTip(text)
        self.widget.review_status.setVisible(bool(text))

    def _show_thumbnail(self, thumbnail_url: str | None):
        """Load and display a thumbnail image."""
        self.widget.video_viewer.clear_media()
        if not thumbnail_url:
            self._set_status("No thumbnail available.")
            return
        image = QImage()
        if thumbnail_url.startswith("http://") or thumbnail_url.startswith("https://"):
            try:
                response = self._api._request("GET", thumbnail_url)
                response.raise_for_status()
                if not image.loadFromData(response.content):
                    self._set_status("Failed to decode thumbnail image.")
                    return
            except Exception as exc:
                self._set_status(f"Failed to load thumbnail: {exc}")
                return
        else:
            local_path = thumbnail_url
            if local_path.startswith("file://"):
                local_path = local_path[7:]
            if not image.load(local_path):
                self._set_status(f"Thumbnail file not found: {local_path}")
                return
        self.widget.video_viewer.show_still(image)
        self._set_status("Thumbnail")
    
    def _on_add_task(self):
        """Handle add task button click."""
        if not self.current_shot:
            return
        shot_id = self.current_shot.get("id")
        if not shot_id:
            return
        try:
            dlg = TaskCreateDialog(self.widget, api=self._api)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            values = dlg.get_values()
            new_task = self._api.create_task(shot_id=shot_id, title=values.get("title", "New Task"))
            update_payload = {}
            if values.get("notes"):
                update_payload["notes"] = values["notes"]
            if values.get("status") is not None:
                update_payload["status"] = values["status"]
            if values.get("priority") is not None:
                update_payload["priority"] = values["priority"]
            if values.get("budget_hours") is not None:
                update_payload["budget_hours"] = values["budget_hours"]
            if values.get("artist") is not None:
                update_payload["artist"] = values["artist"]
            if update_payload:
                new_task = self._api.update_task(new_task.get("id"), **update_payload)
        except Exception as exc:
            print(f"[Review] Add task failed: {exc}")
            return
        tasks = self.current_shot.get("tasks") or []
        tasks.append(new_task)
        self.current_shot["tasks"] = tasks
        self.widget.task_list_sidebar.set_tasks(tasks)

    def _get_preview_paths(self, shot: dict):
        preview_paths = []
        selected_path = None

        video_path = shot.get("video_path")
        preview_video = shot.get("preview_video")
        base_path = shot.get("base_path")

        if video_path and Path(video_path).suffix.lower() != ".exr":
            candidate = Path(video_path)
            if candidate.exists():
                selected_path = candidate
                preview_paths.append(candidate)

        if preview_video:
            preview_path = Path(preview_video)
            if preview_path.is_absolute():
                candidate = Path(self._files_io.convert_path(preview_video))
            elif base_path:
                candidate = Path(self._files_io.convert_path(base_path)) / preview_video
            else:
                candidate = None
            if candidate and candidate.exists():
                preview_paths.append(candidate)
                if selected_path is None:
                    selected_path = candidate

        if base_path:
            preview_dir = Path(self._files_io.convert_path(base_path)) / "renders" / "precomp" / "previews"
            if preview_dir.exists():
                for ext in (".mp4", ".mov", ".m4v"):
                    preview_paths.extend(preview_dir.glob(f"*{ext}"))

        preview_paths = self._sort_preview_paths(preview_paths)

        if selected_path and selected_path not in preview_paths:
            preview_paths.insert(0, selected_path)

        return preview_paths, selected_path

    def _sort_preview_paths(self, preview_paths: list):
        seen = set()
        unique = []
        for path in preview_paths:
            path_str = str(path)
            if path_str not in seen:
                unique.append(path)
                seen.add(path_str)

        def sort_key(path: Path):
            match = self._preview_version_re.search(path.name)
            version = int(match.group(1)) if match else -1
            no_version = 0 if match else 1
            return (no_version, version, path.name.lower())

        return sorted(unique, key=sort_key, reverse=True)
    
    def _on_save_thumbnail(self):
        """Handle save as thumbnail button click."""
        if not self.current_shot:
            return
        shot_id = self.current_shot.get("id")
        if not shot_id:
            return
        image = self.widget.video_viewer.capture_annotated_frame()
        if image is None or image.isNull():
            print("[Review] No frame available for thumbnail")
            return

        output_dir = Path("media") / "review_thumbnails"
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time())
        filename = f"shot_{shot_id}_frame_{self.widget.video_viewer.current_frame}_{timestamp}.png"
        output_path = output_dir / filename

        if not image.save(str(output_path), "PNG"):
            print(f"[Review] Failed to save thumbnail: {output_path}")
            return

        try:
            updated = self._api.upload_shot_thumbnail(shot_id, str(output_path))
        except Exception as exc:
            print(f"[Review] Thumbnail upload failed: {exc}")
            return

        thumb_path = updated.get("thumbnail")
        if thumb_path:
            base_url = self._api.base_url
            if base_url.endswith("/api/"):
                base_url = base_url[:-5]
            elif base_url.endswith("/api"):
                base_url = base_url[:-4]
            self.current_shot["thumbnail"] = f"{base_url}{thumb_path}"
            preview_paths, selected_path = self._get_preview_paths(self.current_shot)
            self.widget.shot_info_header.set_preview_options(
                preview_paths,
                selected_path,
                thumbnail_url=self.current_shot.get("thumbnail"),
                selected_preview=self.current_shot.get("preview_selection"),
                render_options=discover_renders(self.current_shot.get("base_path"), self._files_io),
            )

    def _on_save_annotated_preview(self):
        """Handle save annotated preview button click."""
        print("[Review] Save annotated preview clicked")

    def _on_scrub_started(self):
        """Enter live scrubbing mode for the timeline."""
        self._was_playing_before_scrub = self.widget.video_viewer.is_playing()
        self.widget.video_viewer.pause()
        self.widget.video_viewer.begin_scrub()

    def _on_scrub_finished(self):
        """Exit scrubbing mode and restore playback state."""
        final_frame = self.widget.playback_bar.slider_timeline.value()
        self.widget.video_viewer.end_scrub(final_frame, self._was_playing_before_scrub)
        self._was_playing_before_scrub = False
    
    def _on_video_error(self, error_message: str):
        """Handle video playback errors."""
        print(f"[Review] Video error: {error_message}")
        self.widget.video_viewer.pause()
        self._set_play_all(False)
        self.widget.playback_status.setText("Playback error")
        self._set_status(error_message)


# =============================================================================
# REVIEW PAGE - Main widget combining UI and Logic
# =============================================================================

class ReviewPage(QWidget):
    """
    Main Review Page widget.
    Combines ReviewPageUI layout with ReviewPageLogic for functionality.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        
        # Set up UI
        self.ui = ReviewPageUI()
        self.ui.setup_ui(self)
        self.setFocusProxy(self.center_widget)
        self._presentation_window = None
        self._presentation_visibility = {}
        
        # Set up logic
        self.logic = ReviewPageLogic(self)
        self.logic.connect_signals()
        self.session_bar.tools_toggled.connect(self.annotation_toolbar.setVisible)
        self.session_bar.present_toggled.connect(self._set_presentation_mode)

        # Keyboard shortcuts
        self._setup_shortcuts()

        # Arrow key repeat handling
        self._nudge_timer = QTimer(self)
        self._nudge_timer.timeout.connect(self._on_nudge_tick)
        self._nudge_direction = 0
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        
        # Position navigation overlay after UI is set up
        self._reposition_navigation_overlay()
    
    def set_shots(self, shots: list):
        """
        Set the list of shots to review.
        
        Each shot dict should contain:
        - id: Shot ID
        - title: Shot title
        - video_path: Path to MP4 file (optional)
        - tasks: List of task dicts (optional)
        - thumbnail: URL to thumbnail (optional)
        """
        self.logic.set_shots(shots)

    def set_settings_manager(self, settings):
        self.ocio_panel.set_settings_manager(settings)
        self._review_settings = settings
        index = self.combo_cache_memory.findData(settings.get("review_cache_mb", 1024))
        self.combo_cache_memory.blockSignals(True)
        self.combo_cache_memory.setCurrentIndex(index if index >= 0 else 2)
        self.combo_cache_memory.blockSignals(False)
        self.video_viewer.set_cache_limit_mb(self.combo_cache_memory.currentData())

    def _set_cache_memory(self, index):
        megabytes = self.combo_cache_memory.itemData(index)
        if megabytes is None:
            return
        self.video_viewer.set_cache_limit_mb(megabytes)
        settings = getattr(self, "_review_settings", None)
        if settings is not None:
            settings.set("review_cache_mb", megabytes)

    def _on_cache_progress(self, ready, total, used, limit):
        self.cache_progress.setRange(0, max(1, total))
        self.cache_progress.setValue(ready)
        self.cache_progress.setFormat("%v / %m frames" if total else "No frames")
        self.cache_memory_label.setText(f"{used / 1024**2:.0f} / {limit / 1024**2:.0f} MB")
        tooltip = f"{ready}/{total} frames loaded at the selected resolution and OCIO settings."
        if total and len(self.video_viewer._cache_plan) < total:
            tooltip += " Increase Cache RAM or shorten In/Out to cache the whole range. Playback loads sections when needed."
        self.cache_progress.setToolTip(tooltip)
        if ready == total and total and not self.video_viewer._cache_errors:
            kind = "Render" if self.logic._review_mode == "render" else "Preview"
            self.logic._set_status(f"{kind}: {Path(self.video_viewer.current_video_path).name}")

    def _set_presentation_mode(self, enabled):
        if not enabled:
            self._finish_presentation()
            return
        if self._presentation_window is not None:
            return
        window = ReviewPresentationWindow(self.window())
        self._presentation_window = window
        self._presentation_visibility = {
            self.review_selection_bar: not self.review_selection_bar.isHidden(),
            self.ocio_panel: not self.ocio_panel.isHidden(),
        }
        self._presentation_tools_visible = self.session_bar.button_tools.isChecked()
        self.layout().removeWidget(self.center_widget)
        window.layout().addWidget(self.center_widget)
        for widget in self._presentation_visibility:
            widget.hide()
        self.session_bar.button_tools.setChecked(False)
        self.session_bar.button_present.setText("Exit Present")
        window.closing.connect(self._finish_presentation)
        window.showFullScreen()
        self.center_widget.show()
        self.center_widget.setFocus()
        QTimer.singleShot(0, self._reposition_navigation_overlay)

    def _finish_presentation(self):
        window = self._presentation_window
        if window is None:
            return
        self._presentation_window = None
        window.layout().removeWidget(self.center_widget)
        self.layout().addWidget(self.center_widget, 1)
        self.center_widget.show()
        for widget, visible in self._presentation_visibility.items():
            widget.setVisible(visible)
        self.session_bar.button_tools.setChecked(self._presentation_tools_visible)
        button = self.session_bar.button_present
        button.blockSignals(True)
        button.setChecked(False)
        button.setText("Present")
        button.blockSignals(False)
        window.close()
        window.deleteLater()
        self.center_widget.setFocus()
        QTimer.singleShot(0, self._reposition_navigation_overlay)

    def set_job_options(self, jobs: list):
        """Populate job selector options."""
        if hasattr(self, "review_selection_bar"):
            self.review_selection_bar.set_jobs(jobs)

    def set_timeline_options(self, timelines: list):
        """Populate timeline selector options."""
        if hasattr(self, "review_selection_bar"):
            self.review_selection_bar.set_timelines(timelines)

    def set_selected_job_id(self, job_id: int | None):
        """Update the selected job in the review selector."""
        if hasattr(self, "review_selection_bar"):
            self.review_selection_bar.set_selected_job(job_id)

    def set_selected_timeline_id(self, timeline_id: int | None):
        """Update the selected timeline in the review selector."""
        if hasattr(self, "review_selection_bar"):
            self.review_selection_bar.set_selected_timeline(timeline_id)
    
    def resizeEvent(self, event):
        """Handle resize to reposition navigation overlay."""
        super().resizeEvent(event)
        self._reposition_navigation_overlay()
    
    def showEvent(self, event):
        """Handle show event to position overlay correctly."""
        super().showEvent(event)
        # Use a timer to ensure layout is complete before repositioning
        QTimer.singleShot(10, self._reposition_navigation_overlay)

    def closeEvent(self, event):
        self._finish_presentation()
        self.logic._set_play_all(False)
        self._stop_nudge()
        self.video_viewer.clear_media()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.viewer_frame and event.type() == QEvent.Type.Resize:
            self._reposition_navigation_overlay()
        owner = obj if isinstance(obj, QWidget) else None
        # QWidget.isAncestorOf stops at popup windows. Their parent chain still
        # identifies the owning combo, so include Review's dropdown menus.
        while owner is not None and owner is not self and owner is not self.center_widget:
            owner = owner.parentWidget()
        if owner is None:
            return super().eventFilter(obj, event)
        if event.type() == QEvent.Type.ShortcutOverride and self._shortcut_blocked():
            # Rejecting a shortcut in its callback is too late: Qt has already
            # consumed the key. Let open menus and focused controls handle it.
            review_keys = {
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                Qt.Key.Key_PageUp, Qt.Key.Key_PageDown, Qt.Key.Key_Space,
                Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_L, Qt.Key.Key_F11,
                Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight, Qt.Key.Key_Backslash,
            }
            if event.modifiers() in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier) and event.key() in review_keys:
                event.accept()
                return True
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                if event.modifiers() != Qt.KeyboardModifier.NoModifier:
                    return False
                if self._shortcut_blocked():
                    return False
                if event.isAutoRepeat():
                    return True
                direction = -1 if key == Qt.Key.Key_Left else 1
                self._start_nudge(direction)
                return True
        elif event.type() == QEvent.Type.KeyRelease:
            key = event.key()
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                if event.modifiers() != Qt.KeyboardModifier.NoModifier:
                    return False
                if self._shortcut_blocked():
                    return False
                if event.isAutoRepeat():
                    return True
                self._stop_nudge()
                return True
        return super().eventFilter(obj, event)

    def _setup_shortcuts(self):
        shortcuts = (
            ("Space", self._on_shortcut_play_pause), ("L", self._on_shortcut_play_forward),
            ("J", self._on_shortcut_play_backward), ("K", self._on_shortcut_stop),
            ("Up", self._on_shortcut_prev_preview), ("Down", self._on_shortcut_next_preview),
            ("PgUp", self.logic._go_to_previous_shot), ("PgDown", self.logic._go_to_next_shot),
            ("[", self._set_review_in), ("]", self._set_review_out),
            ("\\", self._reset_review_range),
            ("F11", lambda: self.session_bar.button_present.toggle()),
        )
        self._review_shortcuts = []
        for key, callback in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self.center_widget)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(lambda callback=callback: self._run_review_shortcut(callback))
            self._review_shortcuts.append(shortcut)

    def _run_review_shortcut(self, callback):
        if not self._shortcut_blocked():
            callback()

    def _set_review_in(self):
        if self.video_viewer.total_frames:
            self.playback_bar.spinbox_in_point.setValue(
                self.video_viewer.source_start_frame + self.video_viewer.current_frame)
            self.playback_bar.button_play_range.setChecked(True)

    def _set_review_out(self):
        if self.video_viewer.total_frames:
            self.playback_bar.spinbox_out_point.setValue(
                self.video_viewer.source_start_frame + self.video_viewer.current_frame)
            self.playback_bar.button_play_range.setChecked(True)

    def _reset_review_range(self):
        self.playback_bar.spinbox_in_point.setValue(self.video_viewer.source_start_frame)
        self.playback_bar.spinbox_out_point.setValue(
            self.video_viewer.source_start_frame + max(0, self.video_viewer.total_frames - 1))
        self.playback_bar.button_play_range.setChecked(False)

    def _shortcut_blocked(self) -> bool:
        focus_widget = QApplication.focusWidget()
        if not self.center_widget.isVisible() or QApplication.activeWindow() != self.center_widget.window():
            return True
        if focus_widget is not None and not (focus_widget is self or focus_widget is self.center_widget
                                             or self.center_widget.isAncestorOf(focus_widget)):
            return True
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox)):
            return True
        if QApplication.activePopupWidget() is not None:
            return True
        return False

    def _nudge_interval_ms(self) -> int:
        fps = max(1.0, getattr(self.video_viewer, "frame_rate", 24.0))
        return max(12, int(round(1000.0 / (fps * 2.0))))

    def _start_nudge(self, direction: int):
        if direction not in (-1, 1):
            return
        self._nudge_direction = direction
        if direction < 0:
            self.video_viewer.step_backward(1)
        else:
            self.video_viewer.step_forward(1)
        self._nudge_timer.setInterval(self._nudge_interval_ms())
        if not self._nudge_timer.isActive():
            self._nudge_timer.start()

    def _stop_nudge(self):
        self._nudge_timer.stop()
        self._nudge_direction = 0

    def _on_nudge_tick(self):
        if self._shortcut_blocked():
            self._stop_nudge()
            return
        if self._nudge_direction < 0:
            self.video_viewer.step_backward(1)
        elif self._nudge_direction > 0:
            self.video_viewer.step_forward(1)

    def _on_shortcut_play_pause(self):
        if self._shortcut_blocked():
            return
        self.video_viewer.toggle_playback()

    def _on_shortcut_play_forward(self):
        if self._shortcut_blocked():
            return
        self.video_viewer.play()

    def _on_shortcut_play_backward(self):
        if self._shortcut_blocked():
            return
        self.video_viewer.play_backward()

    def _on_shortcut_stop(self):
        if self._shortcut_blocked():
            return
        self.video_viewer.pause()

    def _on_shortcut_step_back(self):
        if self._shortcut_blocked():
            return
        self.video_viewer.step_backward(1)

    def _on_shortcut_step_forward(self):
        if self._shortcut_blocked():
            return
        self.video_viewer.step_forward(1)

    def _on_shortcut_prev_preview(self):
        if self._shortcut_blocked():
            return
        combo = getattr(self.shot_info_header, "combo_preview", None)
        if not combo or not combo.isEnabled():
            return
        index = combo.currentIndex()
        if index > 0:
            combo.setCurrentIndex(index - 1)

    def _on_shortcut_next_preview(self):
        if self._shortcut_blocked():
            return
        combo = getattr(self.shot_info_header, "combo_preview", None)
        if not combo or not combo.isEnabled():
            return
        index = combo.currentIndex()
        if index + 1 < combo.count():
            combo.setCurrentIndex(index + 1)

    def _reposition_navigation_overlay(self):
        """Reposition the shot navigation overlay over the viewer frame."""
        if hasattr(self, 'shot_navigation') and hasattr(self, 'viewer_frame'):
            # Position overlay to fill the viewer frame
            self.shot_navigation.setGeometry(self.viewer_frame.rect())
            self.shot_navigation.raise_()


# =============================================================================
# DEBUG / STANDALONE TESTING
# =============================================================================

def find_video_files(folder_path: str) -> list:
    """
    Scan a folder for video files and return a list of shot dicts.
    Supports: .mp4, .mov, .avi, .mkv, .webm
    """
    import os
    from pathlib import Path
    
    video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}
    shots = []
    
    folder = Path(folder_path)
    if not folder.exists():
        print(f"[Warning] Folder not found: {folder_path}")
        return shots
    
    # Find all video files
    video_files = []
    for ext in video_extensions:
        video_files.extend(folder.glob(f"*{ext}"))
        video_files.extend(folder.glob(f"*{ext.upper()}"))
    
    # Sort by name
    video_files = sorted(set(video_files), key=lambda p: p.name.lower())
    
    # Create shot dicts
    for idx, video_path in enumerate(video_files):
        shot = {
            "id": idx + 1,
            "title": video_path.stem,  # Filename without extension
            "video_path": str(video_path),
            "tasks": [
                {"id": idx * 10 + 1, "title": "Review", "status": "in_progress"},
            ]
        }
        shots.append(shot)
        print(f"  Found: {video_path.name}")
    
    return shots


if __name__ == "__main__":
    import sys
    import os
    from PyQt6.QtWidgets import QApplication
    
    app = QApplication(sys.argv)
    
    # Use the application's shared theme when previewing Review independently.
    theme_path = Path(__file__).resolve().parent / "ui" / "dark_v01.qss"
    app.setStyleSheet(theme_path.read_text(encoding="utf-8"))

    # Create and show the review page
    review_page = ReviewPage()
    review_page.setWindowTitle("ShotBox Review - Debug Mode")
    review_page.resize(1200, 800)
    
    # === CONFIGURE TEST VIDEO FOLDER HERE ===
    # Change this path to your video folder for testing
    TEST_VIDEO_FOLDER = "/home/rockybtw/Documents/projects/test/Nuke/test2/sho010/renders/precomp/previews"
    
    # Alternative: pass folder as command line argument
    # Usage: python review_page.py /path/to/videos
    if len(sys.argv) > 1:
        TEST_VIDEO_FOLDER = sys.argv[1]
    
    print("=" * 60)
    print("ShotBox Review Page - Debug Mode")
    print("=" * 60)
    
    # Try to load videos from folder
    if os.path.exists(TEST_VIDEO_FOLDER):
        print(f"Scanning folder: {TEST_VIDEO_FOLDER}")
        test_shots = find_video_files(TEST_VIDEO_FOLDER)
        
        if test_shots:
            print(f"\nLoaded {len(test_shots)} video(s)")
        else:
            print("\nNo video files found in folder!")
            test_shots = []
    else:
        print(f"Folder not found: {TEST_VIDEO_FOLDER}")
        print("\nUsing placeholder test data instead.")
        print("To test with real videos:")
        print("  1. Edit TEST_VIDEO_FOLDER in the script, or")
        print("  2. Run: python review_page.py /path/to/your/videos")
        
        # Fallback test data
        test_shots = [
            {
                "id": 1,
                "title": "Shot_010 (No Video)",
                "video_path": "",
                "tasks": [
                    {"id": 1, "title": "Comp", "status": "in_progress"},
                    {"id": 2, "title": "Roto", "status": "approved"},
                    {"id": 3, "title": "Paint", "status": "not_started"},
                ]
            },
            {
                "id": 2,
                "title": "Shot_020 (No Video)",
                "video_path": "",
                "tasks": [
                    {"id": 4, "title": "Comp", "status": "assigned"},
                ]
            },
        ]
    
    print("=" * 60)
    
    review_page.set_shots(test_shots)
    review_page.show()
    
    sys.exit(app.exec())
