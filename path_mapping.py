"""Cross-platform project path mapping helpers."""

from __future__ import annotations

import ntpath
import os
import platform
import posixpath
from dataclasses import dataclass


@dataclass(frozen=True)
class PathMapping:
    windows: tuple[str, ...]
    linux: tuple[str, ...]
    darwin: tuple[str, ...]

    def roots_for(self, system: str) -> tuple[str, ...]:
        if system == "Windows":
            return self.windows
        if system == "Linux":
            return self.linux
        if system == "Darwin":
            return self.darwin
        return ()


PATH_MAPPINGS = (
    PathMapping(
        windows=("Z:/PROJECTS",),
        linux=("/mnt/projects/PROJECTS", "/Volumes/projects/PROJECTS"),
        darwin=("/Volumes/projects/PROJECTS",),
    ),
    PathMapping(
        windows=("X:/Projects B",),
        linux=("/Volumes/Expansion_B/Projects B",),
        darwin=("/Volumes/Expansion_B/Projects B",),
    ),
)


def _normalize_for_matching(path: str) -> str:
    return path.replace("\\", "/")


def _matches_root(path: str, root: str, *, case_sensitive: bool) -> bool:
    normalized_root = _normalize_for_matching(root).rstrip("/")
    comparable_path = path if case_sensitive else path.casefold()
    comparable_root = normalized_root if case_sensitive else normalized_root.casefold()
    return comparable_path == comparable_root or comparable_path.startswith(comparable_root + "/")


def _pick_existing_root(candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        try:
            if os.path.exists(candidate):
                return candidate
        except OSError:
            continue
    return candidates[0] if candidates else ""


def _normalizer_for(system: str):
    return ntpath if system == "Windows" else posixpath


def convert_path(file_path, system: str | None = None) -> str:
    """Convert a known project path to the root used by ``system``.

    Unknown paths and paths already using a valid root for the target platform
    are returned unchanged. Root matching is boundary-aware so similarly named
    folders are not treated as mapped project roots.
    """
    value = str(file_path) if file_path is not None else ""
    if not value:
        return value

    target_system = system or platform.system()
    if target_system not in ("Windows", "Linux", "Darwin"):
        return value

    normalized = _normalize_for_matching(value)

    for mapping in PATH_MAPPINGS:
        target_roots = mapping.roots_for(target_system)
        if any(
            _matches_root(
                normalized,
                root,
                case_sensitive=target_system != "Windows",
            )
            for root in target_roots
        ):
            return value

        source_roots = (
            ("Windows", mapping.windows),
            ("Linux", mapping.linux),
            ("Darwin", mapping.darwin),
        )
        for source_system, roots in source_roots:
            for root in roots:
                normalized_root = _normalize_for_matching(root).rstrip("/")
                if not _matches_root(
                    normalized,
                    normalized_root,
                    case_sensitive=source_system != "Windows",
                ):
                    continue

                target_root = _pick_existing_root(target_roots)
                if not target_root:
                    return value
                suffix = normalized[len(normalized_root):].lstrip("/")
                mapped = target_root.rstrip("/")
                if suffix:
                    mapped += "/" + suffix
                return _normalizer_for(target_system).normpath(mapped)

    return value
