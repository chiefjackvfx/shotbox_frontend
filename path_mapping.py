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


def _windows_network_root(drive: str) -> str | None:
    """Resolve a mapped drive using the current Windows logon session."""
    if os.name != "nt" or len(drive) != 2 or drive[1] != ":":
        return None
    import ctypes
    from ctypes import wintypes
    try:
        get_connection = ctypes.WinDLL("mpr").WNetGetConnectionW
        get_connection.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        get_connection.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(buffer))
        if get_connection(drive, buffer, ctypes.byref(length)) == 0:
            return buffer.value.replace("\\", "/")
    except OSError:
        pass
    return None


def comparison_path(file_path: str, system: str | None = None) -> str:
    """Compare project paths across drive letters and supported mount aliases.

    Keys always use forward slashes, so comparison doesn't depend on the host
    running the tests. Linux suffixes remain case-sensitive; Windows keys don't.
    These keys are for matching only, never opening files.
    """
    target_system = system or platform.system()
    paths = _normalizer_for(target_system)
    value = str(file_path or "").strip().strip('"').strip("'")
    if not value:
        return ""
    normalized = _normalize_for_matching(value)
    if target_system == "Windows":
        if normalized.casefold().startswith("//?/unc/"):
            normalized = "//" + normalized[8:]
        elif normalized.startswith("//?/"):
            normalized = normalized[4:]
        value = normalized
    for mapping in PATH_MAPPINGS:
        canonical_root = (mapping.windows or mapping.linux or mapping.darwin)[0].rstrip("/")
        aliases = [
            (mapping.windows, False), (mapping.linux, True), (mapping.darwin, True),
        ]
        if target_system == "Windows" and normalized.startswith("//"):
            for root in mapping.windows:
                drive, suffix = ntpath.splitdrive(root)
                network_root = _windows_network_root(drive)
                if network_root:
                    aliases.append(((network_root.rstrip("/") + suffix.replace("\\", "/"),), False))
        for roots, case_sensitive in aliases:
            for root in roots:
                if _matches_root(normalized, root, case_sensitive=case_sensitive):
                    suffix = normalized[len(_normalize_for_matching(root).rstrip("/")):].lstrip("/")
                    result = posixpath.normpath(canonical_root + "/" + suffix)
                    return result.casefold() if target_system == "Windows" else result
    return paths.normcase(paths.abspath(paths.normpath(value))).replace("\\", "/")


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
