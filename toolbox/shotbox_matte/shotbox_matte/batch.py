"""Headless discovery of mixed files and nested shot folders for batch queues.

Discovery inspects sources without loading weights or running inference. Each
image sequence is inspected separately, so unreadable sources cannot hide peers.
Directory traversal is deterministic and does not follow directory symlinks.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from .media import IMAGE_EXTENSIONS, SUPPORTED_EXTENSIONS, VIDEO_EXTENSIONS
from .sequences import SequenceInfo, _group, discover_sequences, natural_key


@dataclass(frozen=True)
class BatchDiscovery:
    sequences: list[SequenceInfo]
    errors: list[str]
    duplicate_count: int = 0


def _directory_link(path: Path) -> bool:
    # Windows junctions can form the same recursive cycles as directory
    # symlinks. pathlib exposes their check on supported Python versions.
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def discover_batch_sources(paths: Iterable[Path | str] | Path | str) -> BatchDiscovery:
    """Expand selected files/folders into unique image sequences and movies.

    Top-level selections keep their order. Within each folder, natural-sorted
    depth-first traversal includes nested shot folders, skipping dot directories
    and directory symlinks. Selecting multiple frames from one sequence or
    overlapping parent/child selections adds that source once. ``duplicate_count``
    counts repeated source selections, rather than the other frames of a sequence
    found during one folder scan.

    File selection discovers the entire numbered sequence alongside that frame.
    Folder scans ignore unsupported files; explicitly selecting one reports an
    error. Errors are collected per source and valid peers remain available.
    """
    sequences: list[SequenceInfo] = []
    errors: list[str] = []
    seen_sources: set[Path] = set()
    # Cache the image group, rather than an individual selected frame. It avoids
    # rescanning a shot folder for each file when users select an entire sequence.
    inspected: dict[tuple[Path, str], bool] = {}
    duplicates = 0

    def representatives(folder: Path) -> Iterator[Path]:
        try:
            entries = sorted(folder.iterdir(), key=lambda path: (natural_key(path.name), path.name))
        except OSError as exc:
            errors.append(f"Cannot read folder {folder}: {exc}")
            return
        groups: set[str] = set()
        for entry in entries:
            try:
                if entry.is_dir():
                    if not _directory_link(entry) and not entry.name.startswith("."):
                        yield from representatives(entry)
                elif entry.is_file() and entry.suffix.casefold() in SUPPORTED_EXTENSIONS:
                    if entry.suffix.casefold() in IMAGE_EXTENSIONS:
                        group, _number = _group(entry)
                        if group in groups:
                            continue
                        groups.add(group)
                    yield entry
            except OSError as exc:
                errors.append(f"Cannot inspect {entry}: {exc}")

    def add_source(candidate: Path) -> None:
        nonlocal duplicates
        try:
            canonical = candidate.resolve(strict=True)
            key = (canonical, "video") if canonical.suffix.casefold() in VIDEO_EXTENSIONS else (canonical.parent, _group(canonical)[0])
            if key in inspected:
                duplicates += int(inspected[key])
                return
            inspected[key] = False
            discovered = discover_sequences(canonical)
            for sequence in discovered:
                identity = (sequence.video_path or sequence.files[0]).resolve()
                if identity in seen_sources:
                    duplicates += 1
                else:
                    seen_sources.add(identity)
                    sequences.append(sequence)
            inspected[key] = bool(discovered)
        except Exception as exc:
            # Third-party decoders use several exception types. One damaged
            # header must not discard every other source in a bulk selection.
            errors.append(f"Cannot inspect source {candidate}: {exc}")

    # A single string/Path is convenient for callers and must not be iterated as
    # characters. The public API also accepts generators of many selected paths.
    selected = (paths,) if isinstance(paths, (str, Path)) else paths
    for value in selected:
        try:
            path = Path(value).expanduser().absolute()
            if not path.exists():
                raise ValueError(f"Input does not exist: {path}")
            if path.is_dir():
                if _directory_link(path):
                    raise ValueError(f"Directory symlinks are not followed: {path}")
                if path.name.startswith("."):
                    raise ValueError(f"Hidden folders are skipped: {path}")
                candidates = 0
                previous_errors = len(errors)
                for candidate in representatives(path):
                    candidates += 1
                    add_source(candidate)
                if not candidates and len(errors) == previous_errors:
                    errors.append(f"No supported EXR/image sequences, MOV, MP4 or MXF files found in {path}")
            elif path.is_file():
                if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                    raise ValueError(f"Unsupported input {path}; select EXR/image sequences, MOV, MP4 or MXF.")
                add_source(path)
            else:
                raise ValueError(f"Input is not a regular file or folder: {path}")
        except (OSError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    return BatchDiscovery(sequences, errors, duplicates)
