"""Allowlisted image/video input, decoded directly to float RGB in memory.

Image raster I/O uses OpenImageIO. MOV/MP4/MXF decode uses PyAV/FFmpeg without an
intermediate movie or copied source frames. Video frame ordinals are zero-based;
the source FPS is informative and does not resample or retime the input.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:
    import numpy as np
    from .sequences import SequenceInfo


IMAGE_EXTENSIONS = frozenset({".exr", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".dpx", ".bmp"})
VIDEO_EXTENSIONS = frozenset({".mov", ".mp4", ".mxf"})
SUPPORTED_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def default_source_color(path: Path | str) -> str:
    """A convenient default, with explicit user overrides for log/linear TIFF or DPX."""
    return "linear-srgb" if Path(path).suffix.casefold() == ".exr" else "srgb"


def _pyav():
    try:
        import av
    except ImportError as exc:
        raise RuntimeError("PyAV is required for MOV/MP4/MXF input. Install ShotBox Matte's declared dependencies.") from exc
    return av


def _video_errors(av) -> tuple[type[Exception], ...]:
    # Missing decoders can raise PyAV's LookupError-derived FFmpegError rather
    # than OSError/ValueError. Lightweight decoder doubles need not expose the
    # optional PyAV error module. Cancellation exceptions remain untouched.
    ffmpeg_error = getattr(getattr(av, "error", None), "FFmpegError", OSError)
    return OSError, ValueError, ffmpeg_error


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        from .core import CancelledError
        raise CancelledError("Source loading cancelled.")


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    count: int
    fps: float | None


def probe_video(path: Path | str) -> VideoInfo:
    """Read the container's frame count, decoding to count if it is unspecified.

    Positive container frame counts are trusted for discovery; execution checks
    that every requested frame actually decodes and reports truncated sources.
    The first video stream is used; audio streams are ignored.
    """
    path = Path(path)
    if path.suffix.casefold() not in VIDEO_EXTENSIONS:
        raise ValueError("Only MOV, MP4 and MXF video files are supported.")
    av = _pyav()
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError(f"MOV/MP4/MXF contains no video stream: {path}")
            stream = container.streams.video[0]
            width, height = int(stream.codec_context.width), int(stream.codec_context.height)
            count = int(stream.frames or 0)
            rate = stream.average_rate or stream.base_rate
            fps = float(rate) if rate is not None and rate > 0 else None
            if count < 1 or width < 1 or height < 1:
                count = 0
                for frame in container.decode(stream):
                    if count == 0:
                        width, height = int(frame.width), int(frame.height)
                    count += 1
            if count < 1 or width < 1 or height < 1:
                raise ValueError(f"MOV/MP4/MXF contains no decodable video frames: {path}")
            return VideoInfo(width, height, count, fps)
    except _video_errors(av) as exc:
        raise ValueError(f"Cannot inspect MOV/MP4/MXF {path}: {exc}") from exc


def read_image(path: Path | str) -> np.ndarray:
    """Read an allowlisted image using the shared full-float OpenImageIO reader."""
    from .imaging import read_exr
    path = Path(path)
    if path.suffix.casefold() not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported image extension: {path.suffix}")
    return read_exr(path)


def _decode_video_indices(path: Path, indices: tuple[int, ...],
                          cancelled: Callable[[], bool] | None = None) -> Iterator[np.ndarray]:
    import numpy as np
    if not indices:
        return
    av = _pyav()
    target = 0
    try:
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ValueError(f"MOV/MP4/MXF contains no video stream: {path}")
            stream = container.streams.video[0]
            for ordinal, frame in enumerate(container.decode(stream)):
                _check_cancelled(cancelled)
                if ordinal != indices[target]:
                    continue
                # rgb48le keeps 10/12/16-bit source detail instead of passing
                # through RGB24. FFmpeg applies the video's YUV range/matrix.
                pixels = frame.to_ndarray(format="rgb48le")
                yield np.ascontiguousarray(pixels, dtype=np.float32) / np.float32(65535.0)
                target += 1
                if target == len(indices):
                    return
    except _video_errors(av) as exc:
        raise ValueError(f"Cannot decode MOV/MP4/MXF {path}: {exc}") from exc
    raise ValueError(f"Video ended before requested frame {indices[target]} (zero-based): {path}. The source may be truncated or its frame-count metadata incorrect.")


def _validate_indices(indices) -> tuple[int, ...]:
    values = tuple(indices)
    if any(not isinstance(i, int) or i < 0 for i in values) or any(b <= a for a, b in zip(values, values[1:])):
        raise ValueError("Source frame indices must be non-negative integers in strictly increasing order.")
    return values


def read_source_frames(sequence: SequenceInfo, indices,
                       cancelled: Callable[[], bool] | None = None) -> Iterator[np.ndarray]:
    """Yield selected logical source frames, decoding a movie only once."""
    values = _validate_indices(indices)
    if values and values[-1] >= sequence.count:
        raise ValueError(f"Requested source frame {values[-1]}, but {sequence.count} frames were detected.")
    _check_cancelled(cancelled)
    if sequence.media_type == "video":
        ordinals = tuple(sequence.frame_indices[i] for i in values) if sequence.frame_indices else values
        yield from _decode_video_indices(sequence.video_path or sequence.files[0], ordinals, cancelled)
    else:
        for index in values:
            _check_cancelled(cancelled)
            yield read_image(sequence.files[index])


def read_frame(path: Path | str, index: int = 0) -> np.ndarray:
    """Read a movie ordinal or one image, for asynchronous UI previews."""
    path = Path(path)
    _validate_indices((index,))
    suffix = path.suffix.casefold()
    if suffix in VIDEO_EXTENSIONS:
        frames = _decode_video_indices(path, (index,))
        try:
            return next(frames)
        finally:
            frames.close()
    if suffix in IMAGE_EXTENSIONS:
        if index != 0:
            raise ValueError("A single image file has only frame index 0.")
        return read_image(path)
    raise ValueError("Select an EXR/image sequence, MOV, MP4 or MXF; other input formats are unsupported.")


def read_source_frame(sequence: SequenceInfo, index: int = 0) -> np.ndarray:
    frames = read_source_frames(sequence, (index,))
    try:
        return next(frames)
    finally:
        frames.close()
