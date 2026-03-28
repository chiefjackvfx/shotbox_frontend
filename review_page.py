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
    QFrame, QSizePolicy, QSlider, QSpinBox, QComboBox,
    QScrollArea, QSplitter, QToolButton, QButtonGroup,
    QLineEdit, QTextEdit, QPlainTextEdit,
    QColorDialog, QSpacerItem, QStackedWidget, QInputDialog, QApplication, QDialog
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot, QSize, QPoint, QRect, QEvent
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
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
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
            self.is_drawing = False
            
            # Create annotation based on mode
            annotation = {
                "type": self.current_mode,
                "color": QColor(self.current_color),
                "thickness": self.current_thickness,
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
            pen = QPen(annotation["color"])
            pen.setWidth(annotation["thickness"])
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self._frame_image = QImage()
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color: #0a0a0a;")

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
            scaled = self._frame_image.scaled(
                target_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            painter.drawImage(target_rect.topLeft(), scaled)
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
    video_loaded = pyqtSignal(str)  # Emits file path when loaded
    error_occurred = pyqtSignal(str)  # Emits error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background-color: #1a1a1a;")

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

        # PyAV decoder and playback timer
        self.reader = PyAVFrameReader()
        self.play_timer = QTimer(self)
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
        self._cache_size = 24
        self._cache_lookback = 8

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

    def load_video(self, file_path: str):
        """Load a video file."""
        import os

        if not file_path:
            print("[VideoViewer] No file path provided")
            return

        if not os.path.exists(file_path):
            error_msg = f"Video file not found: {file_path}"
            print(f"[VideoViewer] {error_msg}")
            self.error_occurred.emit(error_msg)
            return

        print(f"[VideoViewer] Loading: {file_path}")
        self.stop()
        self.reader.close()
        self.current_video_path = file_path
        self._showing_still = False
        self._frame_cache.clear()
        self._cache_order.clear()

        # Clear annotations for new video
        self.annotation_canvas.clear_all_annotations()

        try:
            self.reader.open(file_path)
        except Exception as exc:
            error_msg = f"Failed to load video: {exc}"
            print(f"[VideoViewer] {error_msg}")
            self.error_occurred.emit(error_msg)
            return

        self.frame_rate = self.reader.fps
        self.total_frames = max(1, self.reader.total_frames)
        self.duration_ms = self.reader.duration_ms
        self.in_point = 0
        self.out_point = max(0, self.total_frames - 1)
        self._update_playback_interval()

        self.duration_changed.emit(self.total_frames)
        self.video_loaded.emit(file_path)
        self.seek_to_frame(0)

    def is_showing_still(self) -> bool:
        return self._showing_still

    def show_still(self, image: QImage):
        """Display a still image without changing playback state."""
        if image is None or image.isNull():
            return
        self.pause()
        self._showing_still = True
        self.video_widget.set_frame_image(image)
        self._update_canvas_geometry()

    def is_playing(self) -> bool:
        return self._is_playing

    def play(self):
        """Start playback."""
        if self.total_frames <= 0:
            return
        if self.use_play_range and self.current_frame < self.in_point:
            self.seek_to_frame(self.in_point)
        self._play_direction = 1
        if not self._is_playing:
            self._is_playing = True
            self._update_playback_interval()
            self.play_timer.start()
            self.playback_state_changed.emit(True)

    def play_backward(self):
        """Start reverse playback."""
        if self.total_frames <= 0:
            return
        if self.use_play_range and self.current_frame > self.out_point:
            self.seek_to_frame(self.out_point)
        self._play_direction = -1
        if not self._is_playing:
            self._is_playing = True
            self._update_playback_interval()
            self.play_timer.start()
            self.playback_state_changed.emit(True)

    def pause(self):
        """Pause playback."""
        if self._is_playing:
            self.play_timer.stop()
            self._is_playing = False
            self.playback_state_changed.emit(False)

    def toggle_playback(self):
        """Toggle between play and pause."""
        if self._is_playing:
            self.pause()
        else:
            self.play()

    def stop(self):
        """Stop playback and return to start."""
        self.pause()
        self.seek_to_frame(0)

    def seek_to_frame(self, frame: int):
        """Seek to a specific frame."""
        if self.total_frames <= 0:
            return
        frame = max(0, min(frame, self.total_frames - 1))
        try:
            decoded_frame, index = self.reader.seek_to_frame(frame)
        except Exception as exc:
            error_msg = f"Frame seek failed: {exc}"
            print(f"[VideoViewer] {error_msg}")
            self.error_occurred.emit(error_msg)
            return
        if decoded_frame is None:
            return
        self._present_frame(decoded_frame, index)

    def seek_to_position_ms(self, position_ms: int):
        """Seek to a specific position in milliseconds."""
        if self.frame_rate <= 0:
            return
        position_ms = max(0, min(position_ms, self.duration_ms))
        frame = int((position_ms / 1000.0) * self.frame_rate)
        self.seek_to_frame(frame)

    def step_forward(self, frames: int = 1):
        """Step forward by specified frames."""
        was_playing = self._is_playing
        if was_playing:
            self.pause()
        if self.total_frames <= 0:
            return
        max_frame = self.out_point if self.use_play_range else self.total_frames - 1
        target_frame = min(self.current_frame + frames, max_frame)
        cached = self._frame_cache.get(target_frame)
        if cached is not None:
            self._present_cached_frame(target_frame, cached)
            return
        if frames == 1 and not self._showing_still and self.current_frame < max_frame:
            try:
                decoded_frame, index = self.reader.decode_next()
            except Exception as exc:
                error_msg = f"Frame decode failed: {exc}"
                print(f"[VideoViewer] {error_msg}")
                self.error_occurred.emit(error_msg)
                return
            if decoded_frame is not None:
                if self.use_play_range and index > max_frame:
                    self.seek_to_frame(max_frame)
                else:
                    self._present_frame(decoded_frame, index)
                return
        self.seek_to_frame(target_frame)

    def step_backward(self, frames: int = 1):
        """Step backward by specified frames."""
        was_playing = self._is_playing
        if was_playing:
            self.pause()
        if self.total_frames <= 0:
            return
        min_frame = self.in_point if self.use_play_range else 0
        target_frame = max(self.current_frame - frames, min_frame)
        cached = self._frame_cache.get(target_frame)
        if cached is not None:
            self._present_cached_frame(target_frame, cached)
            return
        if frames == 1 and not self._showing_still and target_frame > min_frame:
            self._prefetch_backwards(target_frame, min_frame)
            cached = self._frame_cache.get(target_frame)
            if cached is not None:
                self._present_cached_frame(target_frame, cached)
                return
        self.seek_to_frame(target_frame)

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

    def set_out_point(self, frame: int):
        """Set the out point for play range."""
        self.out_point = min(max(0, self.total_frames - 1), max(frame, self.in_point))

    def set_play_range_enabled(self, enabled: bool):
        """Enable or disable play range limiting."""
        self.use_play_range = enabled

    def set_frame_rate(self, fps: float):
        """Set the frame rate for frame calculations."""
        if fps > 0:
            self.frame_rate = fps
            if self.duration_ms > 0:
                self.total_frames = max(1, int((self.duration_ms / 1000.0) * self.frame_rate))
                self.out_point = max(0, self.total_frames - 1)
                self.duration_changed.emit(self.total_frames)
            self._update_playback_interval()

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
        """Advance playback by one frame."""
        if self.total_frames <= 0:
            self.pause()
            return

        if self.use_play_range:
            if self._play_direction >= 0:
                if self.current_frame < self.in_point:
                    self.seek_to_frame(self.in_point)
                    return
                if self.current_frame >= self.out_point:
                    if self.loop_playback:
                        self.seek_to_frame(self.in_point)
                    else:
                        self.pause()
                    return
            else:
                if self.current_frame > self.out_point:
                    self.seek_to_frame(self.out_point)
                    return
                if self.current_frame <= self.in_point:
                    if self.loop_playback:
                        self.seek_to_frame(self.out_point)
                    else:
                        self.pause()
                    return

        if self._play_direction >= 0:
            try:
                decoded_frame, index = self.reader.decode_next()
            except Exception as exc:
                error_msg = f"Frame decode failed: {exc}"
                print(f"[VideoViewer] {error_msg}")
                self.error_occurred.emit(error_msg)
                self.pause()
                return
        else:
            target = self.current_frame - 1
            if target < 0:
                if self.loop_playback:
                    target = self.out_point if self.use_play_range else self.total_frames - 1
                else:
                    self.pause()
                    return
            try:
                decoded_frame, index = self.reader.seek_to_frame(target)
            except Exception as exc:
                error_msg = f"Frame decode failed: {exc}"
                print(f"[VideoViewer] {error_msg}")
                self.error_occurred.emit(error_msg)
                self.pause()
                return

        if decoded_frame is None:
            if self.loop_playback:
                if self._play_direction >= 0:
                    start_frame = self.in_point if self.use_play_range else 0
                    self.seek_to_frame(start_frame)
                else:
                    end_frame = self.out_point if self.use_play_range else self.total_frames - 1
                    self.seek_to_frame(end_frame)
            else:
                self.pause()
            return

        if self.use_play_range:
            if self._play_direction >= 0 and index > self.out_point:
                if self.loop_playback:
                    self.seek_to_frame(self.in_point)
                else:
                    self.pause()
                return
            if self._play_direction < 0 and index < self.in_point:
                if self.loop_playback:
                    self.seek_to_frame(self.out_point)
                else:
                    self.pause()
                return

        self._present_frame(decoded_frame, index)

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

    def _present_cached_frame(self, index: int, image: QImage):
        self.video_widget.set_frame_image(image)
        self._update_canvas_geometry()
        self.current_frame = index
        self.annotation_canvas.set_frame(self.current_frame)
        self.frame_changed.emit(self.current_frame)

    def _frame_to_qimage(self, frame):
        try:
            array = frame.to_ndarray(format="rgb24")
        except Exception:
            return None
        if not array.flags["C_CONTIGUOUS"]:
            array = np.ascontiguousarray(array)
        height, width, _ = array.shape
        bytes_per_line = 3 * width
        image = QImage(array.data, width, height, bytes_per_line, QImage.Format.Format_RGB888)
        return image.copy()

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
        self.annotation_canvas.setGeometry(target_rect)
        self.annotation_canvas.raise_()

    def _cache_frame(self, index: int, image: QImage):
        if index in self._frame_cache:
            return
        self._frame_cache[index] = image
        self._cache_order.append(index)
        while len(self._cache_order) > self._cache_size:
            old_index = self._cache_order.popleft()
            self._frame_cache.pop(old_index, None)

    def _prefetch_backwards(self, target_frame: int, min_frame: int):
        start_frame = max(min_frame, target_frame - self._cache_lookback + 1)
        try:
            decoded_frame, index = self.reader.seek_to_frame(start_frame)
        except Exception as exc:
            error_msg = f"Frame decode failed: {exc}"
            print(f"[VideoViewer] {error_msg}")
            self.error_occurred.emit(error_msg)
            return
        if decoded_frame is None:
            return
        image = self._frame_to_qimage(decoded_frame)
        if image is not None:
            self._cache_frame(index, image)
        while index < target_frame:
            decoded_frame, index = self.reader.decode_next()
            if decoded_frame is None:
                break
            image = self._frame_to_qimage(decoded_frame)
            if image is not None:
                self._cache_frame(index, image)

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
    scrub_started = pyqtSignal()
    scrub_finished = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(90)
        self.setObjectName("playback_bar")
        
        self.total_frames = 100
        self.current_frame = 0
        self.in_point = 0
        self.out_point = 100
        self.is_playing = False
        
        self._apply_style()
        self._setup_ui()

    def _apply_style(self):
        self.setStyleSheet("""
            #playback_bar {
                background-color: #2b2b2b;
            }
            #playback_bar QPushButton {
                background-color: #3a3a3a;
                border: 1px solid #4a4a4a;
                border-radius: 3px;
                padding: 2px 6px;
                font-size: 14px;
            }
            #playback_bar QPushButton:hover {
                background-color: #4a4a4a;
            }
            #playback_bar QPushButton:pressed {
                background-color: #2f2f2f;
            }
            #playback_bar QPushButton:checked {
                background-color: #515151;
                border-color: #7a7a7a;
            }
            #playback_bar QLabel {
                color: #d0d0d0;
            }
            #playback_bar QSpinBox, #playback_bar QComboBox {
                background-color: #252525;
                border: 1px solid #444;
                padding: 2px 4px;
                border-radius: 2px;
            }
            #playback_bar QSlider::groove:horizontal {
                height: 8px;
                background: #3a3a3a;
                border-radius: 4px;
            }
            #playback_bar QSlider::handle:horizontal {
                background: #c07a2e;
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            #playback_bar QSlider::handle:horizontal:hover {
                background: #d08a3a;
            }
        """)

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
        self.button_play_range = QPushButton("⟷")
        self.button_play_range.setFixedSize(36, 32)
        self.button_play_range.setCheckable(True)
        self.button_play_range.setToolTip("Use In/Out Range")
        self.button_play_range.toggled.connect(self.play_range_toggled.emit)
        controls_layout.addWidget(self.button_play_range)
        
        controls_layout.addSpacing(10)
        
        # Go to start
        self.button_go_to_start = QPushButton("⏮")
        self.button_go_to_start.setFixedSize(36, 32)
        self.button_go_to_start.setToolTip("Go to Start")
        self.button_go_to_start.clicked.connect(self.go_to_start_clicked.emit)
        controls_layout.addWidget(self.button_go_to_start)
        
        # Step backward 10
        self.button_step_back_10 = QPushButton("⏪")
        self.button_step_back_10.setFixedSize(36, 32)
        self.button_step_back_10.setToolTip("Step Back 10 Frames")
        self.button_step_back_10.clicked.connect(lambda: self.step_backward_clicked.emit(10))
        controls_layout.addWidget(self.button_step_back_10)
        
        # Step backward 1
        self.button_step_back_1 = QPushButton("◀")
        self.button_step_back_1.setFixedSize(42, 34)
        self.button_step_back_1.setToolTip("Step Back 1 Frame")
        self.button_step_back_1.clicked.connect(lambda: self.step_backward_clicked.emit(1))
        controls_layout.addWidget(self.button_step_back_1)
        
        # Play/Pause
        self.button_play_pause = QPushButton("▶")
        self.button_play_pause.setFixedSize(54, 34)
        self.button_play_pause.setToolTip("Play/Pause")
        self.button_play_pause.clicked.connect(self._on_play_pause_clicked)
        controls_layout.addWidget(self.button_play_pause)
        
        # Step forward 1
        self.button_step_forward_1 = QPushButton("▶")
        self.button_step_forward_1.setFixedSize(42, 34)
        self.button_step_forward_1.setToolTip("Step Forward 1 Frame")
        self.button_step_forward_1.clicked.connect(lambda: self.step_forward_clicked.emit(1))
        controls_layout.addWidget(self.button_step_forward_1)
        
        # Step forward 10
        self.button_step_forward_10 = QPushButton("⏩")
        self.button_step_forward_10.setFixedSize(36, 32)
        self.button_step_forward_10.setToolTip("Step Forward 10 Frames")
        self.button_step_forward_10.clicked.connect(lambda: self.step_forward_clicked.emit(10))
        controls_layout.addWidget(self.button_step_forward_10)
        
        # Go to end
        self.button_go_to_end = QPushButton("⏭")
        self.button_go_to_end.setFixedSize(36, 32)
        self.button_go_to_end.setToolTip("Go to End")
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
        
        controls_layout.addStretch()
        
        main_layout.addLayout(controls_layout)
    
    def set_total_frames(self, total: int):
        """Set the total number of frames."""
        self.total_frames = max(1, total)
        self.slider_timeline.setRange(0, self.total_frames - 1)
        self.spinbox_current_frame.setRange(0, self.total_frames - 1)
        self.spinbox_in_point.setRange(0, self.total_frames - 1)
        self.spinbox_out_point.setRange(0, self.total_frames - 1)
        self.spinbox_out_point.setValue(self.total_frames - 1)
        self.out_point = self.total_frames - 1
        self.label_total_frames.setText(f"/ {self.total_frames - 1}")
    
    def set_current_frame(self, frame: int):
        """Set the current frame (from external source)."""
        self.current_frame = frame
        self.slider_timeline.blockSignals(True)
        self.slider_timeline.setValue(frame)
        self.slider_timeline.blockSignals(False)
        self.spinbox_current_frame.blockSignals(True)
        self.spinbox_current_frame.setValue(frame)
        self.spinbox_current_frame.blockSignals(False)
    
    def set_playing(self, is_playing: bool):
        """Update play/pause button state."""
        self.is_playing = is_playing
        self.button_play_pause.setText("⏸" if is_playing else "▶")
    
    def _on_play_pause_clicked(self):
        """Handle play/pause button click."""
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
        self.frame_changed.emit(value)
    
    def _on_in_point_changed(self, value: int):
        """Handle in point change."""
        self.in_point = value
        if self.in_point > self.out_point:
            self.spinbox_out_point.setValue(self.in_point)
        self.in_point_changed.emit(value)
    
    def _on_out_point_changed(self, value: int):
        """Handle out point change."""
        self.out_point = value
        if self.out_point < self.in_point:
            self.spinbox_in_point.setValue(self.out_point)
        self.out_point_changed.emit(value)


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
        self.setMinimumHeight(90)
        
        self.current_tool = AnnotationCanvas.MODE_NONE
        self.current_color = self.COLOR_PRESETS[0]
        self.current_thickness = 3
        self.current_scope = AnnotationCanvas.SCOPE_FRAME
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
        self.button_select = QPushButton("🖱")
        self.button_select.setFixedSize(36, 36)
        self.button_select.setCheckable(True)
        self.button_select.setChecked(True)
        self.button_select.setToolTip("Select (No Drawing)")
        self.button_group_tools.addButton(self.button_select)
        row_tools.addWidget(self.button_select)
        
        # Circle tool
        self.button_circle = QPushButton("○")
        self.button_circle.setFixedSize(36, 36)
        self.button_circle.setCheckable(True)
        self.button_circle.setToolTip("Draw Circle")
        self.button_group_tools.addButton(self.button_circle)
        row_tools.addWidget(self.button_circle)
        
        # Rectangle tool
        self.button_rectangle = QPushButton("□")
        self.button_rectangle.setFixedSize(36, 36)
        self.button_rectangle.setCheckable(True)
        self.button_rectangle.setToolTip("Draw Rectangle")
        self.button_group_tools.addButton(self.button_rectangle)
        row_tools.addWidget(self.button_rectangle)
        
        # Arrow tool
        self.button_arrow = QPushButton("→")
        self.button_arrow.setFixedSize(36, 36)
        self.button_arrow.setCheckable(True)
        self.button_arrow.setToolTip("Draw Arrow")
        self.button_group_tools.addButton(self.button_arrow)
        row_tools.addWidget(self.button_arrow)
        
        # Freehand tool
        self.button_freehand = QPushButton("✎")
        self.button_freehand.setFixedSize(36, 36)
        self.button_freehand.setCheckable(True)
        self.button_freehand.setToolTip("Freehand Draw")
        self.button_group_tools.addButton(self.button_freehand)
        row_tools.addWidget(self.button_freehand)
        
        # Text tool
        self.button_text = QPushButton("T")
        self.button_text.setFixedSize(36, 36)
        self.button_text.setCheckable(True)
        self.button_text.setToolTip("Add Text")
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
            button.setStyleSheet(f"background-color: {color.name()}; border: 2px solid #555;")
            button.setToolTip(f"Color: {color.name()}")
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
        
        self.combo_thickness = QComboBox()
        self.combo_thickness.setFixedWidth(60)
        for thickness in self.THICKNESS_PRESETS:
            self.combo_thickness.addItem(f"{thickness}px", thickness)
        self.combo_thickness.setCurrentIndex(1)  # Default to 3px
        self.combo_thickness.currentIndexChanged.connect(self._on_thickness_changed)
        row_options.addWidget(self.combo_thickness)
        
        row_options.addSpacing(20)

        # === Scope selection ===
        self.label_scope = QLabel("Scope:")
        row_options.addWidget(self.label_scope)

        self.combo_scope = QComboBox()
        self.combo_scope.setFixedWidth(90)
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
        self.button_save_thumbnail = QPushButton("📷 Save as Thumbnail")
        self.button_save_thumbnail.setFixedHeight(30)
        self.button_save_thumbnail.setToolTip("Save Current Frame with Annotations as Shot Thumbnail")
        self.button_save_thumbnail.clicked.connect(self.save_thumbnail_clicked.emit)
        row_options.addWidget(self.button_save_thumbnail)

        self.button_save_annotated_preview = QPushButton("🎞 Save Annotated Preview")
        self.button_save_annotated_preview.setFixedHeight(30)
        self.button_save_annotated_preview.setToolTip("Save Preview with Annotations")
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
        max_frame = max(0, total_frames - 1)
        self.spin_range_start.setRange(0, max_frame)
        self.spin_range_end.setRange(0, max_frame)
        if self.spin_range_end.value() > max_frame:
            self.spin_range_end.setValue(max_frame)
        if self.spin_range_start.value() > max_frame:
            self.spin_range_start.setValue(max_frame)
        if self.range_end < self.range_start:
            self.spin_range_end.setValue(self.range_start)
        self.range_selected.emit(self.range_start, self.range_end)


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
        self.label_header.setStyleSheet("font-weight: bold; font-size: 14px;")
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
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        
        self._setup_ui()
        self._update_mask()
    
    def _setup_ui(self):
        """Create the navigation overlay UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 10, 0)
        
        # Previous shot button
        self.button_previous_shot = QPushButton("←")
        self.button_previous_shot.setFixedSize(50, 50)
        self.button_previous_shot.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 150);
                color: white;
                border: none;
                border-radius: 25px;
                font-size: 24px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
            QPushButton:disabled {
                background-color: rgba(0, 0, 0, 50);
                color: #666;
            }
        """)
        self.button_previous_shot.setToolTip("Previous Shot")
        self.button_previous_shot.clicked.connect(self.previous_shot_clicked.emit)
        layout.addWidget(self.button_previous_shot, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        
        layout.addStretch()
        
        # Next shot button
        self.button_next_shot = QPushButton("→")
        self.button_next_shot.setFixedSize(50, 50)
        self.button_next_shot.setStyleSheet("""
            QPushButton {
                background-color: rgba(0, 0, 0, 150);
                color: white;
                border: none;
                border-radius: 25px;
                font-size: 24px;
            }
            QPushButton:hover {
                background-color: rgba(60, 60, 60, 200);
            }
            QPushButton:disabled {
                background-color: rgba(0, 0, 0, 50);
                color: #666;
            }
        """)
        self.button_next_shot.setToolTip("Next Shot")
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
        self.setFixedHeight(64)
        
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
        self.label_shot_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        top_row.addWidget(self.label_shot_title)
        
        top_row.addStretch()
        
        # Shot position (e.g., "2 of 5")
        self.label_shot_position = QLabel("")
        self.label_shot_position.setStyleSheet("color: #888;")
        top_row.addWidget(self.label_shot_position)

        layout.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        self.label_shot_meta = QLabel("")
        self.label_shot_meta.setStyleSheet("color: #aaa; font-size: 12px;")
        bottom_row.addWidget(self.label_shot_meta)

        bottom_row.addStretch()

        self.label_preview = QLabel("Preview:")
        bottom_row.addWidget(self.label_preview)

        self.combo_preview = QComboBox()
        self.combo_preview.setMinimumWidth(220)
        bottom_row.addWidget(self.combo_preview)

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
    ):
        """Populate the preview selector."""
        self.combo_preview.blockSignals(True)
        self.combo_preview.clear()

        if thumbnail_url:
            self.combo_preview.addItem(
                "Thumbnail",
                {"type": "thumbnail", "value": thumbnail_url},
            )

        if preview_paths:
            for preview_path in preview_paths:
                self.combo_preview.addItem(
                    preview_path.name,
                    {"type": "video", "value": str(preview_path)},
                )

        if self.combo_preview.count() == 0:
            self.combo_preview.addItem("No previews", None)
            self.combo_preview.setEnabled(False)
            self.combo_preview.blockSignals(False)
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
        self.setFixedHeight(44)
        self._setup_ui()

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(8)

        self.label_job = QLabel("Job:")
        layout.addWidget(self.label_job)

        self.combo_job = QComboBox()
        self.combo_job.setMinimumWidth(200)
        self.combo_job.currentIndexChanged.connect(self._on_job_changed)
        layout.addWidget(self.combo_job)

        layout.addSpacing(16)

        self.label_timeline = QLabel("Timeline:")
        layout.addWidget(self.label_timeline)

        self.combo_timeline = QComboBox()
        self.combo_timeline.setMinimumWidth(200)
        self.combo_timeline.currentIndexChanged.connect(self._on_timeline_changed)
        layout.addWidget(self.combo_timeline)

        layout.addStretch()

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
        # Main layout - horizontal split
        main_layout = QHBoxLayout(widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # === Left sidebar - Task list ===
        widget.task_list_sidebar = TaskListSidebar()
        main_layout.addWidget(widget.task_list_sidebar)
        
        # === Center area - Video viewer and controls ===
        center_widget = QWidget()
        center_layout = QVBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        
        # Shot info header (top)
        widget.review_selection_bar = ReviewSelectionBar()
        center_layout.addWidget(widget.review_selection_bar)

        # Shot info header (top)
        widget.shot_info_header = ShotInfoHeader()
        center_layout.addWidget(widget.shot_info_header)
        
        # Annotation toolbar
        widget.annotation_toolbar = AnnotationToolbar()
        center_layout.addWidget(widget.annotation_toolbar)
        
        # Video viewer with navigation overlay
        # Use a frame to contain video and overlay together
        widget.viewer_frame = QFrame()
        widget.viewer_frame.setStyleSheet("background-color: #0a0a0a;")
        viewer_frame_layout = QVBoxLayout(widget.viewer_frame)
        viewer_frame_layout.setContentsMargins(0, 0, 0, 0)
        viewer_frame_layout.setSpacing(0)
        
        widget.video_viewer = VideoViewerWidget()
        viewer_frame_layout.addWidget(widget.video_viewer, 1)
        
        center_layout.addWidget(widget.viewer_frame, 1)
        
        # Navigation overlay (parented to viewer_frame, positioned in logic)
        widget.shot_navigation = ShotNavigationOverlay(widget.viewer_frame)
        
        # Playback bar (bottom)
        widget.playback_bar = PlaybackBar()
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
        self._preview_version_re = re.compile(r"_v(\\d+)", re.IGNORECASE)
        self._was_playing_before_scrub = False
    
    def connect_signals(self):
        """Connect all signals between UI components."""
        widget = self.widget
        
        # Annotation toolbar -> Canvas
        widget.annotation_toolbar.tool_selected.connect(
            widget.video_viewer.annotation_canvas.set_mode
        )
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
            widget.video_viewer.annotation_canvas.set_scope_range
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
        widget.playback_bar.scrub_started.connect(self._on_scrub_started)
        widget.playback_bar.scrub_finished.connect(self._on_scrub_finished)
        
        # Video viewer -> Playback bar
        widget.video_viewer.frame_changed.connect(widget.playback_bar.set_current_frame)
        widget.video_viewer.duration_changed.connect(widget.playback_bar.set_total_frames)
        widget.video_viewer.duration_changed.connect(widget.annotation_toolbar.set_frame_range_limit)
        widget.video_viewer.playback_state_changed.connect(widget.playback_bar.set_playing)
        
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
            self.widget.shot_info_header.clear()
            self.widget.task_list_sidebar.set_tasks([])
        
        self._update_navigation_state()
    
    def _load_shot(self, index: int):
        """Load a shot by index."""
        if 0 <= index < len(self.shots):
            previous_shot_id = self.current_shot.get("id") if self.current_shot else None
            self.current_shot_index = index
            self.current_shot = self.shots[index]
            shot_changed = previous_shot_id != self.current_shot.get("id")
            
            # Update shot info header
            title = self.current_shot.get("title", f"Shot {index + 1}")
            self.widget.shot_info_header.set_shot_info(
                title, 
                index, 
                len(self.shots)
            )
            self.widget.shot_info_header.set_shot_metadata(self.current_shot)
            
            preview_paths, selected_path = self._get_preview_paths(self.current_shot)
            self.widget.shot_info_header.set_preview_options(
                preview_paths,
                selected_path,
                thumbnail_url=self.current_shot.get("thumbnail"),
                selected_preview=self.current_shot.get("preview_selection"),
            )
            selection = self.widget.shot_info_header.current_preview_selection()
            if selection:
                self.current_shot["preview_selection"] = selection

            if selection and selection.get("type") == "thumbnail":
                self.widget.video_viewer.pause()
                self._show_thumbnail(selection.get("value"))
            else:
                selected_path_str = selection.get("value") if selection else None
                current_video_path = self.widget.video_viewer.current_video_path
                needs_reload = (
                    shot_changed
                    or self.widget.video_viewer.is_showing_still()
                    or (selected_path_str and selected_path_str != current_video_path)
                )

                if needs_reload:
                    self.widget.video_viewer.stop()

                if selected_path_str and needs_reload:
                    print(f"[Review] Loading shot: {title}")
                    print(f"[Review] Video path: {selected_path_str}")
                    self.current_shot["video_path"] = selected_path_str
                    self.widget.video_viewer.load_video(selected_path_str)
                elif not selected_path_str and shot_changed:
                    print(f"[Review] Shot '{title}' has no preview video")
            
            # Load tasks
            tasks = self.current_shot.get("tasks", [])
            self.widget.task_list_sidebar.set_tasks(tasks)
            
            self._update_navigation_state()
    
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
        self.current_shot["preview_selection"] = selection
        if selection.get("type") == "thumbnail":
            self.widget.video_viewer.pause()
            self._show_thumbnail(selection.get("value"))
            return
        path = selection.get("value")
        if not path:
            return
        if path == self.widget.video_viewer.current_video_path and not self.widget.video_viewer.is_showing_still():
            return
        self.current_shot["video_path"] = path
        self.widget.video_viewer.load_video(path)

    def _show_thumbnail(self, thumbnail_url: str | None):
        """Load and display a thumbnail image."""
        if not thumbnail_url:
            return
        image = QImage()
        if thumbnail_url.startswith("http://") or thumbnail_url.startswith("https://"):
            try:
                response = self._api._request("GET", thumbnail_url)
                response.raise_for_status()
                if not image.loadFromData(response.content):
                    print("[Review] Failed to decode thumbnail image")
                    return
            except Exception as exc:
                print(f"[Review] Failed to load thumbnail: {exc}")
                return
        else:
            local_path = thumbnail_url
            if local_path.startswith("file://"):
                local_path = local_path[7:]
            if not image.load(local_path):
                print(f"[Review] Thumbnail file not found: {local_path}")
                return
        self.widget.video_viewer.show_still(image)
    
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

        if video_path:
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

        return sorted(unique, key=sort_key)
    
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
            )

    def _on_save_annotated_preview(self):
        """Handle save annotated preview button click."""
        print("[Review] Save annotated preview clicked")

    def _on_scrub_started(self):
        """Enter live scrubbing mode for the timeline."""
        self._was_playing_before_scrub = self.widget.video_viewer.is_playing()
        self.widget.video_viewer.begin_scrub()

    def _on_scrub_finished(self):
        """Exit scrubbing mode and restore playback state."""
        final_frame = self.widget.playback_bar.slider_timeline.value()
        self.widget.video_viewer.end_scrub(final_frame, self._was_playing_before_scrub)
        self._was_playing_before_scrub = False
    
    def _on_video_error(self, error_message: str):
        """Handle video playback errors."""
        print(f"[Review] Video error: {error_message}")
        # TODO: Could show error message in UI


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
        
        # Set up logic
        self.logic = ReviewPageLogic(self)
        self.logic.connect_signals()

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
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
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
                if self._shortcut_blocked():
                    return False
                if event.isAutoRepeat():
                    return True
                self._stop_nudge()
                return True
        return super().eventFilter(obj, event)

    def _setup_shortcuts(self):
        self._shortcut_play_pause = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._shortcut_play_pause.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_play_pause.activated.connect(self._on_shortcut_play_pause)

        self._shortcut_play_forward = QShortcut(QKeySequence(Qt.Key.Key_L), self)
        self._shortcut_play_forward.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_play_forward.activated.connect(self._on_shortcut_play_forward)

        self._shortcut_play_backward = QShortcut(QKeySequence(Qt.Key.Key_J), self)
        self._shortcut_play_backward.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_play_backward.activated.connect(self._on_shortcut_play_backward)

        self._shortcut_stop = QShortcut(QKeySequence(Qt.Key.Key_K), self)
        self._shortcut_stop.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_stop.activated.connect(self._on_shortcut_stop)

        self._shortcut_step_back = QShortcut(QKeySequence(Qt.Key.Key_Left), self)
        self._shortcut_step_back.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_step_back.setAutoRepeat(True)
        self._shortcut_step_back.activated.connect(self._on_shortcut_step_back)

        self._shortcut_step_forward = QShortcut(QKeySequence(Qt.Key.Key_Right), self)
        self._shortcut_step_forward.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_step_forward.setAutoRepeat(True)
        self._shortcut_step_forward.activated.connect(self._on_shortcut_step_forward)

        self._shortcut_prev_preview = QShortcut(QKeySequence(Qt.Key.Key_Up), self)
        self._shortcut_prev_preview.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_prev_preview.activated.connect(self._on_shortcut_prev_preview)

        self._shortcut_next_preview = QShortcut(QKeySequence(Qt.Key.Key_Down), self)
        self._shortcut_next_preview.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut_next_preview.activated.connect(self._on_shortcut_next_preview)

    def _shortcut_blocked(self) -> bool:
        focus_widget = self.focusWidget()
        if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            return True
        if isinstance(focus_widget, QComboBox) and focus_widget.view().isVisible():
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
    
    # Apply a basic dark theme for testing
    app.setStyleSheet("""
        QWidget {
            background-color: #1e1e1e;
            color: #e0e0e0;
        }
        QPushButton {
            background-color: #3a3a3a;
            border: 1px solid #555;
            padding: 4px 8px;
            border-radius: 3px;
        }
        QPushButton:hover {
            background-color: #4a4a4a;
        }
        QPushButton:pressed {
            background-color: #2a2a2a;
        }
        QPushButton:checked {
            background-color: #505050;
            border-color: #888;
        }
        QSlider::groove:horizontal {
            height: 6px;
            background: #3a3a3a;
            border-radius: 3px;
        }
        QSlider::handle:horizontal {
            background: #888;
            width: 14px;
            margin: -4px 0;
            border-radius: 7px;
        }
        QSlider::handle:horizontal:hover {
            background: #aaa;
        }
        QSpinBox, QComboBox {
            background-color: #2a2a2a;
            border: 1px solid #555;
            padding: 2px;
            border-radius: 3px;
        }
        QScrollArea {
            border: none;
        }
        QLabel {
            color: #e0e0e0;
        }
    """)
    
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
