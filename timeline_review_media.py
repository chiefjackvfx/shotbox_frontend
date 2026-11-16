"""Bounded in-memory movie decoding for the desktop timeline.

All decoder access, including cleanup, runs on the page's serial media pool.
No disposable files or persistent caches are created.
"""
from collections import OrderedDict
import threading

import av
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtGui import QImage


class WorkSignals(QObject):
    finished = pyqtSignal(object, object, str)


class Work(QRunnable):
    def __init__(self, key, fn):
        super().__init__()
        self.key, self.fn = key, fn
        self.signals = WorkSignals()

    def run(self):
        try:
            result, error = self.fn(), ""
        except Exception as exc:
            result, error = None, str(exc)
        self.signals.finished.emit(self.key, result, error)


class MovieDecoder:
    def __init__(self):
        self.cache = OrderedDict()
        self.sources = OrderedDict()
        self.limit = 256 * 1024**2
        self.bytes = 0
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()

    def close(self):
        for container, *_ in self.sources.values():
            container.close()
        self.sources.clear()
        self.cache.clear()
        self.bytes = 0

    def read(self, media, index, scale=1):
        if self._cancelled.is_set():
            return None
        path = media["path"]
        key = (path, media.get("identity"), index, scale)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        source_key = (path, media.get("identity"))
        if source_key not in self.sources:
            container = av.open(path)
            try:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
            except Exception:
                container.close()
                raise
            self.sources[source_key] = [container, stream, None, -2]
            while len(self.sources) > 2:
                _, old = self.sources.popitem(last=False)
                old[0].close()
        self.sources.move_to_end(source_key)
        source = self.sources[source_key]
        container, stream, iterator, previous = source
        fps = media.get("fps", 25)
        origin = stream.start_time or 0
        if iterator is None or previous + 1 != index:
            container.seek(origin + int(index / fps / float(stream.time_base)),
                           stream=stream, backward=True)
            iterator = container.decode(video=0)
            source[2] = iterator
        result = None
        for frame in iterator:
            if self._cancelled.is_set():
                return None
            actual = (round(float((frame.pts - origin) * stream.time_base) * fps)
                      if frame.pts is not None else source[3] + 1)
            source[3] = actual
            if actual < index:
                continue
            # A VFR movie is sampled at the editorial position's nearest available frame.
            if scale > 1:
                frame = frame.reformat(width=max(1, frame.width // scale),
                                       height=max(1, frame.height // scale))
            pixels = frame.to_ndarray(format="rgb24")
            result = QImage(pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0],
                            QImage.Format.Format_RGB888).copy()
            break
        if result is None:
            raise ValueError(f"Frame {index} is unavailable in {path}")
        self.cache[key] = result
        self.bytes += result.sizeInBytes()
        while self.bytes > self.limit and len(self.cache) > 1:
            _, image = self.cache.popitem(last=False)
            self.bytes -= image.sizeInBytes()
        return result
