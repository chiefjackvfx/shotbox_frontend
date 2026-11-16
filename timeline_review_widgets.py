"""Painted timeline and normalized annotation surface for independent review."""
from copy import deepcopy
from collections import OrderedDict
import math

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QWidget, QToolTip

from timeline_review_model import timecode


class ReviewPicture(QWidget):
    clicked = pyqtSignal()
    drawing_started = pyqtSignal()
    drawing_changed = pyqtSignal()
    zoom_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.image = QImage()
        self.message = "Choose a timeline to begin."
        self.overlay_text = ""
        self.zoom = 1.0
        self.pan = QPointF()
        self.tool = None
        self.color = "#ff6b35"
        self.stroke_size = 3
        self.shapes = []
        self.redo_shapes = []
        self.saved_shapes = []
        self.aspect_ratio = 16 / 9
        self._stroke = None
        self._origin = None
        self._pan_origin = None
        self._moved = False

    def target(self):
        aspect = self.image.width() / self.image.height() if not self.image.isNull() else self.aspect_ratio
        width = min(self.width(), self.height() * aspect) * self.zoom
        height = width / aspect
        return QRectF((self.width() - width) / 2 + self.pan.x(),
                      (self.height() - height) / 2 + self.pan.y(), width, height)

    def fit(self):
        self.zoom, self.pan = 1.0, QPointF()
        self.zoom_changed.emit(100)
        self.update()

    def set_image(self, image):
        self.image = image
        if not image.isNull():
            self.aspect_ratio = image.width() / image.height()
        self.update()

    def clear_drawing(self):
        if self.shapes:
            self.redo_shapes.extend(reversed(self.shapes))
        self.shapes = []
        self._stroke = None
        self.tool = None
        self.drawing_changed.emit()
        self.update()

    def undo(self):
        if self.shapes:
            self.redo_shapes.append(self.shapes.pop())
            self.drawing_changed.emit()
            self.update()

    def redo(self):
        if self.redo_shapes:
            self.drawing_started.emit()
            self.shapes.append(self.redo_shapes.pop())
            self.drawing_changed.emit()
            self.update()

    def annotation(self):
        return ({"schema_version": 1, "coordinate_space": "normalized",
                 "aspect_ratio": self.aspect_ratio, "shapes": deepcopy(self.shapes)}
                if self.shapes else None)

    def point(self, pos):
        rect = self.target()
        return QPointF(max(0., min(1., (pos.x() - rect.x()) / max(1, rect.width()))),
                       max(0., min(1., (pos.y() - rect.y()) / max(1, rect.height()))))

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        if not delta:
            event.ignore()
            return
        old = self.target()
        anchor = self.point(event.position())
        self.zoom = max(.1, min(16., self.zoom * (1.15 if delta > 0 else 1 / 1.15)))
        new = self.target()
        self.pan += QPointF(old.x() + anchor.x() * old.width() - new.x() - anchor.x() * new.width(),
                            old.y() + anchor.y() * old.height() - new.y() - anchor.y() * new.height())
        self.zoom_changed.emit(round(self.zoom * 100))
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        self.setFocus()
        self._origin = event.position()
        self._moved = False
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_origin = QPointF(self.pan)
        elif event.button() == Qt.MouseButton.LeftButton and self.tool and not self.image.isNull():
            if not self.target().contains(event.position()):
                return
            self.drawing_started.emit()
            p = self.point(event.position())
            self._stroke = {"tool": self.tool, "color": self.color, "size": self.stroke_size}
            if self.tool == "pen":
                self._stroke.update(xs=[p.x()], ys=[p.y()])
            else:
                self._stroke.update(x=p.x(), y=p.y(), w=0., h=0.)
            self.shapes.append(self._stroke)
            self.redo_shapes.clear()
            self.drawing_changed.emit()
            self.update()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        if (event.position() - self._origin).manhattanLength() > 3:
            self._moved = True
        if self._pan_origin is not None:
            self.pan = self._pan_origin + event.position() - self._origin
        elif self._stroke:
            p = self.point(event.position())
            if self._stroke["tool"] == "pen":
                if len(self._stroke["xs"]) < 10000:
                    self._stroke["xs"].append(p.x())
                    self._stroke["ys"].append(p.y())
            else:
                self._stroke.update(w=p.x() - self._stroke["x"], h=p.y() - self._stroke["y"])
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            if not self._moved:
                self.fit()
            self._pan_origin = None
        elif event.button() == Qt.MouseButton.LeftButton and self._origin is not None:
            if not self.tool and not self._moved:
                self.clicked.emit()
            self._stroke = None
            self.drawing_changed.emit()
        self._origin = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0a0c10"))
        target = self.target()
        if not self.image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self.image)
        else:
            painter.setPen(QColor("#c7cbd4"))
            painter.drawText(self.rect().adjusted(20, 20, -20, -20),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.message)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(target)
        for shape in self.saved_shapes + self.shapes:
            self._paint_shape(painter, target, shape)
        if self.overlay_text:
            painter.setClipping(False)
            rect = QRectF(0, self.height() - 35, self.width(), 35)
            painter.fillRect(rect, QColor(0, 0, 0, 180))
            painter.setPen(QColor("white"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.overlay_text)

    @staticmethod
    def _paint_shape(painter, target, shape):
        try:
            size = max(.5, min(50., float(shape["size"])))
            pen = QPen(QColor(shape["color"]), size)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            def point(x, y):
                return QPointF(target.x() + float(x) * target.width(), target.y() + float(y) * target.height())
            tool = shape["tool"]
            if tool == "pen":
                points = [point(x, y) for x, y in zip(shape["xs"], shape["ys"])]
                if len(points) == 1:
                    painter.drawPoint(points[0])
                elif points:
                    painter.drawPolyline(QPolygonF(points))
            elif tool in ("line", "arrow", "rect"):
                a = point(shape["x"], shape["y"])
                b = point(shape["x"] + shape["w"], shape["y"] + shape["h"])
                if tool == "rect":
                    painter.drawRect(QRectF(a, b).normalized())
                else:
                    painter.drawLine(a, b)
                    if tool == "arrow":
                        angle = math.atan2(b.y() - a.y(), b.x() - a.x())
                        length = min(24, math.hypot(b.x() - a.x(), b.y() - a.y()) / 2)
                        for offset in (-.5, .5):
                            painter.drawLine(b, b - QPointF(length * math.cos(angle + offset), length * math.sin(angle + offset)))
        except (TypeError, KeyError, ValueError):
            return


class TimelineStrip(QWidget):
    seek = pyqtSignal(int)
    task_selected = pyqtSignal(object, object)
    add_task = pyqtSignal(object)
    thumbnail_needed = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit = None
        self.frame = 0
        self.show_tasks = True
        self.thumbnails = OrderedDict()
        self._task_regions = []
        self._dragging = False
        self.setFixedHeight(170)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Review timeline; click or drag to scrub")

    def frame_x(self, frame):
        return (frame - self.edit.start) / max(1, self.edit.end - self.edit.start) * self.width()

    def frame_at(self, x):
        return max(self.edit.start, min(self.edit.end - 1,
                   self.edit.start + int(x / max(1, self.width()) * (self.edit.end - self.edit.start))))

    def mousePressEvent(self, event):
        if not self.edit or not self.edit.cuts:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus()
            for rect, cut, task in self._task_regions:
                if rect.contains(event.position()):
                    self.task_selected.emit(cut, task)
                    return
            self._dragging = True
            self.seek.emit(self.frame_at(event.position().x()))

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.seek.emit(self.frame_at(event.position().x()))
        else:
            for rect, cut, task in self._task_regions:
                if rect.contains(event.position()):
                    QToolTip.showText(event.globalPosition().toPoint(), f"{cut.shot['title']} · {task['title']} · {task.get('frame_reference') or 'Whole shot'}", self)
                    return
            QToolTip.hideText()

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def mouseDoubleClickEvent(self, event):
        if self.edit:
            cut = self.edit.at(self.frame_at(event.position().x()))
            if cut:
                self.add_task.emit(cut)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#171b22"))
        self._task_regions = []
        if not self.edit or not self.edit.cuts:
            p.setPen(QColor("#bec4ce"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "No timeline shots")
            return
        visible = event.rect()
        spacing = max(1, round((self.edit.end - self.edit.start) * 110 / max(1, self.width())))
        start = self.edit.start + max(0, (self.frame_at(visible.left()) - self.edit.start) // spacing) * spacing
        for frame in range(start, min(self.edit.end, self.frame_at(visible.right()) + spacing), spacing):
            x = self.frame_x(frame)
            p.setPen(QColor("#747c8a"))
            p.drawLine(QPointF(x, 23), QPointF(x, 29))
            p.drawText(QRectF(x + 4, 0, 105, 23), timecode(frame - self.edit.start, self.edit.fps))
        for cut in self.edit.cuts:
            rect = QRectF(self.frame_x(cut.start), 30, max(1, self.frame_x(cut.end) - self.frame_x(cut.start) - 1), 67)
            if not rect.intersects(QRectF(visible)):
                continue
            active = cut.start <= self.frame < cut.end
            color = "#6c4b30" if active else "#303b4e"
            if cut.shot.get("hidden"):
                color = "#323238"
            p.fillRect(rect, QColor(color))
            thumb = self.thumbnails.get(cut.shot["id"])
            if thumb is not None:
                thumb_rect = QRectF(rect.left(), rect.top(), min(rect.width(), 86), rect.height())
                p.drawImage(thumb_rect, thumb)
                p.fillRect(rect, QColor(0, 0, 0, 100))
            elif cut.shot.get("thumbnail"):
                self.thumbnail_needed.emit(cut.shot)
            p.setPen(QColor("#ff9e51" if active else "#69778c"))
            p.drawRect(rect)
            p.save()
            p.setClipRect(rect.adjusted(3, 2, -3, -2))
            p.setPen(QColor("#f0f1f5"))
            label = cut.shot.get("title", "")
            if cut.shot.get("hidden"):
                label += " · Hidden"
            version = cut.media.get("version")
            detail = f"v{version:03}" if version is not None else "No preview"
            p.drawText(rect.adjusted(7, 5, -5, -5), Qt.AlignmentFlag.AlignTop, label + "\n" + detail)
            p.restore()
            if self.show_tasks:
                for i, task in enumerate(t for t in cut.shot.get("tasks", []) if not t.get("hidden")):
                    frame, _ = cut.task_frame(task)
                    ref = task.get("frame_reference")
                    end = (frame + ref[1] - ref[0] + 1 if isinstance(ref, list) else
                           cut.end if ref is None else frame + 1)
                    marker = QRectF(self.frame_x(frame), 103 + i % 3 * 18,
                                    max(9, self.frame_x(min(cut.end, end)) - self.frame_x(frame)), 15)
                    p.fillRect(marker, QColor("#3b8b80"))
                    p.save()
                    p.setClipRect(marker)
                    p.setPen(QColor("white"))
                    p.drawText(marker.adjusted(3, 0, -2, 0), task.get("title", ""))
                    p.restore()
                    self._task_regions.append((marker, cut, task))
        x = self.frame_x(self.frame)
        p.setPen(QPen(QColor("#ff9e51"), 2))
        p.drawLine(QPointF(x, 24), QPointF(x, self.height()))
