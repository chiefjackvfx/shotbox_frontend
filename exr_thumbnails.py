"""Cached EXR menu previews decoded outside the UI thread."""

from collections import OrderedDict
from bisect import bisect_right
import math
from pathlib import Path
import re

from PyQt6 import sip
from PyQt6.QtCore import QElapsedTimer, QObject, QPoint, QRectF, QRunnable, QThreadPool, QTimer, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QMenu
from exr_preview_proxy import PREVIEW_FPS, SOURCE_FPS


def find_exr_preview_frame(folder):
    frames = find_exr_preview_frames(folder)
    return frames[0] if frames else None


def find_exr_preview_frames(folder):
    """Choose one sequence without requiring a single, complete sequence."""
    folder = Path(folder)
    groups = {}
    for path in sorted(folder.iterdir()):
        if path.name.startswith(".") or path.suffix.lower() != ".exr" or not path.is_file():
            continue
        match = re.match(r"^(.*?)(\d+)([^0-9]*\.exr)$", path.name, re.IGNORECASE)
        if match is None:
            continue
        prefix, frame, suffix = match.groups()
        groups.setdefault((prefix, suffix.casefold(), len(frame)), []).append((int(frame), path))
    if not groups:
        return ()

    def preference(item):
        (prefix, suffix, padding), frames = item
        matches_folder = prefix.rstrip("._-").casefold() == folder.name.casefold()
        return (not matches_folder, -len(frames), prefix.casefold(), suffix.casefold(), padding)

    _, frames = min(groups.items(), key=preference)
    return tuple(str(path) for _, path in sorted(frames, key=lambda item: (item[0], item[1].name)))


