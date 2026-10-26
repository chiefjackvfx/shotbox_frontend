"""Bounded Alpha Gen segments for complete, frame-aligned shot processing."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math


MAX_MODEL_FRAMES = 145
PREFERRED_CHUNK_FRAMES = 121
CHUNK_OVERLAP = 17
TEMPORAL_MODES = ("segmented", "full-range")


def padded_frame_count(count: int) -> int:
    if not isinstance(count, int) or count < 1:
        raise ValueError("At least one source frame is required.")
    return ((count - 1 + 7) // 8) * 8 + 1


@dataclass(frozen=True)
class TemporalChunk:
    start: int
    stop: int
    padded_count: int

    @property
    def selected_count(self) -> int:
        return self.stop - self.start

    def to_dict(self) -> dict:
        return asdict(self)


def temporal_chunks(count: int, fps: float, mode: str = "segmented") -> tuple[TemporalChunk, ...]:
    if mode not in TEMPORAL_MODES:
        raise ValueError(f"Frame processing mode must be one of: {', '.join(TEMPORAL_MODES)}.")
    padded = padded_frame_count(count)
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or fps <= 0:
        raise ValueError("FPS must be a finite positive number.")
    if mode == "full-range":
        return (TemporalChunk(0, count, padded),)
    # Retain the existing single-pass recipe wherever it fits its frame/time limits.
    if padded <= MAX_MODEL_FRAMES and (padded - 1) / fps <= 6.0 + 1e-9:
        return (TemporalChunk(0, count, padded),)
    limit = min(PREFERRED_CHUNK_FRAMES, math.floor((6 * min(fps, 24) + 1e-9) / 8) * 8 + 1)
    overlap = min(CHUNK_OVERLAP, limit // 2)
    chunks = []
    start = 0
    while start < count:
        stop = min(start + limit, count)
        chunks.append(TemporalChunk(start, stop, padded_frame_count(stop - start)))
        if stop == count:
            break
        start = stop - overlap
    return tuple(chunks)
