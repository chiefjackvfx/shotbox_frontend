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
            "/opt/Nuke16.0v8/Nuke16.0",
            "/opt/Nuke15.2v2/Nuke15.2",
            "/opt/Nuke15.1v1/Nuke15.1",
            "/opt/Nuke15.0v2/Nuke15.0",
            "/usr/local/Nuke16.0v8/Nuke16.0",
            "/usr/local/Nuke15.2v2/Nuke15.2",
            "/usr/local/Nuke15.1v1/Nuke15.1",
            "/usr/local/Nuke15.0v2/Nuke15.0",
            "/usr/local/Nuke14.0v5/Nuke14.0",
        ],
        'Windows': [
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


def build_preview_output_path(
    shot_dir: str,
    shot_name: str,
    version: Optional[int] = None,
    preview_subdir: str = PreviewConfig.PREVIEW_SUBDIR
) -> Tuple[Path, int, bool]:
    """
    Build the output path for a preview video.
    
    Creates the output directory if it doesn't exist.
    Output format: {shot_name}_v{version}_preview.mp4
    
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
        output_path = previews_dir / f"{shot_name}_v{version:02d}_preview.mp4"
        file_exists = output_path.exists()
        return output_path, version, file_exists
    
    # Auto-increment: find next version number
    existing_previews = list(previews_dir.glob(f"{shot_name}_v*_preview.mp4"))
    
    if existing_previews:
        versions = []
        for p in existing_previews:
            try:
                # Extract version from {shot}_v{nn}_preview.mp4
                stem = p.stem  # e.g. "sho010_v03_preview"
                # Remove _preview suffix, then get version
                without_suffix = stem.replace('_preview', '')
                v = int(without_suffix.split('_v')[-1])
                versions.append(v)
            except (ValueError, IndexError):
                pass
        next_version = max(versions) + 1 if versions else 1
    else:
        next_version = 1
    
    output_path = previews_dir / f"{shot_name}_v{next_version:02d}_preview.mp4"
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
    match = re.search(r'_v(\d+)(?:[_.-]|$)', target)
    if match:
        return int(match.group(1))
    return None


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
    precomp_subdir: str = PreviewConfig.PRECOMP_SUBDIR,
) -> Tuple[Path, Path, bool]:
    """
    Build the output path for a precomp EXR sequence.

    Output folder: renders/precomp/{shot_name}_v{version:02d}
    Output file: {shot_name}_v{version:02d}_####.exr

    Returns:
        Tuple of (sequence_path, output_dir, file_exists)
    """
    precomp_dir = Path(shot_dir) / precomp_subdir / f"{shot_name}_v{version:02d}"
    precomp_dir.mkdir(parents=True, exist_ok=True)
    sequence_path = precomp_dir / f"{shot_name}_v{version:02d}_####.exr"
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
        if not os.path.exists(input_path):
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
        
        # Build output path
        output_path, actual_version, file_exists = build_preview_output_path(
            shot_dir, shot_name, version=version
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
        on_output: Optional[Callable[[str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PreviewResult:
        """
        Generate a preview video, overwriting if file exists.
        
        Same as generate_preview but deletes existing file first if needed.
        """
        # Build output path to check if it exists
        output_path, actual_version, file_exists = build_preview_output_path(
            shot_dir, shot_name, version=version
        )
        
        # Delete existing file if it exists
        if file_exists and output_path.exists():
            try:
                output_path.unlink()
            except Exception as e:
                return PreviewResult(
                    success=False,
                    error=f"Failed to delete existing file: {e}"
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
        on_output: Optional[Callable[[str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PrecompResult:
        """
        Generate a precomp EXR sequence from the original clip.

        Output is placed in renders/precomp/{shot_name}_v{version:02d}.
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
        on_output: Optional[Callable[[str], None]] = None,
        check_cancelled: Optional[Callable[[], bool]] = None,
    ) -> PrecompResult:
        """Generate a precomp EXR sequence, overwriting existing files if needed."""
        sequence_path, output_dir, file_exists = build_precomp_exr_output_path(
            shot_dir=shot_dir,
            shot_name=shot_name,
            version=version,
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
            on_output=on_output,
            check_cancelled=check_cancelled,
        )


