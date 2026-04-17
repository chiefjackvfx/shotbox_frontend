#!/usr/bin/env python
"""
Nuke Headless Tasks
===================
Transcode video files with slate overlay using Nuke.

This module provides both the Nuke rendering task AND reusable utilities
for finding Nuke, building output paths, and running preview generation
from external code (e.g., PyQt widgets).

Usage:
    # Launch debug UI (no Nuke required)
    python nuke_headless_tasks.py
    
    # Run via Nuke headless
    /path/to/Nuke -t nuke_headless_tasks.py make_preview --input /path/to/clip.mov --output /path/to/preview.mp4

    # Render original clip to precomp EXR sequence (ACEScg)
    /path/to/Nuke -t nuke_headless_tasks.py make_precomp_exr --input /path/to/clip.mov --output /path/to/shot_v01_####.exr
    
    # Use as a module from PyQt code:
    from nuke_headless_tasks import PreviewGenerator
    generator = PreviewGenerator()
    result = generator.generate_preview(
        input_path="/path/to/clip.mov",
        shot_dir="/path/to/shot",
        shot_name="sho010",
        project="MyProject",
        colourspace="sRGB",
        fps=25
    )
"""

import sys
import os
import argparse
import importlib
import subprocess
import shutil
import glob
import platform
from pathlib import Path
from datetime import datetime
from typing import Optional, Callable, Tuple, List


# =============================================================================
# Preview Generation Utilities (Reusable from external code)
# =============================================================================

class PreviewConfig:
    """Configuration for preview generation."""
    
    # Default Nuke search paths by platform
    NUKE_SEARCH_PATHS = {
        'Linux': [
            "/opt/Nuke17.0v1/Nuke17.0",
            "/opt/Nuke16.0v8/Nuke16.0",
            "/opt/Nuke15.2v2/Nuke15.2",
            "/opt/Nuke15.1v1/Nuke15.1",
            "/opt/Nuke15.0v2/Nuke15.0",
            "/usr/local/Nuke17.0v1/Nuke17.0",
            "/usr/local/Nuke16.0v8/Nuke16.0",
            "/usr/local/Nuke15.2v2/Nuke15.2",
            "/usr/local/Nuke15.1v1/Nuke15.1",
            "/usr/local/Nuke15.0v2/Nuke15.0",
            "/usr/local/Nuke14.0v5/Nuke14.0",
        ],
        'Windows': [
            "C:/Program Files/Nuke17.0v1/Nuke17.0.exe",
            "C:/Program Files/Nuke16.0v8/Nuke16.0.exe",
            "C:/Program Files/Nuke15.2v2/Nuke15.2.exe",
            "C:/Program Files/Nuke15.1v1/Nuke15.1.exe",
            "C:/Program Files/Nuke15.0v2/Nuke15.0.exe",
            "C:/Program Files/Nuke14.0v5/Nuke14.0.exe",
        ],
        'Darwin': [  # macOS
            "/Applications/Nuke16.0v8/Nuke16.0.app/Contents/MacOS/Nuke16.0",
            "/Applications/Nuke15.2v2/Nuke15.2.app/Contents/MacOS/Nuke15.2",
            "/Applications/Nuke15.1v1/Nuke15.1.app/Contents/MacOS/Nuke15.1",
            "/Applications/Nuke15.0v2/Nuke15.0.app/Contents/MacOS/Nuke15.0",
        ],
    }
    
    # Default preview output subdirectory
    PREVIEW_SUBDIR = "renders/precomp/previews"
    PRECOMP_SUBDIR = "renders/precomp"
    
    # Default quality
    DEFAULT_QUALITY = "medium"
    
    # Default artist name
    DEFAULT_ARTIST = "ShotBox"

    # Preview runtime template
    PREVIEW_TEMPLATE_NAME = "preview_template_v001.nk"

    # Nuke 17 / OCIO v2.4 studio config used across generated scripts and headless renders
    OCIO_CONFIG_NAME = "fn-nuke_studio-config-v3.0.0_aces-v2.0_ocio-v2.4"


def _extract_nuke_from_dir(candidate_dir: str) -> Optional[str]:
    """Find a Nuke executable inside a directory."""
    if not candidate_dir or not os.path.isdir(candidate_dir):
        return None
    system = platform.system()
    if system == "Windows":
        patterns = [
            os.path.join(candidate_dir, "Nuke*.exe"),
            os.path.join(candidate_dir, "Nuke*", "Nuke*.exe"),
        ]
    else:
        patterns = [
            os.path.join(candidate_dir, "Nuke*"),
        ]
    matches = []
    for pattern in patterns:
        matches.extend(glob.glob(pattern))
    for match in sorted(matches, reverse=True):
        if os.path.isfile(match):
            return match
    return None


def find_nuke_executable(custom_paths: Optional[List[str]] = None) -> Optional[str]:
    """
    Find Nuke executable on the system.
    
    Args:
        custom_paths: Optional list of paths to search first
        
    Returns:
        Path to Nuke executable, or None if not found
    """
    search_paths = []
    extra_dirs = []
    preferred_files = []
    preferred_dirs = []

    # Environment overrides
    for key in ("NUKE_EXE", "NUKE_PATH", "NUKE_HOME"):
        env_value = os.environ.get(key)
        if not env_value:
            continue
        if os.path.isfile(env_value):
            preferred_files.append(env_value)
        elif os.path.isdir(env_value):
            preferred_dirs.append(env_value)
    
    # Add custom paths first
    if custom_paths:
        for path in custom_paths:
            if os.path.isfile(path):
                preferred_files.append(path)
            elif os.path.isdir(path):
                preferred_dirs.append(path)
            else:
                search_paths.append(path)

    for path in preferred_files:
        if os.path.isfile(path):
            return path

    for directory in dict.fromkeys(preferred_dirs):
        found = _extract_nuke_from_dir(directory)
        if found:
            return found
    
    # Add platform-specific paths
    system = platform.system()
    if system in PreviewConfig.NUKE_SEARCH_PATHS:
        search_paths.extend(PreviewConfig.NUKE_SEARCH_PATHS[system])

    for path in search_paths:
        if os.path.isfile(path):
            return path
        if os.path.isdir(path):
            extra_dirs.append(path)

    # Look inside directories for a Nuke executable
    for directory in dict.fromkeys(extra_dirs):
        found = _extract_nuke_from_dir(directory)
        if found:
            return found

    # Windows: scan Program Files for Nuke installs
    if system == "Windows":
        program_dirs = [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
        ]
        for base in filter(None, program_dirs):
            for subdir in ("", "Foundry", "The Foundry"):
                candidate = os.path.join(base, subdir)
                found = _extract_nuke_from_dir(candidate)
                if found:
                    return found

    # Fall back to PATH lookup
    for exe_name in ("Nuke", "Nuke16.0", "Nuke15.2", "Nuke15.1", "Nuke15.0", "Nuke14.0"):
        found = shutil.which(exe_name)
        if found:
            return found

    return None


def get_headless_script_path() -> Path:
    """
    Get the path to this nuke_headless_tasks.py script.
    
    Returns:
        Path to the script
    """
    return Path(__file__).resolve()