class EXRMenuPreview(QLabel):
    """Loop EXR frames beside an open menu without taking its input focus."""

    frame_changed = pyqtSignal(int)

    def __init__(self, menu, frames):
        super().__init__(
            "Loading preview…", menu,
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput,
        )
        self._menu = menu
        parent = menu.parentWidget()
        self._parent_menu = parent if isinstance(parent, QMenu) else None
        self._frames = (str(frames),) if isinstance(frames, (str, Path)) else tuple(frames)
        self._path = self._frames[0]
        self._frame_index = 0
        self._loader = None
        self._proxy_pixmaps = ()
        self._proxy_indices = ()
        self._loading = False
        self._spinner_angle = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(40)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self._playback_elapsed = QElapsedTimer()
        self._playback_timer = QTimer(self)
        self._playback_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._playback_timer.setInterval(round(1000 / PREVIEW_FPS))
        self._playback_timer.timeout.connect(self._next_frame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(336, 196)
        self.setMargin(8)
        self.setStyleSheet("background-color: #2F3542; color: #D4D4D4; border: 1px solid #404754;")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        menu.aboutToShow.connect(self._schedule_show)
        menu.aboutToHide.connect(self.hide)
        if self._parent_menu is not None:
            self._parent_menu.hovered.connect(self._on_folder_hovered)
            self._parent_menu.aboutToHide.connect(self.hide)

    def _on_folder_hovered(self, action):
        if action is self._menu.menuAction():
            self._schedule_show()
        elif action in self._parent_menu.actions():
            # QMenu also forwards signals from actions inside its submenus.
            self.hide()

    def _preview_anchor(self):
        if self._parent_menu is not None and self._parent_menu.isVisible():
            if self._parent_menu.activeAction() is not self._menu.menuAction():
                return None
            if not self._menu.isVisible():
                return self._parent_menu
        return self._menu if self._menu.isVisible() else None

    def _schedule_show(self):
        # Qt places and sizes the submenu after emitting aboutToShow.
        QTimer.singleShot(0, self._show_beside_menu)

    def _show_beside_menu(self):
        anchor = self._preview_anchor()
        if anchor is None:
            return
        if self._loader is None:
            self._loader = EXRThumbnailLoader.instance()
            self._loader.ready.connect(self._on_still_ready)
            self._loader.proxy_poster.connect(self._on_proxy_poster)
            self._loader.proxy_ready.connect(self._on_proxy_ready)
            self._loader.cache_cleared.connect(self.hide)
            self._loader.cache_resumed.connect(self._resume_after_cache_clear)
        if self._loader.suspended:
            self.hide()
            return
        origin = anchor.mapToGlobal(QPoint(0, 0))
        available = anchor.screen().availableGeometry()
        x = origin.x() + anchor.width() + 4
        if x + self.width() > available.right() + 1:
            x = origin.x() - self.width() - 4
        x = max(available.left(), min(x, available.right() - self.width() + 1))
        y = max(available.top(), min(origin.y(), available.bottom() - self.height() + 1))
        self.move(x, y)
        if self.isVisible():
            return
        self._set_loading(True)
        self.show()
        self._frame_index = 0
        self._proxy_pixmaps = ()
        self._playback_timer.stop()
        # The still has its own worker pool, so another sequence's proxy
        # preparation cannot leave this menu showing only a spinner.
        self._loader.load(self._path, self)
        self._loader.prepare(self._frames, self)

    def _resume_after_cache_clear(self):
        if self._preview_anchor() is not None:
            self._schedule_show()

    def _on_still_ready(self, label, success):
        if label is self and not success and self._loading and self.pixmap().isNull():
            # Other frames may still provide a valid proxy poster.
            self.clear()

    def _next_frame(self):
        if not self.isVisible() or self._preview_anchor() is None or not self._proxy_pixmaps:
            return
        source_index = (self._playback_elapsed.elapsed() * SOURCE_FPS // 1000) % len(self._frames)
        proxy_index = max(0, bisect_right(self._proxy_indices, source_index) - 1)
        index = self._proxy_indices[proxy_index]
        if index == self._frame_index:
            return
        pixmap = self._proxy_pixmaps[proxy_index]
        if pixmap is not None:
            self._frame_index = index
            self._display_pixmap(pixmap)
            self.frame_changed.emit(index)

    def _on_proxy_poster(self, label, image):
        if label is not self or not self.isVisible() or self._preview_anchor() is None:
            return
        self._display_pixmap(QPixmap.fromImage(image))

    def _on_proxy_ready(self, label, result):
        if label is not self or not self.isVisible() or self._preview_anchor() is None:
            return
        self._set_loading(False)
        if result is None:
            return
        self._proxy_pixmaps = tuple(QPixmap.fromImage(image) if image is not None else None
                                    for image in result["images"])
        self._proxy_indices = result["indices"]
        first_valid = next((index for index, pixmap in enumerate(self._proxy_pixmaps) if pixmap is not None), None)
        if first_valid is None:
            if self.pixmap().isNull():
                self.setText("Preview unavailable")
            return
        # Cancel the still only when there is a valid replacement. A failed
        # proxy must not discard a thumbnail that is still being decoded.
        self._exr_preview_key = None
        self._display_pixmap(self._proxy_pixmaps[first_valid])
        self._frame_index = self._proxy_indices[first_valid]
        self._playback_elapsed.start()
        self.frame_changed.emit(self._frame_index)
        if sum(pixmap is not None for pixmap in self._proxy_pixmaps) > 1:
            self._playback_timer.start()

    def _display_pixmap(self, pixmap):
        ratio = self.devicePixelRatioF()
        preview = pixmap.scaled(round(320 * ratio), round(180 * ratio),
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.FastTransformation)
        preview.setDevicePixelRatio(ratio)
        self.setPixmap(preview)

    def _set_loading(self, loading):
        self._loading = bool(loading)
        if self._loading:
            if self.pixmap().isNull():
                self.clear()
            self._spinner_timer.start()
        else:
            self._spinner_timer.stop()
        self.update()

    def _advance_spinner(self):
        self._spinner_angle = (self._spinner_angle + 12) % 360
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._loading:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.pixmap().isNull():
            circle = QRectF((self.width() - 18) / 2, (self.height() - 18) / 2, 18, 18)
        else:
            circle = QRectF(self.width() - 32, self.height() - 32, 18, 18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 18, 24, 210))
        painter.drawEllipse(circle.adjusted(-4, -4, 4, 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor("#FF6B35"), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(circle, -self._spinner_angle * 16, 250 * 16)
        painter.end()

    def hideEvent(self, event):
        self._set_loading(False)
        self._playback_timer.stop()
        self._proxy_pixmaps = ()
        if self._loader is not None:
            self._loader.cancel(self)
        super().hideEvent(event)


def read_thumbnail(path):
    import numpy as np
    from review_media import display_image

    try:
        import av
        with av.open(str(path)) as source:
            frame = next(source.decode(video=0))
            scale = min(256 / frame.width, 144 / frame.height)
            frame = frame.reformat(width=max(1, round(frame.width * scale)),
                                   height=max(1, round(frame.height * scale)), format="gbrpf32le")
            rgb = np.array(frame.to_ndarray(), dtype=np.float32, order="C", copy=True)
    except Exception:
        # Keep layered or unsupported EXR formats working with the native reader.
        from review_media import EXRSequenceReader
        pixels = EXRSequenceReader.read_pixels(path)
        height, width = pixels.shape[:2]
        stride = max(1, math.ceil(max(width / 256, height / 144)))
        rgb = np.array(pixels[::stride, ::stride], dtype=np.float32, order="C", copy=True)
    np.nan_to_num(rgb, copy=False, nan=0.0, posinf=1.0, neginf=0.0)
    np.clip(rgb, 0.0, 1.0, out=rgb)
    rgb = np.where(rgb <= 0.0031308, 12.92 * rgb,
                   1.055 * np.power(rgb, 1.0 / 2.4) - 0.055)
    return display_image(rgb, owned=True).scaled(
        256, 144, Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )


class _ThumbnailSignals(QObject):
    finished = pyqtSignal(object, object)


class _ThumbnailTask(QRunnable):
    def __init__(self, key):
        super().__init__()
        # Keep task identities alive until their queued completion is handled.
        self.setAutoDelete(False)
        self.key = key
        self.signals = _ThumbnailSignals()

    def run(self):
        try:
            image = read_thumbnail(self.key[0])
        except Exception:
            # A missing decoder or unreadable frame must leave menus usable.
            image = None
        self.signals.finished.emit(self, image)


class EXRThumbnailLoader(QObject):
    ready = pyqtSignal(object, bool)
    proxy_poster = pyqtSignal(object, object)
    proxy_ready = pyqtSignal(object, object)
    cache_cleared = pyqtSignal()
    cache_resumed = pyqtSignal()
    _instance = None

    def __init__(self, parent=None, max_items=128, proxy_cache_dir=None):
        super().__init__(parent)
        self._cache = OrderedDict()
        self._max_items = max(1, int(max_items))
        self._pending = {}
        self._tasks = {}
        self._discarded_tasks = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        from exr_preview_proxy import EXRProxyLoader
        self._proxies = EXRProxyLoader(self, directory=proxy_cache_dir)
        self._proxies.poster.connect(self.proxy_poster.emit)
        self._proxies.ready.connect(self.proxy_ready.emit)

    def prepare(self, frames, label):
        self._proxies.prepare(frames, label)

    @property
    def suspended(self):
        return self._proxies.suspended

    def begin_cache_clear(self):
        self._proxies.cancel_all()
        self.cache_cleared.emit()
        self._cache.clear()
        self._discarded_tasks.update(self._tasks.values())
        for labels in self._pending.values():
            for label in labels:
                if not sip.isdeleted(label):
                    label._exr_preview_key = None
        self._pending.clear()
        for key, task in list(self._tasks.items()):
            if self._pool.tryTake(task):
                self._tasks.pop(key)
                self._discarded_tasks.discard(task)

    def finish_cache_clear(self):
        self._cache.clear()
        self._proxies.suspended = False
        self.cache_resumed.emit()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load(self, path, label):
        if self._proxies.suspended:
            return
        try:
            path = Path(path)
            stat = path.stat()
        except OSError:
            label._exr_preview_key = None
            self._deliver(label, None, None)
            return
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        label._exr_preview_key = key
        if key in self._cache:
            self._cache.move_to_end(key)
            self._deliver(label, key, self._cache[key])
            return
        if key in self._pending:
            if label not in self._pending[key]:
                self._pending[key].append(label)
            return
        if label.pixmap().isNull():
            label.setText("Loading preview…")
        self._pending[key] = [label]
        task = _ThumbnailTask(key)
        task.signals.finished.connect(self._finished)
        self._tasks[key] = task
        self._pool.start(task)

    def cancel(self, label):
        self._proxies.cancel(label)
        label._exr_preview_key = None
        for key, labels in list(self._pending.items()):
            remaining = [pending for pending in labels if pending is not label and not sip.isdeleted(pending)]
            self._pending[key] = remaining
            task = self._tasks.get(key)
            if not remaining and task is not None and self._pool.tryTake(task):
                self._tasks.pop(key)
                self._pending.pop(key)

    def _deliver(self, label, key, pixmap):
        if sip.isdeleted(label) or label._exr_preview_key != key:
            return
        if pixmap is None or pixmap.isNull():
            if label.pixmap().isNull():
                label.setText("Preview unavailable")
            self.ready.emit(label, False)
            return
        ratio = label.devicePixelRatioF()
        preview = pixmap.scaled(
            round(320 * ratio), round(180 * ratio),
            Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation,
        )
        preview.setDevicePixelRatio(ratio)
        label.setPixmap(preview)
        self.ready.emit(label, True)

    @pyqtSlot(object, object)
    def _finished(self, task, image):
        key = task.key
        if task in self._discarded_tasks:
            self._discarded_tasks.discard(task)
            if self._tasks.get(key) is task:
                self._tasks.pop(key)
            return
        if self._tasks.get(key) is not task:
            return
        pixmap = QPixmap.fromImage(image) if image is not None else None
        # A transient read failure must be retried on the next hover.
        if pixmap is not None and not pixmap.isNull():
            self._cache[key] = pixmap
            self._cache.move_to_end(key)
        while len(self._cache) > self._max_items:
            self._cache.popitem(last=False)
        self._tasks.pop(key, None)
        for label in self._pending.pop(key, []):
            self._deliver(label, key, pixmap)
