from __future__ import annotations

from collections import OrderedDict

from PyQt6.QtCore import QObject, QTimer, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class ImageLoader(QObject):
    """Async image loader with a small in-memory LRU cache."""

    _instance = None

    def __init__(self, parent: QObject | None = None, max_items: int = 200) -> None:
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._max_items = max(1, int(max_items))
        self._in_flight: dict[str, list] = {}

    @classmethod
    def instance(cls) -> "ImageLoader":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def load(self, url: str, callback) -> None:
        if not url:
            QTimer.singleShot(0, lambda: callback(None))
            return

        cached = self._cache_get(url)
        if cached is not None:
            QTimer.singleShot(0, lambda: callback(cached))
            return

        if url in self._in_flight:
            self._in_flight[url].append(callback)
            return

        self._in_flight[url] = [callback]
        request = QNetworkRequest(QUrl(url))
        reply = self._manager.get(request)
        reply.finished.connect(lambda: self._on_reply(url, reply))

    def _cache_get(self, url: str) -> QPixmap | None:
        if url not in self._cache:
            return None
        pixmap = self._cache[url]
        self._cache.move_to_end(url)
        return pixmap

    def _cache_set(self, url: str, pixmap: QPixmap) -> None:
        self._cache[url] = pixmap
        self._cache.move_to_end(url)
        while len(self._cache) > self._max_items:
            self._cache.popitem(last=False)

    def _on_reply(self, url: str, reply: QNetworkReply) -> None:
        pixmap = None
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError:
                data = bytes(reply.readAll())
                if data:
                    candidate = QPixmap()
                    if candidate.loadFromData(data):
                        pixmap = candidate
                        self._cache_set(url, pixmap)
        finally:
            callbacks = self._in_flight.pop(url, [])
            for cb in callbacks:
                cb(pixmap)
            reply.deleteLater()
