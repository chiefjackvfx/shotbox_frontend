"""Capture and upload shot thumbnails without blocking the desktop UI."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from uuid import uuid4

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal
from PyQt6.QtGui import QImage
from app_cache import cache_directory, temporary_cache_task
from asset_paths import asset_path


@temporary_cache_task
def upload_thumbnail_image(api, shot_id: int, image: QImage) -> dict:
    if image.isNull():
        raise ValueError("No frame available for the thumbnail.")
    with TemporaryDirectory(prefix="shotbox_thumbnail_", dir=cache_directory("temp")) as directory:
        path = Path(directory) / f"shot_{shot_id}_{uuid4().hex}.png"
        if not image.save(str(path), "PNG"):
            raise RuntimeError("Could not save the captured frame.")
        updated = api.upload_shot_thumbnail(shot_id, str(path))
        if not updated or not updated.get("thumbnail"):
            raise RuntimeError("The server did not return an updated thumbnail.")
        return updated


class ThumbnailUpdateCancelled(Exception):
    pass


def extract_preview_thumbnail(video_path: str, *, check_cancelled=lambda: False,
                              frame_index: int | None = None,
                              input_colourspace: str | None = None) -> QImage:
    """Decode a preview's middle frame, or an exact zero-based source frame."""
    import av

    if check_cancelled():
        raise ThumbnailUpdateCancelled()
    with av.open(str(video_path)) as container:
        stream = next(iter(container.streams.video), None)
        if stream is None:
            raise ValueError("No video stream found in the preview.")
        start = stream.start_time or 0
        duration = stream.duration
        if duration is None and container.duration is not None and stream.time_base:
            duration = int(container.duration / av.time_base / stream.time_base)
        target_index = max(0, int(frame_index)) if frame_index is not None else None
        if frame_index is None and duration is None:
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
        if target_index is None and target > start:
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
        # Some camera MXFs declare a YUV ProRes frame as RGB with reserved
        # primaries/transfer values. swscale can reject this, while OpenCV can
        # return an all-black frame as a successful read. Normalize only these
        # invalid decode tags on the in-memory frame; never alter source media.
        if selected.format.name.startswith('yuv'):
            if selected.colorspace == 0:
                selected.colorspace = 1  # BT.709 matrix for invalid RGB-on-YUV tags.
            for attribute in ('color_primaries', 'color_trc'):
                if getattr(selected, attribute) in (0, 3):
                    setattr(selected, attribute, 1)
        if check_cancelled():
            raise ThumbnailUpdateCancelled()
        from review_media import movie_display_image
        from thumbnail_colour import thumbnail_display_processor

        processor = thumbnail_display_processor(input_colourspace) if input_colourspace else None
        # Review's shared converter retains 16-bit RGB until OCIO has run,
        # then clips and quantizes for the 8-bit display image.
        image = movie_display_image(selected, processor)
        if check_cancelled():
            raise ThumbnailUpdateCancelled()
        return image


THUMBNAIL_VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mxf', '.avi', '.mkv', '.r3d', '.braw', '.arx'}


def _shot_media_path(value, shot_dir, folders):
    if not isinstance(value, str) or value.strip().lower() in ('', 'none', 'offline'):
        return None
    path = Path(folders.convert_path(value))
    if not path.is_absolute():
        if not shot_dir:
            return None
        path = Path(folders.convert_path(shot_dir)) / path
    return path if path.suffix.lower() in THUMBNAIL_VIDEO_EXTENSIONS and path.is_file() else None