def get_preview_template_path() -> Path:
    """Get the path to the runtime preview template."""
    return Path(__file__).resolve().parent / PreviewConfig.PREVIEW_TEMPLATE_NAME


def _normalize_nuke_path(path) -> str:
    return str(path).replace("\\", "/")


def _expand_sequence_pattern(input_path: str) -> List[Path]:
    """Resolve a sequence pattern like #### or %04d to matching files."""
    normalized = str(input_path)
    if os.path.exists(normalized):
        return [Path(normalized)]

    glob_pattern = normalized
    if "#" in glob_pattern:
        glob_pattern = glob_pattern.replace("#", "?")
    glob_pattern = glob_pattern.replace("%04d", "????")
    glob_pattern = glob_pattern.replace("%03d", "???")
    glob_pattern = glob_pattern.replace("%02d", "??")
    glob_pattern = glob_pattern.replace("%d", "*")
    if glob_pattern == normalized:
        return []
    return sorted(Path(path) for path in glob.glob(glob_pattern))


def _match_exr_frame_token(file_name: str):
    import re

    return re.match(r"^(.*?)(\d+)([^0-9]*\.exr)$", file_name, re.IGNORECASE)


def _contains_sequence_token(path_text: str) -> bool:
    import re

    return "#" in path_text or bool(re.search(r"%0?\d*d", path_text))


def _sequence_pattern_from_file(file_path: Path) -> Optional[str]:
    match = _match_exr_frame_token(file_path.name)
    if not match:
        return None
    prefix, frame_str, suffix = match.groups()
    return str(file_path.with_name(f"{prefix}{'#' * len(frame_str)}{suffix}"))


def _collect_matching_exr_files(reference_path: Path) -> List[Path]:
    match = _match_exr_frame_token(reference_path.name)
    if not match:
        return []

    prefix, _frame_str, suffix = match.groups()
    parent = reference_path.parent
    if not parent.exists():
        return []

    matches: List[Path] = []
    for candidate in sorted(parent.iterdir()):
        if not candidate.is_file():
            continue
        candidate_match = _match_exr_frame_token(candidate.name)
        if not candidate_match:
            continue
        candidate_prefix, _candidate_frame, candidate_suffix = candidate_match.groups()
        if candidate_prefix == prefix and candidate_suffix == suffix:
            matches.append(candidate)
    return matches


def resolve_preview_sequence_input(input_path: str) -> Tuple[str, Optional[int], Optional[int]]:
    """
    Resolve preview input to a Nuke-friendly path and optional detected frame range.

    For EXR sequences, prefer an existing frame file for `fromUserText(...)` and
    provide the discovered first/last frame explicitly so Nuke does not fall back
    to frame 1 when the sequence starts at 1001.
    """
    normalized = str(input_path)
    if not normalized.lower().endswith(".exr"):
        return normalized, None, None

    existing_files: List[Path] = []
    sequence_path = normalized

    if _contains_sequence_token(normalized):
        matches = _expand_sequence_pattern(normalized)
        if not matches:
            return normalized, None, None
        first_match = matches[0]
        existing_files = _collect_matching_exr_files(first_match)
        sequence_path = _sequence_pattern_from_file(first_match) or normalized
    else:
        current_path = Path(normalized)
        existing_files = _collect_matching_exr_files(current_path)
        if existing_files:
            sequence_path = _sequence_pattern_from_file(existing_files[0]) or normalized
        elif os.path.exists(normalized):
            return normalized, None, None

    if not existing_files:
        return normalized, None, None

    frames = []
    for candidate in existing_files:
        match = _match_exr_frame_token(candidate.name)
        if not match:
            continue
        try:
            frames.append(int(match.group(2)))
        except ValueError:
            continue

    if not frames:
        return sequence_path, None, None

    frames.sort()
    return sequence_path, frames[0], frames[-1]


def preview_input_exists(input_path: str) -> bool:
    """Return True when the input file or sequence pattern resolves to media."""
    if not input_path:
        return False
    if os.path.exists(input_path):
        return True
    if _expand_sequence_pattern(input_path):
        return True
    _resolved_path, detected_first, detected_last = resolve_preview_sequence_input(input_path)
    return detected_first is not None and detected_last is not None


def resolve_preview_input_colourspace(
    *,
    source_type: Optional[str] = None,
    media_type: Optional[str] = None,
    input_path: str = "",
    requested_colourspace: str = "sRGB",
) -> str:
    """Resolve the read-node input transform for preview generation."""
    normalized_path = str(input_path or "").lower()
    normalized_media_type = str(media_type or "").lower()
    normalized_source_type = str(source_type or "").lower()

    if normalized_media_type == "exr" or normalized_path.endswith(".exr"):
        return "ACES - ACEScg"
    if normalized_source_type == "render" and (normalized_media_type == "mov" or normalized_path.endswith(".mov")):
        return "sRGB"
    return requested_colourspace or "sRGB"


def _normalize_plate_name(plate_name: Optional[str]) -> Optional[str]:
    normalized_plate_name = str(plate_name or "").strip()
    return normalized_plate_name or None


def _source_output_base_name(shot_name: str, plate_name: Optional[str] = None) -> str:
    normalized_plate_name = _normalize_plate_name(plate_name)
    if normalized_plate_name is None:
        return shot_name
    return f"{shot_name}_{normalized_plate_name}"


def _preview_matches_name(file_name: str, shot_name: str, plate_name: Optional[str] = None) -> bool:
    import re

    normalized_plate_name = _normalize_plate_name(plate_name)
    if normalized_plate_name is None:
        pattern = rf"^{re.escape(shot_name)}(?:_plate_\d{{2}})?_v\d+(?:_preview)?\.mp4$"
    else:
        pattern = rf"^{re.escape(shot_name)}_{re.escape(normalized_plate_name)}_v\d+(?:_preview)?\.mp4$"
    return bool(re.match(pattern, file_name, re.IGNORECASE))


def _preview_file_candidates(
    shot_dir: str,
    shot_name: str,
    plate_name: Optional[str] = None,
    preview_subdir: str = PreviewConfig.PREVIEW_SUBDIR,
) -> List[Path]:
    previews_dir = Path(shot_dir) / preview_subdir
    if not previews_dir.exists():
        return []
    return sorted(
        preview_path
        for preview_path in previews_dir.glob("*.mp4")
        if _preview_matches_name(preview_path.name, shot_name, plate_name=plate_name)
    )


def _is_legacy_preview_name(file_name: str) -> bool:
    return file_name.lower().endswith("_preview.mp4")


def list_existing_preview_paths(
    shot_dir: str,
    shot_name: str,
    version: int,
    plate_name: Optional[str] = None,
    preview_subdir: str = PreviewConfig.PREVIEW_SUBDIR,
) -> List[Path]:
    """Find preview files for a shot/version in either new or legacy naming."""
    matches: List[Path] = []
    for preview_path in _preview_file_candidates(
        shot_dir,
        shot_name,
        plate_name=plate_name,
        preview_subdir=preview_subdir,
    ):
        path_version = extract_version_from_path(preview_path.name)
        if path_version == version:
            matches.append(preview_path)
    matches.sort(key=lambda path: (path.name.lower().endswith("_preview.mp4"), path.name.lower()))
    return matches