# =============================================================================
# Nuke Render Task (runs inside Nuke -t)
# =============================================================================

class MakePreviewTask:
    """Transcode video with slate overlay. Runs inside Nuke."""
    
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
        """
        Transcode video with slate overlay.
        
        Simply reads the input video, adds text overlays, and writes to MP4.
        Frame range comes entirely from the source file.
        
        Note: The shot_name displayed on slate will include version extracted from output_path.
        e.g., output "sho010_v03_preview.mp4" -> slate shows "sho010_v03"
        """
        self.setup_nuke()
        nuke = self.nuke

        # Normalize paths for Nuke on Windows (prefer forward slashes)
        if platform.system() == "Windows":
            input_path = str(input_path).replace("\\", "/")
            output_path = str(output_path).replace("\\", "/")

        # Ensure output directory exists (and print diagnostics)
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
        
        # Clear any existing script
        nuke.scriptClear()
        
        print(f"Input: {input_path}")
        print(f"Output: {output_path}")
        
        # Extract version from output path for slate display
        # Output format: {shot_name}_v{version}_preview.mp4
        output_stem = Path(output_path).stem  # e.g. "sho010_v03_preview"
        if '_preview' in output_stem:
            # Remove _preview suffix to get "sho010_v03"
            slate_shot_name = output_stem.replace('_preview', '')
        else:
            # Fallback to just shot_name if format doesn't match
            slate_shot_name = shot_name
        
        print(f"Slate shot name: {slate_shot_name}")
        
        # Setup OCIO/ACES color management
        root = nuke.root()
        root['colorManagement'].setValue('OCIO')
        root['OCIO_config'].setValue('aces_1.2')
        root['workingSpaceLUT'].setValue('ACES - ACEScg')
        print("Color management: OCIO with ACES 1.2")
        
        # Create Read node with file path
        read = nuke.createNode('Read', inpanel=False)
        read['file'].fromUserText(input_path)
        
        # Get frame range FROM THE FILE - this is the only source of truth for rendering
        first = int(read['first'].value())
        last = int(read['last'].value())
        duration = last - first + 1
        
        # VFX display frame range (for slate only) - starts at 1001
        vfx_first = 1001
        vfx_last = 1001 + duration - 1
        
        print(f"File frames: {first}-{last} ({duration} frames)")
        print(f"VFX frames (display): {vfx_first}-{vfx_last}")
        
        # Set input colorspace
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
        ocio_colorspace = colorspace_map.get(colourspace, 'Utility - sRGB - Texture')
        try:
            read['colorspace'].setValue(ocio_colorspace)
            print(f"Input colorspace: {ocio_colorspace}")
        except:
            print("Using default input colorspace")
        
        # Set project frame range and fps
        root['first_frame'].setValue(first)
        root['last_frame'].setValue(last)
        root['fps'].setValue(fps)
        
        # Get input dimensions
        input_format = read.format()
        width = input_format.width()
        height = input_format.height()
        
        # Reformat to max 1920 wide (fit within, maintain aspect)
        reformat = nuke.createNode('Reformat', inpanel=False)
        reformat.setInput(0, read)
        reformat['type'].setValue('to box')
        reformat['box_width'].setValue(1920)
        reformat['box_height'].setValue(1080)
        reformat['resize'].setValue('fit')
        reformat['black_outside'].setValue(True)
        
        # Get reformatted dimensions for slate positioning
        slate_height = 50
        
        # Add black bars for slate (top and bottom)
        reformatted_format = reformat.format()
        rw = reformatted_format.width()
        rh = reformatted_format.height()
        new_height = rh + (slate_height * 2)
        
        format_name = f"preview_{rw}x{new_height}"
        nuke.addFormat(f"{rw} {new_height} {format_name}")
        
        add_bars = nuke.createNode('Reformat', inpanel=False)
        add_bars.setInput(0, reformat)
        add_bars['type'].setValue('to box')
        add_bars['box_width'].setValue(rw)
        add_bars['box_height'].setValue(new_height)
        add_bars['box_fixed'].setValue(True)
        add_bars['resize'].setValue('none')
        add_bars['black_outside'].setValue(True)
        
        last_node = add_bars
        
        # --- TEXT OVERLAYS ---
        date_str = datetime.now().strftime("%d/%m/%y %H:%M")
        
        # Top left: Project
        if project:
            txt = nuke.createNode('Text2', inpanel=False)
            txt.setInput(0, last_node)
            txt['message'].setValue(project)
            txt['box'].setValue([20, rh + slate_height, rw/3, new_height - 10])
            txt['yjustify'].setValue('center')
            txt['global_font_scale'].setValue(0.35)
            last_node = txt
        
        # Top center: Shot name (with version)
        if slate_shot_name:
            txt = nuke.createNode('Text2', inpanel=False)
            txt.setInput(0, last_node)
            txt['message'].setValue(slate_shot_name)
            txt['box'].setValue([rw/3, rh + slate_height, rw*2/3, new_height - 10])
            txt['xjustify'].setValue('center')
            txt['yjustify'].setValue('center')
            txt['global_font_scale'].setValue(0.4)
            last_node = txt
        
        # Top right: Date/time
        txt = nuke.createNode('Text2', inpanel=False)
        txt.setInput(0, last_node)
        txt['message'].setValue(date_str)
        txt['box'].setValue([rw*2/3, rh + slate_height, rw - 20, new_height - 10])
        txt['xjustify'].setValue('right')
        txt['yjustify'].setValue('center')
        txt['global_font_scale'].setValue(0.3)
        last_node = txt
        
        # Bottom left: Current frame (VFX frame = 1001 + file_frame - file_first)
        txt = nuke.createNode('Text2', inpanel=False)
        txt.setInput(0, last_node)
        txt['message'].setValue(f"[expr {vfx_first} + [frame] - {first}]")
        txt['box'].setValue([20, 10, 120, slate_height])
        txt['yjustify'].setValue('center')
        txt['global_font_scale'].setValue(0.35)
        last_node = txt
        
        # Bottom left-center: Frame range (VFX range)
        txt = nuke.createNode('Text2', inpanel=False)
        txt.setInput(0, last_node)
        txt['message'].setValue(f"{vfx_first}-{vfx_last}")
        txt['box'].setValue([120, 10, 280, slate_height])
        txt['yjustify'].setValue('center')
        txt['global_font_scale'].setValue(0.3)
        last_node = txt
        
        # Bottom center: FPS
        txt = nuke.createNode('Text2', inpanel=False)
        txt.setInput(0, last_node)
        txt['message'].setValue(f"{int(fps)}fps")
        txt['box'].setValue([280, 10, 380, slate_height])
        txt['yjustify'].setValue('center')
        txt['global_font_scale'].setValue(0.3)
        last_node = txt
        
        # Bottom right: Artist
        if artist:
            txt = nuke.createNode('Text2', inpanel=False)
            txt.setInput(0, last_node)
            txt['message'].setValue(artist)
            txt['box'].setValue([rw - 250, 10, rw - 20, slate_height])
            txt['xjustify'].setValue('right')
            txt['yjustify'].setValue('center')
            txt['global_font_scale'].setValue(0.3)
            last_node = txt
        
        # --- WRITE NODE ---
        write = nuke.createNode('Write', inpanel=False)
        write.setInput(0, last_node)
        write['file'].setValue(output_path)
        write['file_type'].setValue('mov')
        write['mov64_codec'].setValue('h264')
        
        # Output colorspace: Rec.709 for preview
        try:
            write['colorspace'].setValue('Output - Rec.709')
            print("Output colorspace: Rec.709")
        except:
            pass
        
        # Quality
        quality_map = {'low': 'Low', 'medium': 'Medium', 'high': 'High'}
        write['mov64_quality'].setValue(quality_map.get(quality, 'Medium'))
        
        # Render
        print(f"Rendering {duration} frames...")
        import time
        start_time = time.time()
        
        nuke.execute(write, first, last)
        
        elapsed = time.time() - start_time
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
        root['OCIO_config'].setValue('aces_1.2')
        root['workingSpaceLUT'].setValue('ACES - ACEScg')
        print("Color management: OCIO with ACES 1.2")

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
                    ui_self.output_path.setText(str(p.parent / f"{p.stem}_preview.mp4"))
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
    preview.add_argument('--input', '-i', required=True, help='Input video file')
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