def _timeline_thumbnail_metadata(shot, folders):
    if not shot.get('base_path'):
        return {}
    try:
        path = asset_path(folders.convert_path(shot['base_path']), 'shot_assets', 'timeline_import.json')
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def latest_thumbnail_preview(shot: dict, folders) -> str | None:
    """Prefer rendered previews; otherwise use the explicitly identified primary plate."""
    shot_dir = shot.get("base_path")
    if shot_dir:
        latest, _ = folders.latest_preview(shot_dir)
        if latest and Path(latest).is_file():
            return str(latest)
    stored = _shot_media_path(shot.get('preview_video'), shot_dir, folders)
    if stored:
        return str(stored)
    metadata = _timeline_thumbnail_metadata(shot, folders)
    plates = metadata.get('plates', [])
    primary = next((p for p in plates if isinstance(p, dict) and p.get('track') == 1), {}) if isinstance(plates, list) else {}
    original_clips = shot.get('original_clips')
    first_original = original_clips[0] if isinstance(original_clips, list) and original_clips else None
    references = [primary.get('copied_path'), shot.get('original_clip'), first_original]
    for reference in references:
        if shot_dir and isinstance(reference, str) and reference.strip():
            # Shot folders may have moved since the metadata was written.
            local = Path(folders.convert_path(shot_dir), 'plates', Path(reference.replace('\\', '/')).name)
            if local.suffix.lower() in THUMBNAIL_VIDEO_EXTENSIONS and local.is_file():
                return str(local)
        path = _shot_media_path(reference, shot_dir, folders)
        if path:
            return str(path)
    if any(isinstance(value, str) and value.strip().lower() not in ('', 'none', 'offline') for value in references):
        return None  # A missing known primary must not be replaced by a secondary plate.
    if shot_dir:
        plates_dir = Path(folders.convert_path(shot_dir), 'plates')
        if plates_dir.is_dir():
            candidates = [p for p in plates_dir.iterdir() if p.is_file() and p.suffix.lower() in THUMBNAIL_VIDEO_EXTENSIONS]
            if len(candidates) == 1:
                return str(candidates[0])
    return None


def _source_thumbnail_frame(shot, folders, video_path):
    """Choose the middle of this occurrence's cut for shared/trimmed plates."""
    if shot.get('base_path') and Path(folders.convert_path(video_path)).resolve().is_relative_to(
            Path(folders.convert_path(shot['base_path'])).resolve() / 'renders'):
        return None
    metadata = _timeline_thumbnail_metadata(shot, folders)
    try:
        primary = next(p for p in metadata['plates'] if p.get('track') == 1)
        copied = primary.get('copied_path', '')
        if Path(video_path).name != Path(copied.replace('\\', '/')).name:
            return None  # Rendered previews retain their existing middle-frame behavior.
        return max(0, round(float(primary['cut_start']) +
                            (int(metadata['cut_duration']) - 1) * float(primary['speed']) / 2))
    except (KeyError, StopIteration, TypeError, ValueError, AttributeError):
        return None


def _source_thumbnail_colourspace(shot, folders, video_path, check_cancelled=lambda: False):
    """Apply a plate IDT only to identified source media, never rendered previews."""
    metadata = _timeline_thumbnail_metadata(shot, folders)
    plates = metadata.get('plates', [])
    primary = next((p for p in plates if isinstance(p, dict) and p.get('track') == 1), {}) if isinstance(plates, list) else {}
    shot_dir = shot.get('base_path')
    references = [primary.get('copied_path'), primary.get('local_path'), shot.get('original_clip')]
    originals = shot.get('original_clips')
    if isinstance(originals, list) and originals:
        references.append(originals[0])
    if not shot_dir and not any(isinstance(value, str) and value.strip() for value in references):
        return None
    path = Path(folders.convert_path(video_path)).resolve()
    if shot_dir:
        root = Path(folders.convert_path(shot_dir)).resolve()
        if path.is_relative_to(root / 'renders'):
            return None
        is_plate = path.parent == root / 'plates'
    else:
        is_plate = False
    selected_plate = {}
    if is_plate and isinstance(plates, list):
        selected_plate = next((p for p in plates if isinstance(p, dict)
                               and isinstance(p.get('copied_path'), str)
                               and Path(p['copied_path'].replace('\\', '/')).name == path.name), {})
    for reference in references:
        source = _shot_media_path(reference, shot_dir, folders)
        if source and source.resolve() == path:
            is_plate = True
            if not selected_plate and reference in (primary.get('copied_path'), primary.get('local_path')):
                selected_plate = primary
    if not is_plate:
        return None
    if selected_plate.get('input_colourspace'):
        return selected_plate['input_colourspace']
    from media_colourspace import detect_input_colourspace
    from timeline_import import Cancelled, probe_media

    try:
        detected = detect_input_colourspace(probe_media(str(path), check_cancelled).metadata)
    except Cancelled as exc:
        raise ThumbnailUpdateCancelled() from exc
    return detected.colourspace or shot.get('colourspace') or None


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
                    frame_index = _source_thumbnail_frame(shot, self.folders, preview)
                    options = {'frame_index': frame_index} if frame_index is not None else {}
                    colourspace = _source_thumbnail_colourspace(shot, self.folders, preview, self._cancelled.is_set)
                    if colourspace:
                        options['input_colourspace'] = colourspace
                    image = extract_preview_thumbnail(preview, check_cancelled=self._cancelled.is_set, **options)
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
