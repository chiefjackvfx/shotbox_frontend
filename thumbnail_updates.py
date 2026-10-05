"""Capture and upload shot thumbnails without blocking the desktop UI."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from uuid import uuid4

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtGui import QImage


def upload_thumbnail_image(api, shot_id: int, image: QImage) -> dict:
    if image.isNull():
        raise ValueError("No frame available for the thumbnail.")
    with TemporaryDirectory(prefix="shotbox_thumbnail_") as directory:
        path = Path(directory) / f"shot_{shot_id}_{uuid4().hex}.png"
        if not image.save(str(path), "PNG"):
            raise RuntimeError("Could not save the captured frame.")
        updated = api.upload_shot_thumbnail(shot_id, str(path))
        if not updated or not updated.get("thumbnail"):
            raise RuntimeError("The server did not return an updated thumbnail.")
        return updated


class ThumbnailUpdateCancelled(Exception):
    pass


def extract_preview_thumbnail(video_path: str, *, check_cancelled=lambda: False) -> QImage:
    """Decode the middle frame of a preview video."""
    import av

    with av.open(str(video_path)) as container:
        stream = next(iter(container.streams.video), None)
        if stream is None:
            raise ValueError("No video stream found in the preview.")
        start = stream.start_time or 0
        duration = stream.duration
        if duration is None and container.duration is not None and stream.time_base:
            duration = int(container.duration / av.time_base / stream.time_base)
        target_index = None
        if duration is None:
            frame_count = stream.frames
            if not frame_count:
                frame_count = 0
                for _ in container.decode(stream):
                    if check_cancelled():
                        raise ThumbnailUpdateCancelled()
                    frame_count += 1
                container.seek(start, stream=stream, backward=True, any_frame=False)
            target_index = frame_count // 2
        target = start + max(0, int(duration or 0)) // 2
        if target > start:
            container.seek(target, stream=stream, backward=True, any_frame=False)
        selected = None
        for index, frame in enumerate(container.decode(stream)):
            if check_cancelled():
                raise ThumbnailUpdateCancelled()
            selected = frame
            if target_index is not None:
                if index >= target_index:
                    break
            elif frame.pts is None or frame.pts >= target:
                break
        if selected is None:
            raise ValueError("No frame could be decoded from the preview.")
        array = selected.to_ndarray(format="rgb24")
        height, width, _ = array.shape
        return QImage(
            array.data, width, height, array.strides[0], QImage.Format.Format_RGB888,
        ).copy()


def latest_thumbnail_preview(shot: dict, folders) -> str | None:
    shot_dir = shot.get("base_path")
    if shot_dir:
        latest, _ = folders.latest_preview(shot_dir)
        if latest and Path(latest).is_file():
            return str(latest)
    stored = str(shot.get("preview_video") or "").strip()
    if not stored:
        return None
    path = Path(folders.convert_path(stored))
    if not path.is_absolute():
        if not shot_dir:
            return None
        path = Path(folders.convert_path(shot_dir)) / path
    return str(path) if path.suffix.lower() == ".mp4" and path.is_file() else None


class _BatchThumbnailSignals(QObject):
    progress = pyqtSignal(int, str)
    updated = pyqtSignal(int, str, QImage)
    finished = pyqtSignal(object)


class BatchThumbnailWorker(QRunnable):
    def __init__(self, shots: list[dict], api, folders):
        super().__init__()
        self.shots = [dict(shot) for shot in shots]
        self.api = api
        self.folders = folders
        self.signals = _BatchThumbnailSignals()
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        summary = {"updated": 0, "failed": 0, "skipped": 0, "cancelled": False, "errors": []}
        for index, shot in enumerate(self.shots):
            if self._cancelled.is_set():
                break
            shot_id = shot.get("id")
            name = shot.get("title") or f"Shot {shot_id}"
            self.signals.progress.emit(index, f"Updating thumbnail {index + 1} of {len(self.shots)}: {name}")
            try:
                preview = latest_thumbnail_preview(shot, self.folders) if shot_id else None
                if preview is None:
                    summary["skipped"] += 1
                else:
                    image = extract_preview_thumbnail(preview, check_cancelled=self._cancelled.is_set)
                    if self._cancelled.is_set():
                        break
                    updated = upload_thumbnail_image(self.api, shot_id, image)
                    summary["updated"] += 1
                    self.signals.updated.emit(shot_id, updated["thumbnail"], image)
            except ThumbnailUpdateCancelled:
                break
            except Exception as exc:
                summary["failed"] += 1
                summary["errors"].append(f"{name}: {str(exc) or type(exc).__name__}")
            self.signals.progress.emit(
                index + 1,
                f"Updated: {summary['updated']} | Failed: {summary['failed']} | Skipped: {summary['skipped']}",
            )
        summary["cancelled"] = self._cancelled.is_set()
        summary["remaining"] = len(self.shots) - summary["updated"] - summary["failed"] - summary["skipped"]
        self.signals.finished.emit(summary)