def preview_version_exists(
    shot_dir: str,
    shot_name: str,
    version: int,
    plate_name: Optional[str] = None,
    preview_subdir: str = PreviewConfig.PREVIEW_SUBDIR,
) -> bool:
    return bool(
        list_existing_preview_paths(
            shot_dir,
            shot_name,
            version,
            plate_name=plate_name,
            preview_subdir=preview_subdir,
        )
    )


def build_preview_output_path(
    shot_dir: str,
    shot_name: str,
    version: Optional[int] = None,
    plate_name: Optional[str] = None,
    preview_subdir: str = PreviewConfig.PREVIEW_SUBDIR
) -> Tuple[Path, int, bool]:
    """
    Build the output path for a preview video.
    
    Creates the output directory if it doesn't exist.
    Output format: {shot_name}[_plate_01]_v{version:03d}.mp4
    
    Args:
        shot_dir: Base shot directory
        shot_name: Name of the shot (used for filename)
        version: Explicit version number to use. If None, auto-increments.
        preview_subdir: Subdirectory within shot_dir for previews
        
    Returns:
        Tuple of (output_path, version_number, file_exists)
    """
    previews_dir = Path(shot_dir) / preview_subdir
    previews_dir.mkdir(parents=True, exist_ok=True)
    
    if version is not None:
        # Use explicit version
        output_path = previews_dir / f"{_source_output_base_name(shot_name, plate_name)}_v{version:03d}.mp4"
        file_exists = preview_version_exists(
            shot_dir,
            shot_name,
            version,
            plate_name=plate_name,
            preview_subdir=preview_subdir,
        )
        return output_path, version, file_exists
    
    # Auto-increment: find next version number
    existing_previews = _preview_file_candidates(
        shot_dir,
        shot_name,
        plate_name=plate_name,
        preview_subdir=preview_subdir,
    )
    
    if existing_previews:
        versions = []
        for p in existing_previews:
            version_value = extract_version_from_path(p.name)
            if version_value is not None:
                versions.append(version_value)
        next_version = max(versions) + 1 if versions else 1
    else:
        next_version = 1
    
    output_path = previews_dir / f"{_source_output_base_name(shot_name, plate_name)}_v{next_version:03d}.mp4"
    return output_path, next_version, False  # Auto-increment never conflicts


