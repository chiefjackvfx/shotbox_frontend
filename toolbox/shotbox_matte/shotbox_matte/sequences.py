"""Natural image-sequence and MOV/MP4/MXF discovery without UI/inference imports."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .media import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS


def natural_key(value: str) -> tuple:
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", value))


@dataclass(frozen=True)
class SequenceInfo:
    id: str
    folder: Path
    files: tuple[Path, ...]
    frame_numbers: tuple[int | None, ...]
    width: int
    height: int
    media_type: str = "sequence"
    video_path: Path | None = None
    fps: float | None = None
    frame_indices: tuple[int, ...] = ()

    @property
    def count(self) -> int:
        return len(self.files)

    @property
    def first_frame(self) -> int:
        return self.frame_numbers[0] if self.frame_numbers and self.frame_numbers[0] is not None else 1001

    @property
    def source_range_mode(self) -> str:
        """Numbered sequences use filename numbers; movies/stills use 1-based positions."""
        return ("frame-number" if self.media_type != "video" and self.frame_numbers
                and all(number is not None for number in self.frame_numbers) else "ordinal")

    @property
    def source_start(self) -> int:
        return int(self.frame_numbers[0]) if self.source_range_mode == "frame-number" else 1

    @property
    def source_end(self) -> int:
        return int(self.frame_numbers[-1]) if self.source_range_mode == "frame-number" else self.count

    @property
    def output_source(self) -> Path:
        """A video derives its output stem from the movie filename, beside that file."""
        return self.folder / self.video_path.stem if self.media_type == "video" and self.video_path else self.folder

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "folder": str(self.folder),
                "files": [str(p) for p in self.files],
                "frame_numbers": list(self.frame_numbers), "source_numbers": list(self.frame_numbers),
                "width": self.width, "height": self.height, "count": self.count,
                "first_frame": self.first_frame,
                "source_range_mode": self.source_range_mode,
                "source_start": self.source_start, "source_end": self.source_end,
                "first_file": str(self.files[0]), "last_file": str(self.files[-1]),
                "media_type": self.media_type, "video_path": str(self.video_path) if self.video_path else None,
                "fps": self.fps, "frame_indices": list(self.frame_indices or range(self.count)),
                "output_source": str(self.output_source)}


def _group(path: Path) -> tuple[str, int | None]:
    found = re.fullmatch(r"(.*?)(\d+)", path.stem)
    if found:
        # Padding does not divide a sequence: shot.9 and shot.0010 belong together.
        return found[1] + "#" + path.suffix.casefold(), int(found[2])
    return path.name, None


def discover_sequences(path: Path | str) -> list[SequenceInfo]:
    """Group allowlisted image suffixes; each MOV/MP4/MXF is a separate selectable source."""
    from .imaging import read_dimensions
    from .media import probe_video

    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise ValueError(f"Input does not exist: {path}")
    selected = path if path.is_file() else None
    if selected and path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Select an EXR/image sequence, MOV, MP4 or MXF; other input formats are unsupported.")
    folder = path.parent if selected else path
    if not folder.is_dir():
        raise ValueError(f"Input is not a folder: {folder}")
    if selected and path.suffix.casefold() in VIDEO_EXTENSIONS:
        info = probe_video(path)
        return [SequenceInfo(path.name, folder, (path,) * info.count, (None,) * info.count,
                             info.width, info.height, "video", path, info.fps, tuple(range(info.count)))]
    groups: dict[str, list[tuple[Path, int | None]]] = {}
    videos: list[Path] = []
    for candidate in sorted(folder.iterdir(), key=lambda p: natural_key(p.name)):
        if not candidate.is_file():
            continue
        if candidate.suffix.casefold() in IMAGE_EXTENSIONS:
            key, number = _group(candidate)
            groups.setdefault(key, []).append((candidate, number))
        elif candidate.suffix.casefold() in VIDEO_EXTENSIONS:
            videos.append(candidate)
    if selected:
        key, _ = _group(selected)
        groups = {key: groups[key]} if key in groups else {}
        videos = []
    if not groups and not videos:
        raise ValueError(f"No supported EXR/image sequences, MOV, MP4 or MXF files found in {folder}")
    result = []
    for key, entries in sorted(groups.items(), key=lambda item: natural_key(item[0])):
        numbers = [number for _, number in entries if number is not None]
        if len(numbers) != len(set(numbers)):
            raise ValueError(f"Ambiguous duplicate frame numbers in {key}; split the sequence into separate folders.")
        width, height = read_dimensions(entries[0][0])
        result.append(SequenceInfo(key, folder, tuple(p for p, _ in entries),
                                   tuple(n for _, n in entries), width, height,
                                   frame_indices=tuple(range(len(entries)))))
    for video in videos:
        info = probe_video(video)
        result.append(SequenceInfo(video.name, folder, (video,) * info.count, (None,) * info.count,
                                   info.width, info.height, "video", video, info.fps, tuple(range(info.count))))
    return sorted(result, key=lambda sequence: natural_key(sequence.id))


def select_frame_indices(count: int, frame_skip: int = 0, frame_cap: int = 0) -> tuple[int, ...]:
    if not isinstance(count, int) or count < 1:
        raise ValueError("At least one source frame is required.")
    if not isinstance(frame_skip, int) or frame_skip < 0:
        raise ValueError("Frame skip must be a non-negative integer.")
    if not isinstance(frame_cap, int) or frame_cap < 0:
        raise ValueError("Frame cap must be a non-negative integer (0 means all).")
    indices = range(0, count, frame_skip + 1)
    return tuple(indices[:frame_cap] if frame_cap else indices)


def select_sequence_indices(sequence: SequenceInfo, frame_skip: int = 0, frame_cap: int = 0,
                            source_start: int | None = None, source_end: int | None = None) -> tuple[int, ...]:
    """Apply an inclusive source range, then skip and cap, returning absolute indices.

    A range endpoint inside a gap in a numbered sequence is allowed. A range
    containing no existing frames, or extending beyond the source, is an error.
    """
    # Validate skip/cap even when the requested range is invalid or empty.
    select_frame_indices(sequence.count, frame_skip, frame_cap)
    for name, value in (("in", source_start), ("out", source_end)):
        if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f"Source {name} must be an integer, or omitted to use the source bound.")
    start = sequence.source_start if source_start is None else source_start
    end = sequence.source_end if source_end is None else source_end
    if any(value < sequence.source_start or value > sequence.source_end for value in (start, end)):
        label = "frame numbers" if sequence.source_range_mode == "frame-number" else "1-based frame positions"
        raise ValueError(f"Source range {start}–{end} is outside {label} {sequence.source_start}–{sequence.source_end}.")
    if start > end:
        raise ValueError(f"Source in {start} must not exceed source out {end}.")
    values = sequence.frame_numbers if sequence.source_range_mode == "frame-number" else range(1, sequence.count + 1)
    available = tuple(index for index, number in enumerate(values) if start <= number <= end)
    if not available:
        raise ValueError(f"No source frames exist within inclusive range {start}–{end}.")
    return tuple(available[index] for index in select_frame_indices(len(available), frame_skip, frame_cap))


def select_frames(sequence: SequenceInfo, frame_skip: int = 0, frame_cap: int = 0,
                  source_start: int | None = None, source_end: int | None = None) -> tuple[tuple[Path, ...], tuple[int | None, ...]]:
    """Apply an inclusive range, skip N intervening frames, then cap the result."""
    indices = select_sequence_indices(sequence, frame_skip, frame_cap, source_start, source_end)
    return tuple(sequence.files[i] for i in indices), tuple(sequence.frame_numbers[i] for i in indices)
