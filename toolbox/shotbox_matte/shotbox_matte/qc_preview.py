"""Asynchronous source/matte playback; importing this module keeps the CLI headless."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PreviewFrame:
    source_path: str | None
    source_index: int = 0
    source_number: int = 1
    matte_path: str | None = None


def source_frames(sequence: Any, selected_indices=None) -> list[PreviewFrame]:
    indices = range(sequence.count) if selected_indices is None else selected_indices
    frames = []
    for index in indices:
        if not isinstance(index, int) or not 0 <= index < sequence.count:
            raise ValueError("Preview frame index lies outside the source.")
        number = sequence.frame_numbers[index]
        video = sequence.media_type == "video"
        ordinal = sequence.frame_indices[index] if sequence.frame_indices else index
        frames.append(PreviewFrame(str(sequence.video_path or sequence.files[index]),
                                   ordinal if video else 0,
                                   number if number is not None else index + 1))
    return frames


def result_frames(result: dict) -> tuple[list[PreviewFrame], str | None, float | None]:
    """Use the finished job's manifest so queued clips retain their own mapping."""
    manifest = {}
    if result.get("manifest"):
        try:
            manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    if not isinstance(manifest, dict):
        manifest = {}
    sequence = manifest.get("sequence", {})
    sequence = sequence if isinstance(sequence, dict) else {}
    selected = manifest.get("selected_files", [])
    indices = manifest.get("selected_indices", [])
    numbers = manifest.get("source_numbers", [])
    video = sequence.get("media_type") == "video"
    video_indices = sequence.get("frame_indices", [])
    output_dir = Path(result.get("output_dir", "."))
    names = manifest.get("frames", [])
    if not names and result.get("first_output"):
        # Older worker protocols still provide a usable first-frame comparison.
        names = [str(Path(result["first_output"]).resolve())]
    frames = []
    for position, name in enumerate(names):
        source = selected[position] if position < len(selected) else result.get("first_source")
        index = indices[position] if position < len(indices) else position
        ordinal = video_indices[index] if video and index < len(video_indices) else index
        number = numbers[position] if position < len(numbers) else None
        matte = Path(name)
        if not matte.is_absolute():
            matte = output_dir / matte
        frames.append(PreviewFrame(source, ordinal if video else 0,
                                   number if number is not None else index + 1, str(matte)))
    fps = manifest.get("fps") or sequence.get("fps")
    return frames, manifest.get("source_color"), float(fps) if fps else None


def _display_image(path: str, index: int, color: str, *, matte: bool = False):
    import numpy as np
    from PIL import Image

    from .imaging import prepare_color
    from .media import default_source_color, read_frame

    pixels = read_frame(path, index)
    if not matte:
        pixels = prepare_color(pixels, default_source_color(path) if color == "auto" else color)
    pixels = np.rint(np.clip(pixels, 0.0, 1.0) * 255).astype(np.uint8)
    image = Image.fromarray(pixels)
    image.thumbnail((1024, 640), Image.Resampling.LANCZOS)
    return np.asarray(image).copy()


def load_frame_pair(frame: PreviewFrame, color: str) -> dict:
    """Read real source and output pixels in a worker, keeping errors actionable."""
    images = {"source": None, "matte": None, "errors": []}
    for kind, path, ordinal in (("source", frame.source_path, frame.source_index),
                                ("matte", frame.matte_path, 0)):
        if path:
            try:
                images[kind] = _display_image(path, ordinal, color, matte=kind == "matte")
            except Exception as exc:
                images["errors"].append(f"{kind.capitalize()}: {exc}")
    return images


def compose_preview(pair: dict, mode: str, opacity: float = 0.5):
    """Tint foreground according to raw matte coverage; never modify export data."""
    import numpy as np
    from PIL import Image

    if mode == "source":
        return pair.get("source")
    if mode == "matte":
        return pair.get("matte")
    if mode != "overlay":
        raise ValueError("Unknown QC preview mode.")
    source, matte = pair.get("source"), pair.get("matte")
    if source is None or matte is None:
        return None
    if matte.shape[:2] != source.shape[:2]:
        matte = np.asarray(Image.fromarray(matte).resize((source.shape[1], source.shape[0]), Image.Resampling.BILINEAR))
    alpha = (matte[..., :1].astype(np.float32) / 255) * min(1.0, max(0.0, opacity))
    tint = np.array([245, 96, 47], np.float32)
    return np.rint(source.astype(np.float32) * (1 - alpha) + tint * alpha).astype(np.uint8)


