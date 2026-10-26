"""Prepare small local EXR playback proxies without blocking menus."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
import os
from pathlib import Path
import tempfile
import threading

from PyQt6 import sip
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QImage
from app_cache import cache_directory


PREVIEW_FPS = 10
SOURCE_FPS = 24


def cached_proxy_frame(path, directory):
    from exr_thumbnails import read_thumbnail

    path = Path(path)
    stat = path.stat()
    signature = f"{path}\0{stat.st_mtime_ns}\0{stat.st_size}"
    target = directory / (hashlib.sha256(signature.encode()).hexdigest() + ".jpg")
    image = QImage(str(target))
    if not image.isNull():
        return image
    image = read_thumbnail(path)
    # A failed cache write should still allow an in-memory preview.
    temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        if image.save(str(temporary), "JPG", 50):
            os.replace(temporary, target)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return image


def prune_proxy_cache(directory, max_bytes=128 * 1024 * 1024):
    entries = []
    for path in directory.glob("*.jpg"):
        try:
            stat = path.stat()
            entries.append((stat.st_mtime_ns, stat.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= max_bytes:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            pass


class _ProxySignals(QObject):
    poster = pyqtSignal(object, object)
    finished = pyqtSignal(object, object)


class _ProxyTask(QRunnable):
    def __init__(self, frames, directory):
        super().__init__()
        self.frames = frames
        self.directory = directory
        self.cancelled = threading.Event()
        self.signals = _ProxySignals()

    def _read(self, path):
        if self.cancelled.is_set():
            return None
        try:
            return cached_proxy_frame(path, self.directory)
        except Exception:
            return None

    def run(self):
        # Sample ten images per second of source time, then bound long shots.
        sample_count = math.ceil(len(self.frames) * PREVIEW_FPS / SOURCE_FPS)
        stride = max(1, math.ceil(sample_count / 240))
        indices = tuple(index * SOURCE_FPS // PREVIEW_FPS
                        for index in range(0, sample_count, stride))
        selected = tuple(self.frames[index] for index in indices)
        images = []
        poster_sent = False
        with ThreadPoolExecutor(max_workers=2) as workers:
            for image in workers.map(self._read, selected):
                if self.cancelled.is_set():
                    break
                images.append(image)
                if image is not None and not poster_sent:
                    self.signals.poster.emit(self, image)
                    poster_sent = True
        result = None if self.cancelled.is_set() else {
            "images": images, "indices": indices, "source_count": len(self.frames),
        }
        self.signals.finished.emit(self, result)
        prune_proxy_cache(self.directory)


class EXRProxyLoader(QObject):
    poster = pyqtSignal(object, object)
    ready = pyqtSignal(object, object)

    def __init__(self, parent=None, directory=None):
        super().__init__(parent)
        self.directory = Path(directory) if directory is not None else cache_directory("exr-previews-v1")
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self._jobs = {}
        self.suspended = False

    def prepare(self, frames, label):
        if self.suspended:
            return
        self.cancel(label)
        task = _ProxyTask(tuple(frames), self.directory)
        self._jobs[task] = label
        task.signals.poster.connect(self._poster)
        task.signals.finished.connect(self._finished)
        self.pool.start(task)

    def cancel(self, label):
        for task, target in self._jobs.items():
            if target is label:
                task.cancelled.set()

    def cancel_all(self):
        self.suspended = True
        for task in self._jobs:
            task.cancelled.set()

    @pyqtSlot(object, object)
    def _poster(self, task, image):
        label = self._jobs.get(task)
        if label is not None and not sip.isdeleted(label) and not task.cancelled.is_set():
            self.poster.emit(label, image)

    @pyqtSlot(object, object)
    def _finished(self, task, result):
        label = self._jobs.pop(task, None)
        if label is not None and not sip.isdeleted(label) and not task.cancelled.is_set():
            self.ready.emit(label, result)