def extract_version_from_path(file_path: str) -> Optional[int]:
    """
    Extract version number from a file path.
    
    Looks for patterns like _v01, _v02, etc.
    
    Args:
        file_path: Path to file
        
    Returns:
        Version number or None if not found
    """
    import re
    target = Path(file_path).name
    match = re.search(r'_v(\d+)(?:(?:_preview)?\.[^.]+|[_.-]|$)', target, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None


def _preview_slate_name(shot_name: str, output_path: str) -> str:
    output_stem = Path(output_path).stem
    return output_stem or shot_name


def _duration_timecode(frame_count: int, fps: float) -> str:
    frame_rate = max(int(round(fps)), 1)
    total = max(int(frame_count) - 1, 0)
    hours = total // (frame_rate * 3600)
    minutes = (total % (frame_rate * 3600)) // (frame_rate * 60)
    seconds = (total % (frame_rate * 60)) // frame_rate
    frames = total % frame_rate
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


class _PreviewTemplateEditor:
    REQUIRED_NODES = (
        "Read1",
        "Retime1",
        "WriteCompMP4",
        "MS_slate_overlay",
    )
    REQUIRED_SLATE_KNOBS = (
        "projecttext",
        "artisttext",
        "commenttext",
        "startfr",
    )

    def __init__(self, nuke_module):
        self.nuke = nuke_module

    def render(
        self,
        *,
        template_path: str,
        input_path: str,
        output_path: str,
        shot_name: str,
        project: str,
        artist: str,
        colourspace: str,
        fps: float,
        quality: str,
    ) -> Tuple[int, int]:
        self.nuke.scriptOpen(_normalize_nuke_path(template_path))
        self._validate_template()

        read = self._required_node("Read1")
        retime = self._required_node("Retime1")
        resolved_input_path, detected_first, detected_last = resolve_preview_sequence_input(input_path)
        if detected_first is not None and detected_last is not None:
            self._set_file_knob(read["file"], resolved_input_path, frame_range=(detected_first, detected_last))
            self._set_optional_knob(read, "first", detected_first)
            self._set_optional_knob(read, "last", detected_last)
            self._set_optional_knob(read, "origfirst", detected_first)
            self._set_optional_knob(read, "origlast", detected_last)
            self._set_optional_knob(read, "origset", True)
        else:
            self._set_file_knob(read["file"], resolved_input_path)
        resolved_colourspace = resolve_preview_input_colourspace(
            media_type=Path(resolved_input_path).suffix.lstrip("."),
            input_path=resolved_input_path,
            requested_colourspace=colourspace,
        )
        self._set_optional_knob(read, "colorspace", map_input_colorspace(resolved_colourspace))

        source_first = int(read["first"].value())
        source_last = int(read["last"].value())
        duration = max(source_last - source_first + 1, 1)
        vfx_first = 1001
        vfx_last = vfx_first + duration - 1

        self._set_optional_knob(retime, "input.first", source_first)
        self._set_optional_knob(retime, "input.first_lock", True)
        self._set_optional_knob(retime, "input.last", source_last)
        self._set_optional_knob(retime, "input.last_lock", True)
        self._set_optional_knob(retime, "output.first", vfx_first)
        self._set_optional_knob(retime, "output.first_lock", True)
        self._set_optional_knob(retime, "output.last", vfx_last)
        self._set_optional_knob(retime, "output.last_lock", True)

        root = self.nuke.root()
        self._set_optional_knob(root, "frame", vfx_first)
        self._set_optional_knob(root, "first_frame", vfx_first)
        self._set_optional_knob(root, "last_frame", vfx_last)
        self._set_optional_knob(root, "fps", fps)
        self._set_optional_knob(root, "colorManagement", "OCIO")
        self._set_optional_knob(root, "OCIO_config", PreviewConfig.OCIO_CONFIG_NAME)
        self._set_optional_knob(root, "workingSpaceLUT", "ACES - ACEScg")
        if "name" in root.knobs():
            try:
                self._set_knob(root["name"], f"{_preview_slate_name(shot_name, output_path)}.nk")
            except Exception:
                pass

        write = self._required_node("WriteCompMP4")
        self._set_file_knob(write["file"], output_path)
        self._set_optional_knob(write, "file_type", "mov")
        self._set_optional_knob(write, "create_directories", True)
        self._set_optional_knob(write, "checkHashOnRead", False)
        self._set_optional_knob(write, "colorspace", "color_picking")
        self._set_optional_knob(write, "ocioColorspace", "scene_linear")
        self._set_optional_knob(write, "display", "ACES")
        self._set_optional_knob(write, "view", "sRGB")
        self._set_optional_knob(write, "mov64_quality", {"low": "Low", "medium": "Medium", "high": "High"}.get(quality, "Medium"))

        slate_group = self._required_node("MS_slate_overlay")
        self._set_optional_knob(slate_group, "projecttext", project)
        self._set_optional_knob(slate_group, "artisttext", artist)
        self._set_optional_knob(slate_group, "commenttext", "")
        self._set_optional_knob(slate_group, "startfr", vfx_first)

        slate_group.begin()
        try:
            readtime = self._required_node("readtime")
            self._set_optional_knob(readtime, "tc1", _duration_timecode(duration, fps))
        finally:
            slate_group.end()

        # The template ships with a separate white Text11 overlay. Blank it so
        # only the slate's built-in orange text renders in previews.
        text11 = self.nuke.toNode("Text11")
        if text11 is not None:
            self._set_optional_knob(text11, "message", "")

        self.nuke.execute(write, vfx_first, vfx_last)
        return vfx_first, vfx_last

    def _required_node(self, name: str):
        node = self.nuke.toNode(name)
        if node is None:
            raise RuntimeError(f"Template node not found: {name}")
        return node

    def _validate_template(self):
        missing = [name for name in self.REQUIRED_NODES if self.nuke.toNode(name) is None]
        if missing:
            raise RuntimeError("Preview template is missing required nodes: " + ", ".join(missing))
        slate_group = self._required_node("MS_slate_overlay")
        missing_knobs = [name for name in self.REQUIRED_SLATE_KNOBS if name not in slate_group.knobs()]
        if missing_knobs:
            raise RuntimeError(
                "Preview template MS_slate_overlay is missing required knobs: " + ", ".join(missing_knobs)
            )
        slate_group.begin()
        try:
            if self.nuke.toNode("readtime") is None:
                raise RuntimeError("Preview template MS_slate_overlay is missing required node: readtime")
        finally:
            slate_group.end()

    def _set_optional_knob(self, node, knob_name: str, value):
        if node is None or knob_name not in node.knobs():
            return
        self._set_knob(node[knob_name], value)

    def _set_file_knob(self, knob, file_path: str, frame_range: Optional[Tuple[int, int]] = None):
        normalized = _normalize_nuke_path(file_path)
        from_user_text = getattr(knob, "fromUserText", None)
        if callable(from_user_text):
            user_text = normalized
            if frame_range is not None:
                user_text = f"{normalized} {int(frame_range[0])}-{int(frame_range[1])}"
            from_user_text(user_text)
            return
        self._set_knob(knob, normalized)

    def _set_knob(self, knob, value):
        try:
            knob.setValue(value)
            return
        except TypeError as exc:
            original_error = exc
        if isinstance(value, str):
            values = getattr(knob, "values", None)
            if callable(values):
                try:
                    options = list(values())
                except Exception:
                    options = []
                if value in options:
                    knob.setValue(value)
                    return
        raise original_error


def map_input_colorspace(colourspace: str) -> str:
    """Map user-facing colorspace to an OCIO colorspace name."""
    colorspace_map = {
        'sRGB': 'Utility - sRGB - Texture',
        'Rec.709': 'Utility - Rec.709 - Camera',
        'Rec709': 'Utility - Rec.709 - Camera',
        'rec709': 'Utility - Rec.709 - Camera',
        'Linear': 'Utility - Linear - sRGB',
        'ACEScg': 'ACES - ACEScg',
        'ACES - ACEScg': 'ACES - ACEScg',
        'AlexaV3LogC': 'Input - ARRI - V3 LogC (EI800) - Wide Gamut',
        'ARRI LogC': 'Input - ARRI - V3 LogC (EI800) - Wide Gamut',
        'Arri LogC': 'Input - ARRI - V3 LogC (EI800) - Wide Gamut',
        'Input - ARRI - V3 LogC (EI800) - Wide Gamut': 'Input - ARRI - V3 LogC (EI800) - Wide Gamut',
        'Sony S-Log3': 'Input - Sony - S-Log3 - S-Gamut3.Cine',
        'S-Log3': 'Input - Sony - S-Log3 - S-Gamut3.Cine',
    }
    return colorspace_map.get(colourspace, 'Utility - sRGB - Texture')


def build_precomp_exr_output_path(
    shot_dir: str,
    shot_name: str,
    version: int = 1,
    plate_name: Optional[str] = None,
    precomp_subdir: str = PreviewConfig.PRECOMP_SUBDIR,
) -> Tuple[Path, Path, bool]:
    """
    Build the output path for a precomp EXR sequence.

    Output folder: renders/precomp/{shot_name}[_plate_01]_v{version:02d}
    Output file: {shot_name}[_plate_01]_v{version:02d}_####.exr

    Returns:
        Tuple of (sequence_path, output_dir, file_exists)
    """
    output_base_name = _source_output_base_name(shot_name, plate_name)
    precomp_dir = Path(shot_dir) / precomp_subdir / f"{output_base_name}_v{version:02d}"
    precomp_dir.mkdir(parents=True, exist_ok=True)
    sequence_path = precomp_dir / f"{output_base_name}_v{version:02d}_####.exr"
    file_exists = any(precomp_dir.glob("*.exr"))
    return sequence_path, precomp_dir, file_exists


def build_preview_command(
    nuke_exe: str,
    input_path: str,
    output_path: str,
    shot_name: str = "",
    project: str = "",
    artist: str = PreviewConfig.DEFAULT_ARTIST,
    colourspace: str = "sRGB",
    fps: float = 25,
    quality: str = PreviewConfig.DEFAULT_QUALITY,
    headless_script: Optional[str] = None
) -> List[str]:
    """
    Build the command line for running Nuke headless preview generation.
    
    Args:
        nuke_exe: Path to Nuke executable
        input_path: Input video/image sequence path
        output_path: Output MP4 path
        shot_name: Shot name for slate
        project: Project name for slate
        artist: Artist name for slate
        colourspace: Input colorspace
        fps: Output FPS
        quality: Output quality (low/medium/high)
        headless_script: Optional path to headless script (defaults to this file)
        
    Returns:
        Command as list of strings
    """
    if headless_script is None:
        headless_script = str(get_headless_script_path())
    
    return [
        nuke_exe,
        "-t", headless_script, "make_preview",
        "--input", str(input_path),
        "--output", str(output_path),
        "--shot_name", shot_name,
        "--project", project,
        "--artist", artist,
        "--colourspace", colourspace or "sRGB",
        f"--fps={fps}",
        "--quality", quality,
    ]


def build_precomp_exr_command(
    nuke_exe: str,
    input_path: str,
    output_path: str,
    shot_name: str = "",
    colourspace: str = "sRGB",
    fps: float = 25,
    headless_script: Optional[str] = None,
) -> List[str]:
    """Build the command line for running Nuke headless precomp EXR generation."""
    if headless_script is None:
        headless_script = str(get_headless_script_path())

    return [
        nuke_exe,
        "-t", headless_script, "make_precomp_exr",
        "--input", str(input_path),
        "--output", str(output_path),
        "--shot_name", shot_name,
        "--colourspace", colourspace or "sRGB",
        f"--fps={fps}",
    ]


class PreviewResult:
    """Result of a preview generation operation."""
    
    def __init__(
        self,
        success: bool,
        output_path: Optional[Path] = None,
        version: int = 0,
        relative_path: str = "",
        error: str = "",
        output_lines: Optional[List[str]] = None
    ):
        self.success = success
        self.output_path = output_path
        self.version = version
        self.relative_path = relative_path  # Relative path for database storage
        self.error = error
        self.output_lines = output_lines or []
    
    def __bool__(self):
        return self.success


class PrecompResult:
    """Result of a precomp EXR generation operation."""

    def __init__(
        self,
        success: bool,
        sequence_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
        version: int = 0,
        relative_path: str = "",
        error: str = "",
        output_lines: Optional[List[str]] = None,
    ):
        self.success = success
        self.sequence_path = sequence_path
        self.output_dir = output_dir
        self.version = version
        self.relative_path = relative_path
        self.error = error
        self.output_lines = output_lines or []

    def __bool__(self):
        return self.success


class PreviewGenerator:
    """
    High-level interface for generating preview videos.
    
    This class encapsulates all the logic for finding Nuke, building paths,
    and running the headless render process. It can be used from PyQt widgets
    or any other Python code.
    
    Example:
        generator = PreviewGenerator()
        
        # Check if Nuke is available
        if not generator.nuke_available:
            print("Nuke not found!")
            return
        
        # Generate preview with callback for progress
        def on_output(line):
            print(line)
        
        result = generator.generate_preview(
            input_path="/path/to/clip.mov",
            shot_dir="/path/to/shot",
            shot_name="sho010",
            project="MyProject",
            on_output=on_output
        )
        
        if result.success:
            print(f"Preview created: {result.output_path}")
        else:
            print(f"Error: {result.error}")
    """
    
    def __init__(self, nuke_path: Optional[str] = None):
        """
        Initialize the preview generator.
        
        Args:
            nuke_path: Optional explicit path to Nuke executable
        """
        self._nuke_path = nuke_path
        self._cached_nuke_exe = None
    
    @property
    def nuke_exe(self) -> Optional[str]:
        """Get the Nuke executable path (cached)."""
        if self._cached_nuke_exe is None:
            if self._nuke_path and os.path.exists(self._nuke_path):
                self._cached_nuke_exe = self._nuke_path
            else:
                self._cached_nuke_exe = find_nuke_executable()
        return self._cached_nuke_exe
    
    @property
    def nuke_available(self) -> bool:
        """Check if Nuke is available."""
        return self.nuke_exe is not None
    
    @property
    def headless_script(self) -> Path:
        """Get the path to the headless script."""
        return get_headless_script_path()
    
    def validate_input(self, input_path: str) -> Tuple[bool, str]:
        """
        Validate input file exists.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not input_path:
            return False, "No input path provided"
        if not preview_input_exists(input_path):
            return False, f"Input file not found: {input_path}"
        return True, ""
    
    def generate_preview(
        self,
        input_path: str,
        shot_dir: str,
        shot_name: str,
        project: str = "",
        artist: str = PreviewConfig.DEFAULT_ARTIST,
        colourspace: str = "sRGB",
        fps: float = 25,
        quality: str = PreviewConfig.DEFAULT_QUALITY,
        version: Optional[int] = None,
        plate_name: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PreviewResult:
        """
        Generate a preview video.
        
        Args:
            input_path: Path to input video/image sequence
            shot_dir: Shot directory for output
            shot_name: Shot name (used for filename and slate)
            project: Project name for slate
            artist: Artist name for slate
            colourspace: Input colorspace
            fps: Output FPS
            quality: Output quality
            version: Explicit version number. If None, auto-increments.
            plate_name: Optional source plate suffix such as plate_01.
            on_output: Optional callback for stdout lines
            check_cancelled: Optional callback to check if operation was cancelled
            
        Returns:
            PreviewResult with success status and details
        """
        # Validate Nuke
        if not self.nuke_available:
            return PreviewResult(
                success=False,
                error="Nuke executable not found. Please ensure Nuke is installed."
            )
        
        # Validate input
        valid, error = self.validate_input(input_path)
        if not valid:
            return PreviewResult(success=False, error=error)
        
        # Validate headless script
        if not self.headless_script.exists():
            return PreviewResult(
                success=False,
                error=f"Headless script not found: {self.headless_script}"
            )
        preview_template = get_preview_template_path()
        if not preview_template.exists():
            return PreviewResult(
                success=False,
                error=f"Preview template not found: {preview_template}"
            )
        
        # Build output path
        output_path, actual_version, file_exists = build_preview_output_path(
            shot_dir,
            shot_name,
            version=version,
            plate_name=plate_name,
        )
        
        # If file exists, return with file_exists flag for caller to handle
        if file_exists:
            return PreviewResult(
                success=False,
                output_path=output_path,
                version=actual_version,
                error="FILE_EXISTS",  # Special error code for overwrite prompt
                relative_path=f"{PreviewConfig.PREVIEW_SUBDIR}/{output_path.name}"
            )
        
        # Build command
        cmd = build_preview_command(
            nuke_exe=self.nuke_exe,
            input_path=input_path,
            output_path=str(output_path),
            shot_name=shot_name,
            project=project,
            artist=artist,
            colourspace=colourspace,
            fps=fps,
            quality=quality,
            headless_script=str(self.headless_script)
        )
        
        # Run the process
        cmd_display = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        output_lines = [
            f"NUKE_EXE: {self.nuke_exe}",
            f"HEADLESS_SCRIPT: {self.headless_script}",
            f"PREVIEW_TEMPLATE: {preview_template}",
            f"CMD: {cmd_display}",
        ]
        if on_output:
            for line in output_lines:
                on_output(line)
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )
            
            while True:
                # Check for cancellation
                if check_cancelled and check_cancelled():
                    process.terminate()
                    return PreviewResult(
                        success=False,
                        error="Operation cancelled by user",
                        output_lines=output_lines
                    )
                
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                
                if line:
                    line = line.strip()
                    output_lines.append(line)
                    if on_output:
                        on_output(line)
            
            process.wait()
            
            if process.returncode == 0:
                # Build relative path for database
                relative_path = f"{PreviewConfig.PREVIEW_SUBDIR}/{output_path.name}"
                return PreviewResult(
                    success=True,
                    output_path=output_path,
                    version=actual_version,
                    relative_path=relative_path,
                    output_lines=output_lines
                )
            else:
                output_lines.append(f"ERROR: Nuke exited with code {process.returncode}")
                return PreviewResult(
                    success=False,
                    error=f"Nuke exited with code {process.returncode}",
                    output_lines=output_lines
                )
                
        except Exception as e:
            return PreviewResult(
                success=False,
                error=str(e),
                output_lines=output_lines
            )
    
    def generate_preview_with_overwrite(
        self,
        input_path: str,
        shot_dir: str,
        shot_name: str,
        project: str = "",
        artist: str = PreviewConfig.DEFAULT_ARTIST,
        colourspace: str = "sRGB",
        fps: float = 25,
        quality: str = PreviewConfig.DEFAULT_QUALITY,
        version: Optional[int] = None,
        plate_name: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PreviewResult:
        """
        Generate a preview video, overwriting if file exists.
        
        Same as generate_preview but deletes existing file first if needed.
        """
        # Build output path to check if it exists
        output_path, actual_version, file_exists = build_preview_output_path(
            shot_dir,
            shot_name,
            version=version,
            plate_name=plate_name,
        )
        
        # Delete existing file if it exists
        if file_exists:
            existing_paths = list_existing_preview_paths(
                shot_dir,
                shot_name,
                actual_version,
                plate_name=plate_name,
            )
            try:
                for existing_path in existing_paths:
                    if existing_path.exists():
                        existing_path.unlink()
            except Exception as e:
                return PreviewResult(
                    success=False,
                    error=f"Failed to delete existing preview file: {e}"
                )
        
        # Now generate (will not hit FILE_EXISTS since we deleted it)
        return self.generate_preview(
            input_path=input_path,
            shot_dir=shot_dir,
            shot_name=shot_name,
            project=project,
            artist=artist,
            colourspace=colourspace,
            fps=fps,
            quality=quality,
            version=version,
            plate_name=plate_name,
            on_output=on_output,
            check_cancelled=check_cancelled,
        )

    def generate_precomp_exr(
        self,
        input_path: str,
        shot_dir: str,
        shot_name: str,
        colourspace: str = "sRGB",
        fps: float = 25,
        version: int = 1,
        plate_name: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PrecompResult:
        """
        Generate a precomp EXR sequence from the original clip.

        Output is placed in renders/precomp/{shot_name}[_plate_01]_v{version:02d}.
        """
        if not self.nuke_available:
            return PrecompResult(
                success=False,
                error="Nuke executable not found. Please ensure Nuke is installed."
            )

        valid, error = self.validate_input(input_path)
        if not valid:
            return PrecompResult(success=False, error=error)

        if not self.headless_script.exists():
            return PrecompResult(
                success=False,
                error=f"Headless script not found: {self.headless_script}"
            )

        sequence_path, output_dir, file_exists = build_precomp_exr_output_path(
            shot_dir=shot_dir,
            shot_name=shot_name,
            version=version,
            plate_name=plate_name,
        )

        if file_exists:
            return PrecompResult(
                success=False,
                sequence_path=sequence_path,
                output_dir=output_dir,
                version=version,
                error="FILE_EXISTS",
                relative_path=str(sequence_path.relative_to(Path(shot_dir))).replace("\\", "/"),
            )

        cmd = build_precomp_exr_command(
            nuke_exe=self.nuke_exe,
            input_path=input_path,
            output_path=str(sequence_path),
            shot_name=shot_name,
            colourspace=colourspace,
            fps=fps,
            headless_script=str(self.headless_script),
        )

        cmd_display = " ".join(f'"{c}"' if " " in c else c for c in cmd)
        output_lines = [
            f"NUKE_EXE: {self.nuke_exe}",
            f"HEADLESS_SCRIPT: {self.headless_script}",
            f"CMD: {cmd_display}",
        ]
        if on_output:
            for line in output_lines:
                on_output(line)

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            while True:
                if check_cancelled and check_cancelled():
                    process.terminate()
                    return PrecompResult(
                        success=False,
                        error="Operation cancelled by user",
                        output_lines=output_lines,
                    )

                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break

                if line:
                    line = line.strip()
                    output_lines.append(line)
                    if on_output:
                        on_output(line)

            process.wait()

            if process.returncode == 0:
                relative_path = str(sequence_path.relative_to(Path(shot_dir))).replace("\\", "/")
                return PrecompResult(
                    success=True,
                    sequence_path=sequence_path,
                    output_dir=output_dir,
                    version=version,
                    relative_path=relative_path,
                    output_lines=output_lines,
                )
            output_lines.append(f"ERROR: Nuke exited with code {process.returncode}")
            return PrecompResult(
                success=False,
                error=f"Nuke exited with code {process.returncode}",
                output_lines=output_lines,
            )

        except Exception as e:
            return PrecompResult(
                success=False,
                error=str(e),
                output_lines=output_lines,
            )

    def generate_precomp_exr_with_overwrite(
        self,
        input_path: str,
        shot_dir: str,
        shot_name: str,
        colourspace: str = "sRGB",
        fps: float = 25,
        version: int = 1,
        plate_name: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PrecompResult:
        """Generate a precomp EXR sequence, overwriting existing files if needed."""
        sequence_path, output_dir, file_exists = build_precomp_exr_output_path(
            shot_dir=shot_dir,
            shot_name=shot_name,
            version=version,
            plate_name=plate_name,
        )

        if file_exists and output_dir.exists():
            try:
                for exr_file in output_dir.glob("*.exr"):
                    exr_file.unlink()
            except Exception as e:
                return PrecompResult(
                    success=False,
                    error=f"Failed to delete existing EXRs: {e}",
                )

        return self.generate_precomp_exr(
            input_path=input_path,
            shot_dir=shot_dir,
            shot_name=shot_name,
            colourspace=colourspace,
            fps=fps,
            version=version,
            plate_name=plate_name,
            on_output=on_output,
            check_cancelled=check_cancelled,
        )


# =============================================================================
# Nuke Render Task (runs inside Nuke -t)
# =============================================================================

class MakePreviewTask:
    """Render a preview by patching preview_template_v001.nk inside Nuke."""
    
    def __init__(self):
        self.nuke = None
    
    def setup_nuke(self):
        """Import nuke module - only works when run via Nuke -t"""
        if self.nuke is not None:
            return self.nuke
        try:
            self.nuke = importlib.import_module("nuke")
            return self.nuke
        except ImportError:
            raise RuntimeError("Must be run via: Nuke -t nuke_headless_tasks.py ...")
    
    def run(self, input_path, output_path, shot_name="", project="", artist="", 
            colourspace="sRGB", fps=25, quality="medium"):
        """Open the preview template, patch runtime knobs, and render the write node."""
        self.setup_nuke()
        nuke = self.nuke

        input_path = _normalize_nuke_path(input_path)
        output_path = _normalize_nuke_path(output_path)
        template_path = get_preview_template_path()

        output_dir = os.path.dirname(output_path)
        print(f"Input path: {input_path}")
        print(f"Output path: {output_path}")
        print(f"Input exists: {preview_input_exists(input_path)}")
        print(f"Output dir: {output_dir}")
        print(f"Output dir exists: {os.path.isdir(output_dir)}")
        print(f"Preview template: {template_path}")
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            print(f"ERROR: Could not create output dir: {e}")
        if not template_path.exists():
            raise RuntimeError(f"Preview template not found: {template_path}")
        if not preview_input_exists(input_path):
            raise RuntimeError(f"Input file not found: {input_path}")

        import time
        start_time = time.time()
        editor = _PreviewTemplateEditor(nuke)
        first, last = editor.render(
            template_path=str(template_path),
            input_path=input_path,
            output_path=output_path,
            shot_name=shot_name,
            project=project,
            artist=artist or PreviewConfig.DEFAULT_ARTIST,
            colourspace=colourspace,
            fps=fps,
            quality=quality,
        )
        elapsed = time.time() - start_time
        print(f"Rendered frames: {first}-{last}")
        print(f"Render complete: {elapsed:.1f}s")
        print(f"Output: {output_path}")
        return True


class MakePrecompExrTask:
    """Render original clip to an ACEScg EXR sequence. Runs inside Nuke."""

    def __init__(self):
        self.nuke = None

    def setup_nuke(self):
        if self.nuke is not None:
            return self.nuke
        try:
            self.nuke = importlib.import_module("nuke")
            return self.nuke
        except ImportError:
            raise RuntimeError("Must be run via: Nuke -t nuke_headless_tasks.py ...")

    def run(self, input_path, output_path, shot_name="", colourspace="sRGB", fps=25):
        self.setup_nuke()
        nuke = self.nuke

        if platform.system() == "Windows":
            input_path = str(input_path).replace("\\", "/")
            output_path = str(output_path).replace("\\", "/")

        output_dir = os.path.dirname(output_path)
        print(f"Input path: {input_path}")
        print(f"Output path: {output_path}")
        print(f"Input exists: {os.path.exists(input_path)}")
        print(f"Output dir: {output_dir}")
        print(f"Output dir exists: {os.path.isdir(output_dir)}")
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            print(f"ERROR: Could not create output dir: {e}")

        nuke.scriptClear()

        print(f"Input: {input_path}")
        print(f"Output: {output_path}")

        root = nuke.root()
        root['colorManagement'].setValue('OCIO')
        root['OCIO_config'].setValue(PreviewConfig.OCIO_CONFIG_NAME)
        root['workingSpaceLUT'].setValue('ACES - ACEScg')
        print(f"Color management: OCIO with {PreviewConfig.OCIO_CONFIG_NAME}")

        read = nuke.createNode('Read', inpanel=False)
        read['file'].fromUserText(input_path)

        try:
            read['colorspace'].setValue(map_input_colorspace(colourspace))
            print(f"Input colorspace: {map_input_colorspace(colourspace)}")
        except Exception:
            print("Using default input colorspace")

        first = int(read['first'].value())
        last = int(read['last'].value())
        print(f"File frames: {first}-{last}")

        root['first_frame'].setValue(first)
        root['last_frame'].setValue(last)
        root['fps'].setValue(fps)

        write = nuke.createNode('Write', inpanel=False)
        write.setInput(0, read)
        write['file'].setValue(output_path)
        write['file_type'].setValue('exr')
        try:
            write['channels'].setValue('rgba')
        except Exception:
            pass
        try:
            write['compression'].setValue('zip')
        except Exception:
            pass
        try:
            write['colorspace'].setValue('ACES - ACEScg')
            print("Output colorspace: ACES - ACEScg")
        except Exception:
            pass

        print(f"Rendering {last - first + 1} frames...")
        import time
        start_time = time.time()
        nuke.execute(write, first, last)
        elapsed = time.time() - start_time
        print(f"Render complete: {elapsed:.1f}s")
        print(f"Output: {output_path}")
        return True


# =============================================================================
# Debug UI
# =============================================================================

class PreviewDebugUI:
    """PyQt6 debug UI for testing make_preview."""
    
    def __init__(self):
        self.app = None
        self.window = None
    
    def run(self):
        """Launch the debug UI."""
        try:
            from PyQt6.QtWidgets import (
                QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                QPushButton, QLineEdit, QComboBox, QFileDialog, QGroupBox,
                QSpinBox, QDoubleSpinBox, QTextEdit, QMessageBox
            )
            from PyQt6.QtCore import Qt
        except ImportError:
            print("ERROR: PyQt6 not available. Install with: pip install PyQt6")
            return
        
        class MainWindow(QWidget):
            def __init__(ui_self):
                super().__init__()
                ui_self.setWindowTitle("Nuke Headless - Make Preview Debug UI")
                ui_self.setMinimumWidth(600)
                ui_self.setup_ui()
            
            def setup_ui(ui_self):
                layout = QVBoxLayout(ui_self)
                
                # --- Nuke Path ---
                nuke_group = QGroupBox("Nuke Executable")
                nuke_layout = QHBoxLayout(nuke_group)
                
                ui_self.nuke_path = QLineEdit()
                ui_self.nuke_path.setPlaceholderText("Path to Nuke executable...")
                
                # Try to find Nuke using our utility
                found_nuke = find_nuke_executable()
                if found_nuke:
                    ui_self.nuke_path.setText(found_nuke)
                
                btn_browse_nuke = QPushButton("Browse...")
                btn_browse_nuke.clicked.connect(ui_self.browse_nuke)
                
                nuke_layout.addWidget(ui_self.nuke_path)
                nuke_layout.addWidget(btn_browse_nuke)
                layout.addWidget(nuke_group)
                
                # --- Input/Output ---
                io_group = QGroupBox("Input / Output")
                io_layout = QVBoxLayout(io_group)
                
                # Input
                input_row = QHBoxLayout()
                input_row.addWidget(QLabel("Input:"))
                ui_self.input_path = QLineEdit()
                ui_self.input_path.setPlaceholderText("Select source footage...")
                btn_browse_input = QPushButton("Browse...")
                btn_browse_input.clicked.connect(ui_self.browse_input)
                input_row.addWidget(ui_self.input_path)
                input_row.addWidget(btn_browse_input)
                io_layout.addLayout(input_row)
                
                # Output
                output_row = QHBoxLayout()
                output_row.addWidget(QLabel("Output:"))
                ui_self.output_path = QLineEdit()
                ui_self.output_path.setPlaceholderText("Select output location...")
                btn_browse_output = QPushButton("Browse...")
                btn_browse_output.clicked.connect(ui_self.browse_output)
                output_row.addWidget(ui_self.output_path)
                output_row.addWidget(btn_browse_output)
                io_layout.addLayout(output_row)
                
                layout.addWidget(io_group)
                
                # --- Shot Info ---
                info_group = QGroupBox("Shot Information")
                info_layout = QVBoxLayout(info_group)
                
                row1 = QHBoxLayout()
                row1.addWidget(QLabel("Shot:"))
                ui_self.shot_name = QLineEdit("sho010")
                row1.addWidget(ui_self.shot_name)
                row1.addWidget(QLabel("Project:"))
                ui_self.project = QLineEdit("MyProject")
                row1.addWidget(ui_self.project)
                info_layout.addLayout(row1)
                
                row2 = QHBoxLayout()
                row2.addWidget(QLabel("Artist:"))
                ui_self.artist = QLineEdit("Artist")
                row2.addWidget(ui_self.artist)
                info_layout.addLayout(row2)
                
                row3 = QHBoxLayout()
                row3.addWidget(QLabel("Colourspace:"))
                ui_self.colourspace = QComboBox()
                ui_self.colourspace.setEditable(True)
                ui_self.colourspace.addItems([
                    "sRGB", "Rec.709", "ACES - ACEScg",
                    "Linear", "AlexaV3LogC", "Sony S-Log3",
                ])
                row3.addWidget(ui_self.colourspace)
                row3.addWidget(QLabel("Quality:"))
                ui_self.quality = QComboBox()
                ui_self.quality.addItems(["low", "medium", "high"])
                ui_self.quality.setCurrentText("medium")
                row3.addWidget(ui_self.quality)
                info_layout.addLayout(row3)
                
                row4 = QHBoxLayout()
                row4.addWidget(QLabel("FPS:"))
                ui_self.fps = QDoubleSpinBox()
                ui_self.fps.setRange(1, 120)
                ui_self.fps.setValue(25.0)
                row4.addWidget(ui_self.fps)
                row4.addStretch()
                info_layout.addLayout(row4)
                
                layout.addWidget(info_group)
                
                # --- Command Preview ---
                cmd_group = QGroupBox("Command Preview")
                cmd_layout = QVBoxLayout(cmd_group)
                
                ui_self.cmd_preview = QTextEdit()
                ui_self.cmd_preview.setReadOnly(True)
                ui_self.cmd_preview.setMaximumHeight(80)
                ui_self.cmd_preview.setStyleSheet("font-family: monospace; font-size: 10px;")
                cmd_layout.addWidget(ui_self.cmd_preview)
                
                layout.addWidget(cmd_group)
                
                # --- Buttons ---
                btn_layout = QHBoxLayout()
                
                btn_refresh = QPushButton("Refresh Command")
                btn_refresh.clicked.connect(ui_self.update_command)
                btn_layout.addWidget(btn_refresh)
                
                btn_copy = QPushButton("Copy Command")
                btn_copy.clicked.connect(ui_self.copy_command)
                btn_layout.addWidget(btn_copy)
                
                btn_run = QPushButton("🚀 Run Make Preview")
                btn_run.setStyleSheet("font-weight: bold; padding: 8px; background-color: #4a7ba7;")
                btn_run.clicked.connect(ui_self.run_preview)
                btn_layout.addWidget(btn_run)
                
                layout.addLayout(btn_layout)
                
                # --- Output Log ---
                log_group = QGroupBox("Output Log")
                log_layout = QVBoxLayout(log_group)
                
                ui_self.output_log = QTextEdit()
                ui_self.output_log.setReadOnly(True)
                ui_self.output_log.setStyleSheet("font-family: monospace; font-size: 10px;")
                log_layout.addWidget(ui_self.output_log)
                
                layout.addWidget(log_group)
                
                ui_self.update_command()
            
            def browse_nuke(ui_self):
                path, _ = QFileDialog.getOpenFileName(ui_self, "Select Nuke", "", "All Files (*)")
                if path:
                    ui_self.nuke_path.setText(path)
            
            def browse_input(ui_self):
                path, _ = QFileDialog.getOpenFileName(
                    ui_self, "Select Input", "",
                    "Video (*.mov *.mp4 *.avi *.mxf);;Images (*.exr *.dpx *.tif *.png *.jpg);;All (*)"
                )
                if path:
                    ui_self.input_path.setText(path)
                    p = Path(path)
                    ui_self.output_path.setText(str(p.parent / f"{p.stem}_v001.mp4"))
                    ui_self.shot_name.setText(p.stem)
                    ui_self.update_command()
            
            def browse_output(ui_self):
                path, _ = QFileDialog.getSaveFileName(ui_self, "Save Output", "", "MP4 (*.mp4);;MOV (*.mov)")
                if path:
                    ui_self.output_path.setText(path)
                    ui_self.update_command()
            
            def build_command(ui_self):
                return build_preview_command(
                    nuke_exe=ui_self.nuke_path.text() or "Nuke",
                    input_path=ui_self.input_path.text(),
                    output_path=ui_self.output_path.text(),
                    shot_name=ui_self.shot_name.text(),
                    project=ui_self.project.text(),
                    artist=ui_self.artist.text(),
                    colourspace=ui_self.colourspace.currentText(),
                    fps=ui_self.fps.value(),
                    quality=ui_self.quality.currentText(),
                )
            
            def update_command(ui_self):
                cmd = ui_self.build_command()
                ui_self.cmd_preview.setText(" \\\n    ".join(cmd))
            
            def copy_command(ui_self):
                cmd = ui_self.build_command()
                QApplication.clipboard().setText(" ".join(f'"{c}"' if " " in c else c for c in cmd))
                ui_self.output_log.append("✓ Command copied to clipboard")
            
            def run_preview(ui_self):
                if not ui_self.input_path.text():
                    QMessageBox.warning(ui_self, "Error", "Select an input file.")
                    return
                if not ui_self.output_path.text():
                    QMessageBox.warning(ui_self, "Error", "Select an output location.")
                    return
                if not os.path.exists(ui_self.nuke_path.text()):
                    QMessageBox.warning(ui_self, "Error", f"Nuke not found:\n{ui_self.nuke_path.text()}")
                    return
                if not os.path.exists(ui_self.input_path.text()):
                    QMessageBox.warning(ui_self, "Error", f"Input not found:\n{ui_self.input_path.text()}")
                    return
                
                cmd = ui_self.build_command()
                ui_self.output_log.clear()
                ui_self.output_log.append("Running...\n" + "=" * 50)
                
                try:
                    process = subprocess.Popen(
                        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, bufsize=1
                    )
                    for line in process.stdout:
                        ui_self.output_log.append(line.rstrip())
                        QApplication.processEvents()
                    
                    process.wait()
                    ui_self.output_log.append("=" * 50)
                    if process.returncode == 0:
                        ui_self.output_log.append(f"✓ SUCCESS!\n{ui_self.output_path.text()}")
                    else:
                        ui_self.output_log.append(f"✗ FAILED (code {process.returncode})")
                except Exception as e:
                    ui_self.output_log.append(f"✗ ERROR: {e}")
        
        self.app = QApplication(sys.argv)
        self.window = MainWindow()
        self.window.show()
        self.app.exec()


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='Nuke Headless Tasks')
    subparsers = parser.add_subparsers(dest='command')
    
    # make_preview command
    preview = subparsers.add_parser('make_preview', help='Create preview with slate')
    preview.add_argument('--input', '-i', required=True, help='Input clip, EXR frame, or image sequence pattern')
    preview.add_argument('--output', '-o', required=True, help='Output MP4 path')
    preview.add_argument('--shot_name', '-s', default='', help='Shot name for slate')
    preview.add_argument('--project', '-p', default='', help='Project name for slate')
    preview.add_argument('--artist', '-a', default='', help='Artist name for slate')
    preview.add_argument('--colourspace', default='sRGB', help='Input colorspace')
    preview.add_argument('--fps', type=float, default=25, help='Output FPS')
    preview.add_argument('--quality', choices=['low', 'medium', 'high'], default='medium')

    # make_precomp_exr command
    precomp_exr = subparsers.add_parser('make_precomp_exr', help='Create precomp EXR sequence (ACEScg)')
    precomp_exr.add_argument('--input', '-i', required=True, help='Input video file')
    precomp_exr.add_argument('--output', '-o', required=True, help='Output EXR sequence path (####)')
    precomp_exr.add_argument('--shot_name', '-s', default='', help='Shot name')
    precomp_exr.add_argument('--colourspace', default='sRGB', help='Input colorspace')
    precomp_exr.add_argument('--fps', type=float, default=25, help='Output FPS')
    
    args = parser.parse_args()
    
    if args.command == 'make_preview':
        task = MakePreviewTask()
        success = task.run(
            input_path=args.input,
            output_path=args.output,
            shot_name=args.shot_name,
            project=args.project,
            artist=args.artist,
            colourspace=args.colourspace,
            fps=args.fps,
            quality=args.quality
        )
        sys.exit(0 if success else 1)
    elif args.command == 'make_precomp_exr':
        task = MakePrecompExrTask()
        success = task.run(
            input_path=args.input,
            output_path=args.output,
            shot_name=args.shot_name,
            colourspace=args.colourspace,
            fps=args.fps,
        )
        sys.exit(0 if success else 1)
    else:
        # No command given - launch debug UI
        ui = PreviewDebugUI()
        ui.run()


if __name__ == '__main__':
    main()