_WIDGET_TYPE: Any = None


def create_qc_preview(parent=None):
    """Construct the optional Qt QC controls without loading a model or torch."""
    global _WIDGET_TYPE
    if _WIDGET_TYPE is not None:
        return _WIDGET_TYPE(parent)
    import threading

    threading.current_thread()
    from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
    from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
    from PyQt6.QtWidgets import (
        QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
        QSlider, QVBoxLayout, QWidget,
    )

    class Signals(QObject):
        done = pyqtSignal(object)

    class LoadTask(QRunnable):
        def __init__(self, key, frame, color):
            super().__init__()
            self.key, self.frame, self.color = key, frame, color
            self.signals = Signals()

        def run(self):
            try:
                value = load_frame_pair(self.frame, self.color)
            except Exception as exc:
                value = {"source": None, "matte": None, "errors": [str(exc)]}
            self.signals.done.emit((self.key, value))

    class Canvas(QWidget):
        def __init__(self):
            super().__init__()
            self.pixmap = None
            self.setMinimumSize(260, 180)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        def set_pixels(self, pixels):
            self.pixmap = None
            if pixels is not None:
                height, width = pixels.shape[:2]
                image = QImage(pixels.tobytes(), width, height, width * 3, QImage.Format.Format_RGB888).copy()
                self.pixmap = QPixmap.fromImage(image)
            self.update()

        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor("#101215"))
            if self.pixmap is None:
                painter.setPen(QColor("#9299a6"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Load a source or completed result")
                return
            bounds = self.rect().adjusted(8, 8, -8, -8)
            size = self.pixmap.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(bounds.x() + (bounds.width() - size.width()) // 2,
                               bounds.y() + (bounds.height() - size.height()) // 2,
                               size.width(), size.height(), self.pixmap)

    class QCPreview(QWidget):
        def __init__(self, parent=None):
            super().__init__(parent)
            self._frames: list[PreviewFrame] = []
            self._cache: OrderedDict[int, dict] = OrderedDict()
            self._source_signature = None
            self._color = "auto"
            self._generation = 0
            self._active = None
            self._wanted = None
            self._pair = None
            self._closed = False
            self._has_result = False
            self._fps = 24.0
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            toolbar = QHBoxLayout()
            self.previous_button = QPushButton("◀")
            self.previous_button.setAccessibleName("Previous QC frame")
            self.play_button = QPushButton("Play")
            self.next_button = QPushButton("▶")
            self.next_button.setAccessibleName("Next QC frame")
            self.loop = QCheckBox("Loop")
            self.loop.setChecked(True)
            self.mode = QComboBox()
            for label, key in (("Source", "source"), ("Matte", "matte"), ("Overlay", "overlay")):
                self.mode.addItem(label, key)
            for widget in (self.previous_button, self.play_button, self.next_button, self.loop, self.mode):
                toolbar.addWidget(widget)
            self.opacity = QSlider(Qt.Orientation.Horizontal)
            self.opacity.setRange(0, 100)
            self.opacity.setValue(50)
            self.opacity.setToolTip("Foreground overlay opacity")
            toolbar.addWidget(self.opacity, 1)
            layout.addLayout(toolbar)
            self.canvas = Canvas()
            layout.addWidget(self.canvas, 1)
            self.slider = QSlider(Qt.Orientation.Horizontal)
            layout.addWidget(self.slider)
            self.frame_label = QLabel("No frames")
            self.status = QLabel("")
            self.status.setWordWrap(True)
            self.status.setObjectName("muted")
            layout.addWidget(self.frame_label)
            layout.addWidget(self.status)
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._tick)
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.setInterval(35)
            self._debounce.timeout.connect(self._request)
            self.slider.valueChanged.connect(self._position_changed)
            self.previous_button.clicked.connect(lambda: self.slider.setValue(max(0, self.slider.value() - 1)))
            self.next_button.clicked.connect(lambda: self.slider.setValue(min(self.slider.maximum(), self.slider.value() + 1)))
            self.play_button.clicked.connect(self._toggle_play)
            self.mode.currentIndexChanged.connect(self._render)
            self.opacity.valueChanged.connect(self._render)
            self.clear()

        def set_fps(self, fps):
            import math
            if isinstance(fps, (int, float)) and math.isfinite(fps) and fps > 0:
                self._fps = float(fps)
                self._timer.setInterval(max(8, round(1000 / self._fps)))

        def set_source(self, sequence, source_color="auto", selected_indices=None):
            frames = source_frames(sequence, selected_indices)
            signature = (tuple(frames), source_color)
            if signature == self._source_signature and not self._has_result:
                return
            self._source_signature = signature
            self._set_frames(frames, source_color, False)
            self.set_fps(sequence.fps or self._fps)

        def set_result(self, result, source_color="auto"):
            frames, recorded_color, fps = result_frames(result)
            self._source_signature = None
            self._set_frames(frames, recorded_color or source_color, True)
            if fps:
                self.set_fps(fps)
            if frames:
                self.mode.setCurrentIndex(1)

        def clear(self):
            self._source_signature = None
            self._set_frames([], "auto", False)

        def stop(self):
            self._timer.stop()
            self.play_button.setText("Play")

        def dispose(self):
            self.stop()
            self._debounce.stop()
            self._generation += 1
            self._wanted = None
            self._closed = True
            self._cache.clear()

        def _set_frames(self, frames, color, has_result):
            self.stop()
            self._debounce.stop()
            self._generation += 1
            self._wanted = None
            self._frames, self._color, self._has_result = frames, color, has_result
            self._cache.clear()
            self._pair = None
            self.canvas.set_pixels(None)
            self.slider.blockSignals(True)
            self.slider.setRange(0, max(0, len(frames) - 1))
            self.slider.setValue(0)
            self.slider.blockSignals(False)
            for widget in (self.slider, self.play_button, self.previous_button, self.next_button):
                widget.setEnabled(bool(frames))
            self.mode.setEnabled(bool(frames) and has_result)
            self.mode.setCurrentIndex(0)
            self.status.setText("")
            self._position_changed()

        def _position_changed(self, _value=0):
            if not self._frames:
                self.frame_label.setText("No frames")
                return
            index = self.slider.value()
            self.frame_label.setText(f"Source frame {self._frames[index].source_number} · {index + 1} / {len(self._frames)}")
            self._wanted = (self._generation, index)
            self._debounce.start()

        def _request(self):
            if self._closed or self._wanted is None:
                return
            generation, index = self._wanted
            if generation != self._generation:
                return
            if index in self._cache:
                self._pair = self._cache[index]
                self._cache.move_to_end(index)
                self._render()
                return
            if self._active is not None:
                return  # Keep only the most recent request while an image loads.
            self.status.setText("Loading frame…")
            task = LoadTask((generation, index), self._frames[index], self._color)
            self._active = task
            task.signals.done.connect(self._loaded)
            QThreadPool.globalInstance().start(task)

        def _loaded(self, response):
            key, pair = response
            self._active = None
            if self._closed:
                return
            generation, index = key
            if generation == self._generation:
                self._cache[index] = pair
                while len(self._cache) > 8:
                    self._cache.popitem(last=False)
                if key == self._wanted:
                    self._pair = pair
                    self._render()
            if self._wanted != key:
                self._request()

        def _render(self, _value=0):
            self.opacity.setEnabled(self._has_result and self.mode.currentData() == "overlay")
            if self._pair is None:
                return
            pixels = compose_preview(self._pair, self.mode.currentData(), self.opacity.value() / 100)
            self.canvas.set_pixels(pixels)
            errors = self._pair.get("errors", [])
            self.status.setText("; ".join(errors) if errors else "Display preview only; exported matte values stay raw.")
            if errors and pixels is None:
                self.stop()

        def _toggle_play(self):
            if self._timer.isActive():
                self.stop()
            elif self._frames:
                self.set_fps(self._fps)
                self._timer.start()
                self.play_button.setText("Pause")

        def _tick(self):
            if self._active is not None or self._debounce.isActive():
                return
            position = self.slider.value() + 1
            if position >= len(self._frames):
                if not self.loop.isChecked():
                    self.stop()
                    return
                position = 0
            self.slider.setValue(position)

        def closeEvent(self, event):
            self.dispose()
            event.accept()

    _WIDGET_TYPE = QCPreview
    return QCPreview(parent)
