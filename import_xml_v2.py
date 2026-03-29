# import_xml_v2.py
"""
XML Import V2 for ShotBox - Combined XML parsing, Nuke setup, and Django import.
Single-step process: Parse XML → Create Nuke folders/scripts → Add to Django DB.
"""

# =============================================================================
# IMPORTS
# =============================================================================

import os
import sys
import re
import datetime
import shutil
import platform
import subprocess
import getpass
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path

from PyQt6 import uic
from PyQt6 import QtWidgets, QtGui
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QToolButton, QLabel,
    QLineEdit, QComboBox, QCheckBox, QSpinBox, QMenu, QHeaderView,
    QFileDialog, QMessageBox, QDialog, QProgressBar, QSpacerItem, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QPixmap

import create_nk

# Optional imports
try:
    import cv2
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    print("[XMLImport] opencv-python not installed. Thumbnail generation disabled.")

try:
    import http_help
    HAS_API = True
except ImportError:
    HAS_API = False
    print("[XMLImport] http_help not available. Django import disabled.")


# =============================================================================
# CONSTANTS
# =============================================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

COLOURSPACE_LIST = [
    "Input - ARRI - V3 LogC (EI800) - Wide Gamut",
    "Input - ARRI - V4 LogC (EI800) - Wide Gamut4",
    "Input - Sony - Linear - Venice S-Gamut3.Cine",
    "Input - Sony - S-Log3 - Venice S-Gamut3.Cine",
    "Input - Canon - Curve - Canon-Log3",
    "Input - RED - REDLog3G10 - REDWideGamutRGB",
    "color_picking",
    "Output - Rec.709",
    "ACES - ACEScg"
]

DEFAULT_TASKS = ["Comp", "Roto", "Paint", "Track"]
SHOT_TITLE_MAX_LENGTH = 50
IMPORT_VERSION_PADDING = 3
INITIAL_IMPORT_VERSION = 1
INITIAL_IMPORT_VERSION_TAG = f"v{INITIAL_IMPORT_VERSION:0{IMPORT_VERSION_PADDING}d}"


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ParsedClip:
    """Represents a single clip parsed from XML."""
    name: str
    filepath: str
    in_point: int
    out_point: int
    duration: int
    start_frame: int
    end_frame: int
    track: int  # V1=1, V2=2, etc.
    copied_filepath: Optional[str] = None


@dataclass
class ParsedShot:
    """Represents a shot with all its linked clips from multiple tracks."""
    index: int
    name: str  # e.g., "sho010"
    clips: List[ParsedClip] = field(default_factory=list)
    duration: int = 0
    edit_inpoint: int = 0
    edit_outpoint: int = 0
    thumbnail_path: Optional[str] = None
    folder_path: Optional[str] = None
    nuke_script_path: Optional[str] = None
    db_shot_id: Optional[int] = None
    
    @property
    def primary_clip(self) -> Optional[ParsedClip]:
        """Get the V1 (primary) clip."""
        for clip in self.clips:
            if clip.track == 1:
                return clip
        return self.clips[0] if self.clips else None
    
    @property
    def original_clip_name(self) -> str:
        """Get the primary copied plate path, falling back to the source path."""
        clip = self.primary_clip
        if clip:
            if clip.copied_filepath:
                return clip.copied_filepath
            if clip.filepath != "offline":
                return clip.filepath
        return "offline"

    @property
    def original_clip_paths(self) -> List[str]:
        """Get all copied plate paths, falling back to the source paths."""
        copied_paths = [clip.copied_filepath for clip in self.clips if clip.copied_filepath]
        if copied_paths:
            return copied_paths
        return [clip.filepath for clip in self.clips if clip.filepath != "offline"]


@dataclass 
class ImportStats:
    """Statistics for the import process."""
    shots_processed: int = 0
    folders_created: int = 0
    folders_skipped: int = 0
    nuke_scripts_created: int = 0
    db_shots_created: int = 0
    db_shots_existing: int = 0
    thumbnails_uploaded: int = 0
    errors: List[str] = field(default_factory=list)


# =============================================================================
# XML PARSER
# =============================================================================

class XMLTimelineParser:
    """Parses XML timeline files from editing software (Premiere, Resolve, etc.)."""
    
    def __init__(self):
        self.clips_by_track: Dict[int, List[ParsedClip]] = {}
        self.project_root: str = ""
        self.edit_name: str = ""
        
    def parse(self, xml_path: str, handles: int = 25) -> List[ParsedShot]:
        """
        Parse an XML timeline file and return a list of shots.
        
        Args:
            xml_path: Path to the XML file
            handles: Number of handle frames to add
            
        Returns:
            List of ParsedShot objects with linked clips
        """
        self.clips_by_track = {i: [] for i in range(1, 6)}  # V1-V5
        
        # Set project root and edit name from path
        self._set_paths_from_xml(xml_path)
        
        # Parse XML
        tree = ET.parse(xml_path)
        root = tree.getroot()
        
        track_num = 0
        
        for sequence in root.findall("sequence"):
            for media in sequence.findall("media"):
                for video in media.findall("video"):
                    for video_track in video.findall("track"):
                        track_num += 1
                        if track_num > 5:
                            break
                            
                        for clip_elem in video_track.findall("clipitem"):
                            clip = self._parse_clip(clip_elem, track_num, handles)
                            if clip:
                                self.clips_by_track[track_num].append(clip)
        
        # Link clips into shots based on V1 as primary
        shots = self._link_clips_to_shots(handles)
        
        return shots
    
    def _set_paths_from_xml(self, xml_path: str):
        """Set edit name from XML path. Project root will be derived from clips."""
        self.edit_name = os.path.basename(xml_path).replace(" ", "_")[:-4]
        # Project root will be set later from clip paths
        self.project_root = ""
    
    def _derive_project_root_from_clips(self) -> str:
        """
        Derive the project root from the first online clip path.
        Looks for common project folder patterns like 'Nuke', 'VFX', 'footage', etc.
        Returns the best guess for project root, or empty string if can't determine.
        """
        # Find first online clip
        first_online_path = None
        for track_num in range(1, 6):
            for clip in self.clips_by_track.get(track_num, []):
                if clip.filepath and clip.filepath != "offline" and os.path.exists(clip.filepath):
                    first_online_path = clip.filepath
                    break
            if first_online_path:
                break
        
        if not first_online_path:
            # No online clips found, try to use any path even if not accessible
            for track_num in range(1, 6):
                for clip in self.clips_by_track.get(track_num, []):
                    if clip.filepath and clip.filepath != "offline":
                        first_online_path = clip.filepath
                        break
                if first_online_path:
                    break
        
        if not first_online_path:
            return ""
        
        # Normalize path
        clip_path = first_online_path.replace("\\", "/")
        
        # Look for common project structure markers
        # Walk up the path looking for a folder that looks like a project root
        path_parts = clip_path.split("/")
        
        # Common folder names that indicate we're inside a project
        inside_project_markers = ['footage', 'scans', 'plates', 'source', 'media', 
                                   'raw', 'camera', 'dailies', 'editorial']
        # Common folder names that might be the project root level
        project_level_markers = ['nuke', 'vfx', 'comp', 'cg', '3d', 'assets']
        
        best_root = ""
        
        # Try to find a sensible project root by walking up the path
        for i in range(len(path_parts) - 1, 0, -1):
            folder_name = path_parts[i].lower()
            
            # If we hit a marker that suggests we're inside project content,
            # the parent is likely the project root
            if folder_name in inside_project_markers:
                best_root = "/".join(path_parts[:i])
                break
            
            # If we find a project-level marker, include it in the root
            if folder_name in project_level_markers:
                best_root = "/".join(path_parts[:i])
                break
        
        # If no markers found, use a reasonable default:
        # Go up 3-4 levels from the file (past typical footage/subfolder structure)
        if not best_root:
            depth = min(4, len(path_parts) - 1)
            if len(path_parts) > depth:
                best_root = "/".join(path_parts[:-depth])
        
        return best_root
    
    def get_all_online_clip_paths(self) -> List[str]:
        """Return list of all online clip paths for user review."""
        paths = []
        for track_num in range(1, 6):
            for clip in self.clips_by_track.get(track_num, []):
                if clip.filepath and clip.filepath != "offline":
                    paths.append(clip.filepath)
        return paths
    
    def _parse_clip(self, clip_elem: ET.Element, track: int, handles: int) -> Optional[ParsedClip]:
        """Parse a single clip element from XML."""
        try:
            name = clip_elem.find("name").text or "unnamed"
            
            # Get file path
            filepath = "offline"
            for file_elem in clip_elem.findall("file"):
                pathurl = file_elem.find("pathurl")
                if pathurl is not None and pathurl.text:
                    filepath = self._normalize_filepath(pathurl.text)
            
            # Get timing info
            in_point = int(clip_elem.find("in").text or 0)
            if in_point == 90000:  # Special case in some XMLs
                in_point = 0
                
            out_point = int(clip_elem.find("out").text or 0)
            duration = int(clip_elem.find("duration").text or 0)
            start_frame = int(clip_elem.find("start").text or 0)
            end_frame = int(clip_elem.find("end").text or 0)
            
            # Calculate true duration with handles
            true_duration = (end_frame - start_frame) + (handles * 2)
            
            return ParsedClip(
                name=name,
                filepath=filepath,
                in_point=in_point,
                out_point=out_point,
                duration=true_duration,
                start_frame=start_frame,
                end_frame=end_frame,
                track=track
            )
        except Exception as e:
            print(f"[XMLParser] Error parsing clip: {e}")
            return None
    
    def _normalize_filepath(self, pathurl: str) -> str:
        """Normalize file path from XML URL format."""
        filepath = urllib.parse.unquote(pathurl)
        
        if filepath.startswith("file://localhost"):
            filepath = filepath[17:]
        elif filepath.startswith("file:///Volumes/projects/PROJECTS"):
            filepath = "Z:/PROJECTS" + filepath.split("file:///Volumes/projects/PROJECTS")[1]
        elif filepath.startswith("file://"):
            filepath = filepath[7:]
        
        # Handle Linux path conversion
        if platform.system() == "Linux" and filepath.startswith("Z:/PROJECTS"):
            filepath = "/Volumes/projects/PROJECTS" + filepath.split("Z:/PROJECTS")[1]
        
        return filepath
    
    def _link_clips_to_shots(self, handles: int) -> List[ParsedShot]:
        """Link clips from V2-V5 to V1 clips based on matching start frames."""
        shots = []
        v1_clips = self.clips_by_track.get(1, [])
        
        for idx, v1_clip in enumerate(v1_clips):
            shot = ParsedShot(
                index=idx,
                name="",  # Will be set later with naming convention
                duration=v1_clip.duration,
                edit_inpoint=v1_clip.start_frame,
                edit_outpoint=v1_clip.end_frame
            )
            shot.clips.append(v1_clip)
            
            # Find matching clips from other tracks by start frame
            for track_num in range(2, 6):
                for clip in self.clips_by_track.get(track_num, []):
                    if clip.start_frame == v1_clip.start_frame:
                        shot.clips.append(clip)
                        break
            
            shots.append(shot)
        
        return shots


# =============================================================================
# NUKE SCRIPT GENERATOR
# =============================================================================

class NukeScriptGenerator:
    """Generates Nuke scripts and folder structures for shots."""
    
    def __init__(
        self,
        colourspace: str = COLOURSPACE_LIST[0],
        local_work: bool = False,
        nuke_path: Optional[str] = None,
    ):
        self.colourspace = colourspace
        self.local_work = local_work
        self.nuke_path = nuke_path
        self.user = self._get_username()
        
    def _get_username(self) -> str:
        """Get current username with mapping."""
        user = getpass.getuser()
        user_map = {"Huxley": "Dylan", "Clarke": "Jack", "rockybtw": "Jack", "grade": "Paul"}
        return user_map.get(user, user)
    
    def create_shot_folder(self, shot: ParsedShot, project_root: str, edit_name: str) -> str:
        """
        Create folder structure for a shot.
        
        Returns:
            Path to the created shot folder
        """
        nuke_root = os.path.join(project_root, "VFX")
        work_folder = os.path.join(nuke_root, edit_name) if edit_name else nuke_root
        shot_folder = os.path.join(work_folder, shot.name)
        
        os.makedirs(os.path.join(nuke_root, "assets"), exist_ok=True)
        os.makedirs(work_folder, exist_ok=True)

        shot_preexists = os.path.isdir(shot_folder)
        self._ensure_shot_subfolders(shot_folder)
        self._copy_clips_to_plates(shot, shot_folder)

        if shot_preexists:
            print(f"[NukeGen] Shot folder already exists: {shot_folder}")
        return shot_folder

    def _ensure_shot_subfolders(self, shot_folder: str):
        """Ensure the current shot folder contains the expected structure."""
        os.makedirs(os.path.join(shot_folder, "plates"), exist_ok=True)
        os.makedirs(os.path.join(shot_folder, "renders", "comp"), exist_ok=True)
        os.makedirs(os.path.join(shot_folder, "renders", "precomp"), exist_ok=True)
        os.makedirs(os.path.join(shot_folder, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(shot_folder, "assets"), exist_ok=True)

    def _copy_clips_to_plates(self, shot: ParsedShot, shot_folder: str):
        """Copy each online clip into the local plates folder."""
        plates_folder = os.path.join(shot_folder, "plates")
        os.makedirs(plates_folder, exist_ok=True)

        used_names = set()
        for clip in shot.clips:
            clip.copied_filepath = None

        for clip in shot.clips:
            if clip.filepath == "offline":
                continue
            if not os.path.isfile(clip.filepath):
                raise FileNotFoundError(f"Clip not found for copy: {clip.filepath}")

            dest_path = self._resolve_plate_destination(plates_folder, clip.filepath, used_names)
            if os.path.abspath(clip.filepath) != os.path.abspath(dest_path):
                shutil.copy2(clip.filepath, dest_path)
            clip.copied_filepath = dest_path

    def _resolve_plate_destination(self, plates_folder: str, source_path: str, used_names: set[str]) -> str:
        """Return a deterministic plate destination, avoiding basename collisions."""
        source_name = os.path.basename(source_path)
        stem, ext = os.path.splitext(source_name)
        candidate = source_name
        suffix = 2

        while candidate.lower() in used_names:
            candidate = f"{stem}_{suffix}{ext}"
            suffix += 1

        used_names.add(candidate.lower())
        return os.path.join(plates_folder, candidate)
    
    def create_nuke_script(self, shot: ParsedShot, shot_folder: str, project_root: str) -> str:
        """
        Create a Nuke script for the shot.
        
        Returns:
            Path to the created Nuke script
        """
        return self.create_nuke_script_with_project_name(shot, shot_folder, project_root, None)

    def create_nuke_script_with_project_name(
        self,
        shot: ParsedShot,
        shot_folder: str,
        project_root: str,
        project_name: Optional[str],
    ) -> str:
        """
        Create a Nuke script for the shot, optionally overriding the project name used in the slate.

        Returns:
            Path to the created Nuke script
        """
        scripts_folder = os.path.join(shot_folder, "scripts")
        script_path = os.path.join(scripts_folder, f"{shot.name}_{INITIAL_IMPORT_VERSION_TAG}.nk")
        
        if os.path.isfile(script_path):
            print(f"[NukeGen] Script already exists: {script_path}")
            return script_path

        if any(clip.filepath != "offline" and not clip.copied_filepath for clip in shot.clips):
            self._copy_clips_to_plates(shot, shot_folder)
        
        resolved_project_name = project_name
        if not resolved_project_name and project_root:
            resolved_project_name = os.path.basename(os.path.normpath(project_root))
        request = self._build_create_nk_request(shot, shot_folder, script_path, resolved_project_name or "")
        result = create_nk.generate_from_template(request, nuke_path=self.nuke_path)
        if not result.success:
            raise RuntimeError(result.error or "Unknown Nuke script creation error")
        return result.script_path

    def _build_create_nk_request(
        self,
        shot: ParsedShot,
        shot_folder: str,
        script_path: str,
        project_name: str,
    ) -> create_nk.GenerateNkRequest:
        """Build the resolved request passed to the template-based Nuke editor."""
        primary_clip = shot.primary_clip
        if not primary_clip:
            raise RuntimeError(f"Shot {shot.name} has no primary clip.")

        primary_clip_path = primary_clip.copied_filepath or primary_clip.filepath
        width, height = self._get_video_format(primary_clip_path)
        frame_first = 1001
        frame_last = frame_first + max(shot.duration, 1) - 1
        extra_plate_paths: List[str] = []

        for clip in sorted(shot.clips, key=lambda current: (current.track, current.name, current.filepath)):
            if clip.filepath == "offline":
                continue
            if not clip.copied_filepath:
                raise FileNotFoundError(f"Copied plate missing for clip: {clip.filepath}")
            if clip is primary_clip:
                continue
            extra_plate_paths.append(clip.copied_filepath)

        if not primary_clip_path or primary_clip_path == "offline":
            raise RuntimeError(f"Shot {shot.name} has no online plates to build a Nuke script from.")

        output_files = self._build_nuke_output_files(shot_folder, script_path, shot.name)

        return create_nk.GenerateNkRequest(
            template_path=str(create_nk.get_runtime_template_path()),
            script_path=script_path,
            shot_dir=shot_folder,
            shot_name=shot.name,
            project_name=project_name,
            artist_name=self.user,
            fps=25.0,
            colourspace=self.colourspace,
            duration=max(shot.duration, 1),
            edit_inpoint=shot.edit_inpoint,
            edit_outpoint=shot.edit_outpoint,
            frame_first=frame_first,
            frame_last=frame_last,
            format_width=width,
            format_height=height,
            primary_plate_path=primary_clip_path,
            extra_plate_paths=extra_plate_paths,
            dn_exr_file=output_files["dn_exr_file"],
            comp_mov_file=output_files["comp_mov_file"],
            comp_exr_file=output_files["comp_exr_file"],
            preview_mp4_file=output_files["preview_mp4_file"],
        )

    def _build_nuke_output_files(self, shot_folder: str, script_path: str, shot_name: str) -> Dict[str, str]:
        """Build output file knob values relative to the script folder."""
        version_tag = INITIAL_IMPORT_VERSION_TAG
        shot_dir = Path(shot_folder)
        return {
            "dn_exr_file": self._relative_to_script_path(
                script_path,
                shot_dir / "renders" / "precomp" / f"{shot_name}_DN_{version_tag}" / f"{shot_name}_DN_{version_tag}_####.exr",
            ),
            "comp_mov_file": self._relative_to_script_path(
                script_path,
                shot_dir / "renders" / "comp" / f"{shot_name}_{version_tag}.mov",
            ),
            "comp_exr_file": self._relative_to_script_path(
                script_path,
                shot_dir / "renders" / "comp" / f"{shot_name}_{version_tag}" / f"{shot_name}_{version_tag}_####.exr",
            ),
            "preview_mp4_file": self._relative_to_script_path(
                script_path,
                shot_dir / "renders" / "precomp" / "previews" / f"{shot_name}_{version_tag}.mp4",
            ),
        }

    def _relative_to_script_path(self, script_path: str, target_path: Path) -> str:
        """Return a normalized path relative to the script directory."""
        return str(os.path.relpath(target_path, Path(script_path).parent)).replace("\\", "/")

    def _get_video_format(self, filepath: str) -> tuple[int, int]:
        """Get video format/resolution."""
        if not HAS_CV2 or not os.path.isfile(filepath):
            return 1920, 1080
        try:
            cap = cv2.VideoCapture(filepath)
            if not cap.isOpened():
                return 1920, 1080
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
            cap.release()
            return width, height
        except Exception:
            return 1920, 1080


# =============================================================================
# THUMBNAIL GENERATOR
# =============================================================================

class ThumbnailGenerator:
    """Generates thumbnails from video clips."""
    
    @staticmethod
    def generate(video_path: str, output_path: str, frame: int = 1) -> Optional[str]:
        """
        Generate a thumbnail from a video file.
        
        Args:
            video_path: Path to the video file
            output_path: Where to save the thumbnail
            frame: Which frame to grab (default 1)
        
        Returns:
            Path to the thumbnail or None if failed
        """
        if not HAS_CV2:
            print("[Thumbnail] OpenCV not available")
            return None
        
        # Normalize path
        video_path = video_path.replace("\\", "/")
        
        if not os.path.isfile(video_path):
            print(f"[Thumbnail] Video not found: {video_path}")
            return None
        
        try:
            # Open video
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                print(f"[Thumbnail] Could not open video: {video_path}")
                return None
            
            # Get total frame count
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            
            # Use frame 1 or a frame near the start (like original)
            target_frame = min(frame, max(0, total_frames - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
            
            success, thumb = cap.read()
            cap.release()
            
            if not success or thumb is None:
                print(f"[Thumbnail] Failed to read frame {target_frame} from: {video_path}")
                return None
            
            # Resize to half (like original)
            original_height, original_width = thumb.shape[:2]
            new_height = original_height // 2
            new_width = original_width // 2
            
            if new_width > 0 and new_height > 0:
                thumb = cv2.resize(thumb, (new_width, new_height))
            
            # Ensure output directory exists
            output_dir = os.path.dirname(output_path)
            if output_dir and not os.path.isdir(output_dir):
                os.makedirs(output_dir, exist_ok=True)
            
            # Write with quality setting (like original)
            cv2.imwrite(output_path, thumb, [cv2.IMWRITE_JPEG_QUALITY, 90])
            
            if os.path.isfile(output_path):
                print(f"[Thumbnail] Created: {output_path}")
                return output_path
            else:
                print(f"[Thumbnail] Failed to write: {output_path}")
                return None
                
        except Exception as e:
            print(f"[Thumbnail] Error processing {video_path}: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    @staticmethod
    def get_video_duration(video_path: str) -> int:
        """Get duration of video in frames."""
        if not HAS_CV2 or not os.path.isfile(video_path):
            return 100
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                return 100
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
            return frame_count if frame_count > 0 else 100
        except Exception:
            return 100


# =============================================================================
# DJANGO IMPORTER
# =============================================================================

class DjangoImporter:
    """Imports shots into Django database via API."""
    
    def __init__(self):
        if not HAS_API:
            raise RuntimeError("http_help module not available")
        self.api = http_help.DjangoAPI()
        self._job_cache: Dict[str, int] = {}  # title -> id
        self._timeline_cache: Dict[str, int] = {}  # "job_id:title" -> id
    
    def import_shot(self, shot: ParsedShot, job_title: str, timeline_title: str,
                    colourspace: str, handles: int) -> tuple[int, bool]:
        """
        Import a shot into Django.
        
        Returns:
            Tuple of (shot_id, was_created)
        """
        # Get or create job
        job_id = self._get_or_create_job(job_title)
        
        # Get or create timeline
        timeline_id = self._get_or_create_timeline(job_id, timeline_title)
        
        # Get or create shot with all metadata
        shot_data, created = self.api.get_or_create_shot(
            timeline_id, 
            shot.name, 
            shot.folder_path or ""
        )
        shot_id = shot_data["id"]
        
        # Update shot with additional fields
        update_fields = {
            "duration": str(shot.duration),
            "edit_inpoint": str(shot.edit_inpoint),
            "edit_outpoint": str(shot.edit_outpoint),
            "colourspace": colourspace,
            "handles": str(handles),
            "original_clip": shot.original_clip_name,
            "original_clips": shot.original_clip_paths,
        }
        
        try:
            self.api.update_shot(shot_id, **update_fields)
        except Exception as e:
            print(f"[DjangoImport] Warning: Could not update shot fields: {e}")
        
        return shot_id, created
    
    def upload_thumbnail(self, shot_id: int, thumbnail_path: str) -> bool:
        """Upload a thumbnail for a shot."""
        if not thumbnail_path or not os.path.isfile(thumbnail_path):
            return False
        try:
            self.api.upload_shot_thumbnail(shot_id, thumbnail_path)
            return True
        except Exception as e:
            print(f"[DjangoImport] Thumbnail upload failed: {e}")
            return False
    
    def create_default_tasks(self, shot_id: int, task_names: List[str] = None):
        """Create default tasks for a shot."""
        if task_names is None:
            task_names = DEFAULT_TASKS
        
        for name in task_names:
            try:
                self.api.create_task(shot_id=shot_id, title=name)
            except Exception as e:
                print(f"[DjangoImport] Could not create task '{name}': {e}")
    
    def _get_or_create_job(self, title: str) -> int:
        """Get or create a job, using cache."""
        if title in self._job_cache:
            return self._job_cache[title]
        
        job_data, _ = self.api.get_or_create_job(title)
        job_id = job_data["id"]
        self._job_cache[title] = job_id
        return job_id
    
    def _get_or_create_timeline(self, job_id: int, title: str) -> int:
        """Get or create a timeline, using cache."""
        cache_key = f"{job_id}:{title}"
        if cache_key in self._timeline_cache:
            return self._timeline_cache[cache_key]
        
        tl_data, _ = self.api.get_or_create_timeline(job_id, title)
        tl_id = tl_data["id"]
        self._timeline_cache[cache_key] = tl_id
        return tl_id


# =============================================================================
# IMPORT WORKER THREAD
# =============================================================================

class ImportWorker(QObject):
    """Background worker for the full import process."""
    
    progress = pyqtSignal(int, int, str)  # current, total, message
    finished = pyqtSignal(object)  # ImportStats
    error = pyqtSignal(str)
    
    def __init__(self, shots: List[ParsedShot], project_root: str, edit_name: str,
                 colourspace: str, handles: int, local_work: bool,
                 add_to_db: bool, create_tasks: bool, nuke_path: Optional[str] = None):
        super().__init__()
        self.shots = shots
        self.project_root = project_root
        self.edit_name = edit_name
        self.colourspace = colourspace
        self.handles = handles
        self.local_work = local_work
        self.add_to_db = add_to_db
        self.create_tasks = create_tasks
        self.nuke_path = nuke_path
        
        self.nuke_gen = NukeScriptGenerator(colourspace, local_work, nuke_path=nuke_path)
        self.thumb_gen = ThumbnailGenerator()
        self.django_importer = None
        
        if add_to_db and HAS_API:
            try:
                self.django_importer = DjangoImporter()
            except Exception as e:
                print(f"[ImportWorker] Django importer not available: {e}")
    
    def run(self):
        """Execute the import process."""
        stats = ImportStats()
        total = len(self.shots)
        
        # Derive job title from project root
        job_title = os.path.basename(self.project_root)
        
        try:
            for idx, shot in enumerate(self.shots):
                self.progress.emit(idx + 1, total, f"Processing: {shot.name}")
                
                try:
                    # Step 1: Create folder structure
                    shot_folder = self.nuke_gen.create_shot_folder(
                        shot, self.project_root, self.edit_name
                    )
                    shot.folder_path = shot_folder
                    
                    if os.path.isdir(os.path.join(shot_folder, "scripts")):
                        existing_scripts = os.listdir(os.path.join(shot_folder, "scripts"))
                        if any(f.endswith(".nk") for f in existing_scripts):
                            stats.folders_skipped += 1
                        else:
                            stats.folders_created += 1
                    else:
                        stats.folders_created += 1
                    
                    # Step 2: Generate thumbnail
                    primary_clip = shot.primary_clip
                    if primary_clip and primary_clip.filepath != "offline":
                        thumb_path = os.path.join(shot_folder, f"{shot.name}_thumb_{INITIAL_IMPORT_VERSION_TAG}.jpeg")
                        if not os.path.isfile(thumb_path):
                            result = self.thumb_gen.generate(primary_clip.filepath, thumb_path)
                            if result:
                                shot.thumbnail_path = result
                        else:
                            shot.thumbnail_path = thumb_path
                    
                    # Step 3: Create Nuke script
                    script_path = os.path.join(shot_folder, "scripts", f"{shot.name}_{INITIAL_IMPORT_VERSION_TAG}.nk")
                    if not os.path.isfile(script_path):
                        shot.nuke_script_path = self.nuke_gen.create_nuke_script(
                            shot, shot_folder, self.project_root
                        )
                        stats.nuke_scripts_created += 1
                    else:
                        shot.nuke_script_path = script_path
                    
                    # Step 4: Add to Django DB
                    if self.add_to_db and self.django_importer:
                        self.progress.emit(idx + 1, total, f"Importing to DB: {shot.name}")
                        
                        shot_id, created = self.django_importer.import_shot(
                            shot, job_title, self.edit_name,
                            self.colourspace, self.handles
                        )
                        shot.db_shot_id = shot_id
                        
                        if created:
                            stats.db_shots_created += 1
                            
                            # Upload thumbnail
                            if shot.thumbnail_path:
                                if self.django_importer.upload_thumbnail(shot_id, shot.thumbnail_path):
                                    stats.thumbnails_uploaded += 1
                            
                            # Create default tasks
                            if self.create_tasks:
                                self.django_importer.create_default_tasks(shot_id)
                        else:
                            stats.db_shots_existing += 1
                    
                    stats.shots_processed += 1
                    
                except Exception as e:
                    error_msg = f"Shot {shot.name}: {str(e)}"
                    stats.errors.append(error_msg)
                    print(f"[ImportWorker] Error: {error_msg}")
            
            self.finished.emit(stats)
            
        except Exception as e:
            self.error.emit(str(e))


# =============================================================================
# THUMBNAIL BATCH WORKER
# =============================================================================

class ThumbnailWorker(QThread):
    """Background worker for batch thumbnail generation."""
    
    progress = pyqtSignal(int, int, str)  # current, total, message
    finished_signal = pyqtSignal()
    
    def __init__(self, shots: List[ParsedShot], parent=None):
        super().__init__(parent)
        self.shots = shots
    
    def run(self):
        """Generate thumbnails for all shots."""
        total = len(self.shots)
        
        for idx, shot in enumerate(self.shots):
            self.progress.emit(idx + 1, total, f"Thumbnail: {shot.name}")
            
            primary_clip = shot.primary_clip
            if primary_clip and primary_clip.filepath != "offline":
                # Generate to temp location next to source files
                thumb_dir = os.path.dirname(primary_clip.filepath)
                thumb_dir = os.path.join(thumb_dir, "v1_thumbs")
                
                try:
                    os.makedirs(thumb_dir, exist_ok=True)
                except Exception as e:
                    print(f"[Thumbnail] Could not create dir {thumb_dir}: {e}")
                    continue
                
                thumb_path = os.path.join(thumb_dir, f"{shot.name}_thumb_{INITIAL_IMPORT_VERSION_TAG}.jpeg")
                
                if not os.path.isfile(thumb_path):
                    result = ThumbnailGenerator.generate(primary_clip.filepath, thumb_path)
                    if result:
                        shot.thumbnail_path = result
                    else:
                        print(f"[Thumbnail] Failed to generate: {thumb_path}")
                else:
                    shot.thumbnail_path = thumb_path
        
        self.finished_signal.emit()


# =============================================================================
# LOADING DIALOG
# =============================================================================

class LoadingDialog(QDialog):
    """Progress dialog for long-running operations."""
    
    def __init__(self, parent=None, title: str = "Processing"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(500, 100)
        self.setModal(True)
        
        layout = QVBoxLayout(self)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
    
    def update_progress(self, current: int, total: int, message: str):
        """Update progress bar and message."""
        if total > 0:
            self.progress_bar.setValue(int(100 * current / total))
        self.status_label.setText(message)
        QApplication.processEvents()


# =============================================================================
# MAIN UI PAGE
# =============================================================================

class XMLImportPage(QWidget):
    """Main XML Import page widget."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        
        # Data
        self.shots: List[ParsedShot] = []
        self.parser = XMLTimelineParser()
        self.xml_path: str = ""
        
        # Settings
        self.shot_prefix = "sho"
        self.shot_padding = 3
        self.colourspace = COLOURSPACE_LIST[0]
        self.handles = 25
        self.local_work = False
        self.add_to_db = True
        self.create_tasks = True
        
        # Workers
        self._import_thread: Optional[QThread] = None
        self._thumb_thread: Optional[QThread] = None
        
        # UI Setup
        #self._load_ui()
        self._create_ui()
        self._connect_signals()
        self._init_values()


    
    def _create_ui(self):
        """Create UI programmatically if .ui file not found."""
        main_layout = QVBoxLayout(self)
        
        # Row 1: Shot naming
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Shot name:"))
        self.shoNametextbox = QLineEdit("sho")
        self.shoNametextbox.setMaximumWidth(100)
        row1.addWidget(self.shoNametextbox)
        row1.addWidget(QLabel("Padding:"))
        self.shotNumberPadSpinbox = QSpinBox()
        self.shotNumberPadSpinbox.setRange(3, 4)
        self.shotNumberPadSpinbox.setValue(3)
        row1.addWidget(self.shotNumberPadSpinbox)
        row1.addStretch()
        self.updateBtn = QPushButton("Update Settings")
        row1.addWidget(self.updateBtn)
        main_layout.addLayout(row1)
        
        # Row 2: Colourspace
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Input Transform:"))
        self.colourspaceComboBox = QComboBox()
        self.colourspaceComboBox.addItems(COLOURSPACE_LIST)
        self.colourspaceComboBox.setEditable(True)
        row2.addWidget(self.colourspaceComboBox)
        row2.addStretch()
        main_layout.addLayout(row2)
        
        # Row 3: Project path
        row3 = QHBoxLayout()
        self.btnSelecRoot = QPushButton("Change Project Path")
        self.btnSelecRoot.setEnabled(False)
        row3.addWidget(self.btnSelecRoot)
        self.lableRoot = QLabel("None")
        row3.addWidget(self.lableRoot)
        self.checkBoxlocalwork = QCheckBox("Portable scripts")
        row3.addWidget(self.checkBoxlocalwork)
        row3.addWidget(QLabel("Handles:"))
        self.spinboxHandles = QSpinBox()
        self.spinboxHandles.setValue(25)
        row3.addWidget(self.spinboxHandles)
        row3.addStretch()
        main_layout.addLayout(row3)
        
        # Row 4: Buttons
        row4 = QHBoxLayout()
        self.btnSelectXML = QPushButton("Select XML")
        row4.addWidget(self.btnSelectXML)
        self.btnMakeThumbs = QPushButton("Make Thumbs")
        self.btnMakeThumbs.setEnabled(False)
        row4.addWidget(self.btnMakeThumbs)
        self.btnShotTable = QPushButton("Shot Table")
        self.btnShotTable.setEnabled(False)
        row4.addWidget(self.btnShotTable)
        self.btnNukeAll = QPushButton("Import All")
        self.btnNukeAll.setEnabled(False)
        row4.addWidget(self.btnNukeAll)
        self.checkBoxAddToDB = QCheckBox("Add to ShotBox")
        self.checkBoxAddToDB.setChecked(True)
        row4.addWidget(self.checkBoxAddToDB)
        self.checkBoxCreateTasks = QCheckBox("Create Tasks")
        self.checkBoxCreateTasks.setChecked(True)
        row4.addWidget(self.checkBoxCreateTasks)
        row4.addStretch()
        self.btnMakeShot = QPushButton("Make Single Shot")
        row4.addWidget(self.btnMakeShot)
        main_layout.addLayout(row4)
        
        # Table
        self.tableWidgetTemp = QTableWidget()
        self.tableWidgetTemp.setColumnCount(9)
        self.tableWidgetTemp.setHorizontalHeaderLabels([
            'Shot', 'V1 Thumb', 'V2 Thumb', 'Duration',
            'V1 Clip', 'V2 Clip', 'Setup', 'Layers', 'Status'
        ])
        main_layout.addWidget(self.tableWidgetTemp)
    
    def _connect_signals(self):
        """Connect UI signals to slots."""
        self.btnSelectXML.clicked.connect(self._on_select_xml)
        self.btnSelecRoot.clicked.connect(self._on_select_root)
        self.btnShotTable.clicked.connect(self._on_show_table)
        self.btnMakeThumbs.clicked.connect(self._on_make_thumbs)
        self.btnNukeAll.clicked.connect(self._on_import_all)
        self.updateBtn.clicked.connect(self._on_update_settings)
        
        if hasattr(self, 'btnMakeShot'):
            self.btnMakeShot.clicked.connect(self._on_make_single_shot)
        if hasattr(self, 'btnMakeXL'):
            self.btnMakeXL.clicked.connect(self._on_make_excel)
    
    def _init_values(self):
        """Initialize default values."""
        if hasattr(self, 'shotNumberPadSpinbox'):
            self.shotNumberPadSpinbox.setRange(3, 4)
            self.shotNumberPadSpinbox.setValue(3)
        
        if hasattr(self, 'spinboxHandles'):
            self.spinboxHandles.setValue(25)
        
        if hasattr(self, 'colourspaceComboBox'):
            self.colourspaceComboBox.clear()
            self.colourspaceComboBox.addItems(COLOURSPACE_LIST)
    
    def _on_update_settings(self):
        """Update settings from UI."""
        self.shot_prefix = self.shoNametextbox.text()
        self.shot_padding = self.shotNumberPadSpinbox.value()
        self.colourspace = self.colourspaceComboBox.currentText()
        self.handles = self.spinboxHandles.value()
        self.local_work = self.checkBoxlocalwork.isChecked()
        self.add_to_db = self.checkBoxAddToDB.isChecked()
        self.create_tasks = self.checkBoxCreateTasks.isChecked()
        
        # Rename shots if already parsed
        if self.shots:
            try:
                self._apply_generated_shot_names()
            except ValueError as exc:
                QMessageBox.warning(self, "Invalid Shot Name", str(exc))
                return
            self._on_show_table()
        
        print(f"[Settings] prefix={self.shot_prefix}, padding={self.shot_padding}, "
              f"colourspace={self.colourspace}, handles={self.handles}, db={self.add_to_db}")
    
    def _apply_generated_shot_names(self):
        """Apply generated shot names to the currently parsed shots."""
        for shot in self.shots:
            shot.name = self._shot_name(shot.index + 1)

    def _shot_name(self, number: int) -> str:
        """Generate shot name from number."""
        if self.shot_padding == 3:
            if number < 10:
                candidate = f"{self.shot_prefix}0{number}0"
            else:
                candidate = f"{self.shot_prefix}{number}0"
        elif self.shot_padding == 4:
            if number < 10:
                candidate = f"{self.shot_prefix}00{number}0"
            elif number < 100:
                candidate = f"{self.shot_prefix}0{number}0"
            else:
                candidate = f"{self.shot_prefix}{number}0"
        else:
            candidate = f"{self.shot_prefix}{number}"
        return self._validated_shot_name(candidate)

    def _validated_shot_name(self, shot_name: str) -> str:
        """Validate a shot name against backend limits."""
        if not shot_name:
            raise ValueError("Shot name cannot be empty.")
        if len(shot_name) > SHOT_TITLE_MAX_LENGTH:
            raise ValueError(
                f"Shot name '{shot_name}' exceeds the {SHOT_TITLE_MAX_LENGTH}-character limit."
            )
        return shot_name

    def _current_nuke_path(self) -> Optional[str]:
        """Get the preferred Nuke executable path from settings if available."""
        try:
            from settings import get_settings_manager

            settings = get_settings_manager()
            nuke_exe_path = settings.get("nuke_exe_path", "")
            return nuke_exe_path or None
        except Exception as exc:
            print(f"[XMLImport] Could not read Nuke path from settings: {exc}")
            return None
    
    def _on_select_root(self):
        """Select project root directory."""
        path = QFileDialog.getExistingDirectory(self, "Select Project Root")
        if path:
            self.parser.project_root = path
            self.lableRoot.setText(f"Project: {path}")
    
    def _on_select_xml(self):
        """Select and parse XML file."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Select XML File", "", "XML Files (*.xml)"
        )
        
        if not path:
            return
        
        self.xml_path = path
        self._on_update_settings()
        
        # Parse XML
        try:
            self.shots = self.parser.parse(path, self.handles)
            
            # Apply shot names
            self._apply_generated_shot_names()
            
            # Derive project root from clip paths
            derived_root = self.parser._derive_project_root_from_clips()
            self.parser.project_root = derived_root
            
            # Update UI
            if derived_root:
                self.lableRoot.setText(f"Project: {derived_root}")
            else:
                self.lableRoot.setText("Project: (click Change Project Path)")
            
            self.btnSelecRoot.setEnabled(True)
            self.btnShotTable.setEnabled(True)
            self.btnMakeThumbs.setEnabled(True)
            self.btnNukeAll.setEnabled(True)
            
            # Auto-populate table
            self._on_show_table()
            
        except Exception as e:
            QMessageBox.critical(self, "Parse Error", f"Failed to parse XML:\n{str(e)}")
    
    def _on_show_table(self):
        """Populate the table with parsed shot data."""
        if not self.shots:
            QMessageBox.warning(self, "No Data", "Please select an XML file first.")
            return
        
        table = self.tableWidgetTemp
        table.setRowCount(len(self.shots))
        table.verticalHeader().setDefaultSectionSize(90)
        
        for row, shot in enumerate(self.shots):
            # Shot name
            table.setItem(row, 0, QTableWidgetItem(shot.name))
            
            # V1 Thumbnail
            if shot.thumbnail_path and os.path.isfile(shot.thumbnail_path):
                thumb_label = self._create_thumb_label(shot.thumbnail_path)
                table.setCellWidget(row, 1, thumb_label)
            else:
                primary = shot.primary_clip
                text = os.path.basename(primary.filepath) if primary else "offline"
                table.setItem(row, 1, QTableWidgetItem(text[:30]))
            
            # V2 Thumbnail
            v2_clip = next((c for c in shot.clips if c.track == 2), None)
            if v2_clip:
                table.setItem(row, 2, QTableWidgetItem(os.path.basename(v2_clip.filepath)[:20]))
            else:
                table.setItem(row, 2, QTableWidgetItem("-"))
            
            # Duration
            table.setItem(row, 3, QTableWidgetItem(str(shot.duration)))
            
            # V1 Clip name
            primary = shot.primary_clip
            if primary:
                table.setItem(row, 4, QTableWidgetItem(os.path.basename(primary.filepath)[:25]))
            
            # V2 Clip name
            if v2_clip:
                table.setItem(row, 5, QTableWidgetItem(os.path.basename(v2_clip.filepath)[:25]))
            else:
                table.setItem(row, 5, QTableWidgetItem("-"))
            
            # Setup button
            btn = QPushButton("Setup")
            btn.clicked.connect(lambda checked, s=shot: self._on_setup_single(s))
            table.setCellWidget(row, 6, btn)
            
            # Layer count
            table.setItem(row, 7, QTableWidgetItem(str(len(shot.clips))))
            
            # Status
            table.setItem(row, 8, QTableWidgetItem(""))
        
        # Adjust column widths
        table.setColumnWidth(0, 70)
        table.setColumnWidth(1, 150)
        table.setColumnWidth(2, 150)
        table.setColumnWidth(3, 60)
        table.setColumnWidth(6, 70)
        table.setColumnWidth(7, 50)
    
    def _create_thumb_label(self, path: str) -> QLabel:
        """Create a label with thumbnail image."""
        label = QLabel()
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            label.setPixmap(pixmap.scaled(140, 80, Qt.AspectRatioMode.KeepAspectRatio))
        else:
            label.setText("No preview")
        return label
    
    def _on_make_thumbs(self):
        """Generate thumbnails for all shots."""
        if not self.shots:
            QMessageBox.warning(self, "No Data", "Please select an XML file first.")
            return
        
        dialog = LoadingDialog(self, "Generating Thumbnails")
        dialog.show()
        
        # Create worker thread (QThread subclass)
        self._thumb_worker = ThumbnailWorker(self.shots, self)
        
        # Connect signals
        self._thumb_worker.progress.connect(dialog.update_progress)
        self._thumb_worker.finished_signal.connect(dialog.close)
        self._thumb_worker.finished_signal.connect(self._on_thumbs_finished)
        
        # Start the thread
        self._thumb_worker.start()
    
    def _on_thumbs_finished(self):
        """Called when thumbnail generation is complete."""
        # Refresh the table to show new thumbnails
        self._on_show_table()
    
    def _on_setup_single(self, shot: ParsedShot):
        """Setup a single shot (folder + script + optional DB)."""
        self._on_update_settings()
        
        try:
            nuke_gen = NukeScriptGenerator(
                self.colourspace,
                self.local_work,
                nuke_path=self._current_nuke_path(),
            )
            
            # Create folder
            shot_folder = nuke_gen.create_shot_folder(
                shot, self.parser.project_root, self.parser.edit_name
            )
            shot.folder_path = shot_folder
            
            # Generate thumbnail if needed
            primary = shot.primary_clip
            if primary and primary.filepath != "offline":
                thumb_path = os.path.join(shot_folder, f"{shot.name}_thumb_{INITIAL_IMPORT_VERSION_TAG}.jpeg")
                if not os.path.isfile(thumb_path):
                    ThumbnailGenerator.generate(primary.filepath, thumb_path)
                    shot.thumbnail_path = thumb_path
            
            # Create Nuke script
            nuke_gen.create_nuke_script(shot, shot_folder, self.parser.project_root)
            
            # Add to DB if enabled
            if self.add_to_db and HAS_API:
                try:
                    importer = DjangoImporter()
                    job_title = os.path.basename(self.parser.project_root)
                    shot_id, created = importer.import_shot(
                        shot, job_title, self.parser.edit_name,
                        self.colourspace, self.handles
                    )
                    
                    if created and shot.thumbnail_path:
                        importer.upload_thumbnail(shot_id, shot.thumbnail_path)
                    
                    if created and self.create_tasks:
                        importer.create_default_tasks(shot_id)
                        
                except Exception as e:
                    print(f"[Setup] DB import failed: {e}")
            
            # Update table status
            self._update_shot_status(shot, "Done", "green")
            
        except Exception as e:
            self._update_shot_status(shot, "Error", "red")
            print(f"[Setup] Failed to create shot: {e}")
    
    def _on_import_all(self):
        """Import all shots (folders + scripts + DB)."""
        if not self.shots:
            return
        
        if not self.parser.project_root:
            print("[Import] No project root set")
            return
        
        self._on_update_settings()
        
        # Show progress dialog
        self._import_dialog = LoadingDialog(self, "Importing Shots")
        self._import_dialog.show()
        
        # Create worker (keep reference to prevent garbage collection)
        self._import_worker = ImportWorker(
            shots=self.shots,
            project_root=self.parser.project_root,
            edit_name=self.parser.edit_name,
            colourspace=self.colourspace,
            handles=self.handles,
            local_work=self.local_work,
            add_to_db=self.add_to_db,
            create_tasks=self.create_tasks,
            nuke_path=self._current_nuke_path(),
        )
        
        # Create thread
        self._import_thread = QThread()
        self._import_worker.moveToThread(self._import_thread)
        
        # Connect signals
        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.progress.connect(self._import_dialog.update_progress)
        self._import_worker.finished.connect(self._on_import_finished)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_worker.error.connect(self._import_thread.quit)
        
        # Start
        self._import_thread.start()
    
    def _on_import_finished(self, stats: ImportStats):
        """Handle import completion."""
        if hasattr(self, '_import_dialog'):
            self._import_dialog.close()
        
        # Update table statuses
        for shot in self.shots:
            if shot.folder_path:
                self._update_shot_status(shot, "Done", "green")
        
        # Log errors to console if any
        if stats.errors:
            for err in stats.errors:
                print(f"[Import Error] {err}")
    
    def _on_import_error(self, error: str):
        """Handle import error."""
        if hasattr(self, '_import_dialog'):
            self._import_dialog.close()
        print(f"[Import] Failed: {error}")
    
    def _update_shot_status(self, shot: ParsedShot, status: str, color: str):
        """Update status column for a shot in the table."""
        table = self.tableWidgetTemp
        for row in range(table.rowCount()):
            item = table.item(row, 0)
            if item and item.text() == shot.name:
                status_item = QTableWidgetItem(status)
                status_item.setForeground(QColor(color))
                table.setItem(row, 8, status_item)
                
                # Also update setup button
                btn = table.cellWidget(row, 6)
                if btn and isinstance(btn, QPushButton):
                    btn.setStyleSheet(f"background-color: {color}")
                break
        
    # Put these methods inside your XMLImportPage class
    # They assume you already have: ParsedShot, ParsedClip, ThumbnailGenerator, NukeScriptGenerator, SCRIPT_DIR
    def _create_single_shot_folder_structure(
        self,
        output_directory_path: str,
        shot_name: str,
        parsed_shot_object,
    ) -> str:
        """Create the folder structure for one shot."""

        shot_folder_path = os.path.join(output_directory_path, shot_name)

        if os.path.isdir(shot_folder_path):
            raise FileExistsError(f"Shot folder already exists:\n{shot_folder_path}")

        plates_directory = os.path.join(shot_folder_path, "plates")
        renders_comp_directory = os.path.join(shot_folder_path, "renders", "comp")
        renders_precomp_directory = os.path.join(shot_folder_path, "renders", "precomp")
        scripts_directory = os.path.join(shot_folder_path, "scripts")
        assets_directory = os.path.join(shot_folder_path, "assets")

        os.makedirs(plates_directory, exist_ok=True)
        os.makedirs(renders_comp_directory, exist_ok=True)
        os.makedirs(renders_precomp_directory, exist_ok=True)
        os.makedirs(scripts_directory, exist_ok=True)
        os.makedirs(assets_directory, exist_ok=True)

        return shot_folder_path


    def _build_parsed_shot_from_video_file(
        self,
        shot_name: str,
        video_file_path: str,
    ):
        """Create a ParsedShot from a single video file, using a 1001-based timeline convention."""

        video_duration_frames = ThumbnailGenerator.get_video_duration(video_file_path)

        timeline_start_frame = 1001
        timeline_end_frame = timeline_start_frame + max(0, video_duration_frames - 1)

        parsed_clip = ParsedClip(
            name=os.path.basename(video_file_path),
            filepath=video_file_path,
            in_point=0,
            out_point=video_duration_frames,
            duration=video_duration_frames,
            start_frame=timeline_start_frame,
            end_frame=timeline_end_frame,
            track=1,
        )

        parsed_shot = ParsedShot(
            index=0,
            name=self._validated_shot_name(shot_name),
            clips=[parsed_clip],
            duration=video_duration_frames,
            edit_inpoint=timeline_start_frame,
            edit_outpoint=timeline_end_frame,
        )

        return parsed_shot


    def _on_make_single_shot(self) -> None:
        """UI action: create a single-shot folder, thumbnail, and Nuke script from a video file."""

        # Pull settings from UI into self.local_work, self.colourspace, etc.
        self._on_update_settings()

        creation_dialog = SingleShotCreationDialog(
            self, 
            default_shot_name="sho010",
            colourspace=self.colourspace,
            handles=self.handles,
            add_to_db=self.add_to_db,
            create_tasks=self.create_tasks
        )
        dialog_result = creation_dialog.exec()

        if dialog_result != QDialog.DialogCode.Accepted:
            return

        selected_video_file_path = creation_dialog.selected_video_file_path
        selected_output_directory_path = creation_dialog.selected_output_directory_path
        entered_shot_name = creation_dialog.entered_shot_name
        add_to_db = creation_dialog.add_to_db
        create_tasks = creation_dialog.create_tasks
        job_name = getattr(creation_dialog, 'job_name', '')
        timeline_name = getattr(creation_dialog, 'timeline_name', '')

        try:
            parsed_shot = self._build_parsed_shot_from_video_file(
                shot_name=entered_shot_name,
                video_file_path=selected_video_file_path,
            )

            shot_folder_path = self._create_single_shot_folder_structure(
                output_directory_path=selected_output_directory_path,
                shot_name=entered_shot_name,
                parsed_shot_object=parsed_shot,
            )
            parsed_shot.folder_path = shot_folder_path

            # Generate thumbnail
            thumbnail_file_path = os.path.join(
                shot_folder_path, f"{parsed_shot.name}_thumb_{INITIAL_IMPORT_VERSION_TAG}.jpeg"
            )
            if not os.path.isfile(thumbnail_file_path):
                ThumbnailGenerator.generate(
                    video_path=selected_video_file_path,
                    output_path=thumbnail_file_path,
                )

            if os.path.isfile(thumbnail_file_path):
                parsed_shot.thumbnail_path = thumbnail_file_path

            # Create Nuke script
            nuke_script_generator = NukeScriptGenerator(
                colourspace=self.colourspace,
                local_work=self.local_work,
                nuke_path=self._current_nuke_path(),
            )

            resolved_project_name = job_name or os.path.basename(os.path.normpath(selected_output_directory_path))

            nuke_script_generator.create_nuke_script_with_project_name(
                shot=parsed_shot,
                shot_folder=shot_folder_path,
                project_root="",
                project_name=resolved_project_name,
            )

            # Django import if enabled
            if add_to_db and HAS_API and job_name and timeline_name:
                try:
                    importer = DjangoImporter()
                    
                    # Import the shot
                    shot_id, created = importer.import_shot(
                        shot=parsed_shot,
                        job_title=job_name,
                        timeline_title=timeline_name,
                        colourspace=self.colourspace,
                        handles=self.handles
                    )
                    parsed_shot.db_shot_id = shot_id
                    
                    # Upload thumbnail
                    if created and parsed_shot.thumbnail_path:
                        importer.upload_thumbnail(shot_id, parsed_shot.thumbnail_path)
                    
                    # Create default tasks
                    if created and create_tasks:
                        importer.create_default_tasks(shot_id)
                        
                except Exception as db_error:
                    print(f"[SingleShot] DB Error: {db_error}")

            print(f"[SingleShot] Created: {shot_folder_path}")

        except Exception as error:
            print(f"[SingleShot] Error: {error}")

# This dialog collects: video file, output folder, shot name with DRAG AND DROP support
class SingleShotCreationDialog(QDialog):
    """Dialog to create one shot from a video file without an XML. Supports drag and drop."""

    def __init__(self, parent=None, default_shot_name: str = "sho010", 
                 colourspace: str = "", handles: int = 25,
                 add_to_db: bool = True, create_tasks: bool = True):
        super().__init__(parent)

        self.setWindowTitle("Create Single Shot")
        self.setMinimumWidth(120)
        self.setMinimumHeight(400)
        self.setAcceptDrops(True)

        self.selected_video_file_path: str = ""
        self.selected_output_directory_path: str = ""
        self.entered_shot_name: str = default_shot_name
        
        # Settings passed from parent
        self.colourspace = colourspace
        self.handles = handles
        self.add_to_db = add_to_db
        self.create_tasks = create_tasks

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # Instructions
        instructions = QLabel("Drag and drop files onto the areas below, or use Browse buttons")
        instructions.setStyleSheet("color: #888; font-style: italic;")
        main_layout.addWidget(instructions)

        # Video file drop zone
        self.video_drop_label = QLabel("🎬 Drag and drop a video file here\n(.mov, .mp4, .mxf, etc.)")
        self.video_drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_drop_label.setMinimumHeight(100)
        self.video_drop_label.setStyleSheet("""
            QLabel {
                background-color: #2a2a2a;
                border: 2px dashed #555;
                border-radius: 8px;
                color: #aaa;
                font-size: 14px;
                padding: 20px;
            }
            QLabel:hover {
                border-color: #888;
                background-color: #333;
            }
        """)
        self.video_drop_label.setProperty("drop_target", "video")
        main_layout.addWidget(self.video_drop_label)

        # Video file browse row
        video_file_row_layout = QHBoxLayout()
        video_file_row_layout.addWidget(QLabel("Video file:"))

        self.video_file_path_line_edit = QLineEdit()
        self.video_file_path_line_edit.setPlaceholderText("Or enter path manually...")
        self.video_file_path_line_edit.textChanged.connect(self._on_video_path_changed)
        video_file_row_layout.addWidget(self.video_file_path_line_edit)

        self.select_video_file_button = QPushButton("Browse")
        self.select_video_file_button.clicked.connect(self._select_video_file)
        video_file_row_layout.addWidget(self.select_video_file_button)

        main_layout.addLayout(video_file_row_layout)

        # Output directory drop zone
        self.folder_drop_label = QLabel("📁 Drag and drop output folder here\n(where shot folder will be created)")
        self.folder_drop_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.folder_drop_label.setMinimumHeight(100)
        self.folder_drop_label.setStyleSheet("""
            QLabel {
                background-color: #2a2a2a;
                border: 2px dashed #555;
                border-radius: 8px;
                color: #aaa;
                font-size: 14px;
                padding: 20px;
            }
            QLabel:hover {
                border-color: #888;
                background-color: #333;
            }
        """)
        self.folder_drop_label.setProperty("drop_target", "folder")
        main_layout.addWidget(self.folder_drop_label)

        # Output directory browse row
        output_directory_row_layout = QHBoxLayout()
        output_directory_row_layout.addWidget(QLabel("Output folder:"))

        self.output_directory_path_line_edit = QLineEdit()
        self.output_directory_path_line_edit.setPlaceholderText("Or enter path manually...")
        self.output_directory_path_line_edit.textChanged.connect(self._on_folder_path_changed)
        output_directory_row_layout.addWidget(self.output_directory_path_line_edit)

        self.select_output_directory_button = QPushButton("Browse")
        self.select_output_directory_button.clicked.connect(self._select_output_directory)
        output_directory_row_layout.addWidget(self.select_output_directory_button)

        main_layout.addLayout(output_directory_row_layout)

        # Shot name row
        shot_name_row_layout = QHBoxLayout()
        shot_name_row_layout.addWidget(QLabel("Shot name:"))

        self.shot_name_line_edit = QLineEdit(default_shot_name)
        self.shot_name_line_edit.setMaxLength(SHOT_TITLE_MAX_LENGTH)
        shot_name_row_layout.addWidget(self.shot_name_line_edit)

        main_layout.addLayout(shot_name_row_layout)

        # Options row
        options_row_layout = QHBoxLayout()
        
        self.add_to_db_checkbox = QCheckBox("Add to ShotBox DB")
        self.add_to_db_checkbox.setChecked(add_to_db)
        self.add_to_db_checkbox.setToolTip("Create shot entry in Django database")
        options_row_layout.addWidget(self.add_to_db_checkbox)
        
        self.create_tasks_checkbox = QCheckBox("Create default tasks")
        self.create_tasks_checkbox.setChecked(create_tasks)
        self.create_tasks_checkbox.setToolTip("Create Comp, Roto, Paint, Track tasks")
        options_row_layout.addWidget(self.create_tasks_checkbox)
        
        options_row_layout.addStretch(1)
        main_layout.addLayout(options_row_layout)
        
        # Job/Timeline inputs (for Django - only visible if add_to_db is checked)
        self.db_options_frame = QWidget()
        db_options_layout = QHBoxLayout(self.db_options_frame)
        db_options_layout.setContentsMargins(0, 0, 0, 0)
        
        db_options_layout.addWidget(QLabel("Job:"))
        self.job_name_line_edit = QLineEdit()
        self.job_name_line_edit.setPlaceholderText("Job name for database")
        db_options_layout.addWidget(self.job_name_line_edit)
        
        db_options_layout.addWidget(QLabel("Timeline:"))
        self.timeline_name_line_edit = QLineEdit()
        self.timeline_name_line_edit.setPlaceholderText("Timeline name")
        db_options_layout.addWidget(self.timeline_name_line_edit)
        
        main_layout.addWidget(self.db_options_frame)
        
        # Connect checkbox to show/hide db options
        self.add_to_db_checkbox.stateChanged.connect(self._toggle_db_options)
        self._toggle_db_options()

        # Action buttons row
        action_buttons_row_layout = QHBoxLayout()
        action_buttons_row_layout.addStretch(1)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        action_buttons_row_layout.addWidget(self.cancel_button)

        self.create_shot_button = QPushButton("Create Shot")
        self.create_shot_button.clicked.connect(self._validate_and_accept)
        self.create_shot_button.setStyleSheet("background-color: #f05a20; color: white; font-weight: bold; padding: 8px 16px;")
        action_buttons_row_layout.addWidget(self.create_shot_button)

        main_layout.addLayout(action_buttons_row_layout)
    
    def _toggle_db_options(self):
        """Show/hide database options based on checkbox."""
        self.db_options_frame.setVisible(self.add_to_db_checkbox.isChecked())
    
    def _on_video_path_changed(self, text):
        """Update drop label when video path changes."""
        if text and os.path.isfile(text):
            filename = os.path.basename(text)
            self.video_drop_label.setText(f"✅ {filename}")
            self.video_drop_label.setStyleSheet("""
                QLabel {
                    background-color: #1a3a1a;
                    border: 2px solid #4a4;
                    border-radius: 8px;
                    color: #8f8;
                    font-size: 14px;
                    padding: 20px;
                }
            """)
        else:
            self.video_drop_label.setText("🎬 Drag and drop a video file here\n(.mov, .mp4, .mxf, etc.)")
            self.video_drop_label.setStyleSheet("""
                QLabel {
                    background-color: #2a2a2a;
                    border: 2px dashed #555;
                    border-radius: 8px;
                    color: #aaa;
                    font-size: 14px;
                    padding: 20px;
                }
            """)
    
    def _on_folder_path_changed(self, text):
        """Update drop label when folder path changes."""
        if text and os.path.isdir(text):
            foldername = os.path.basename(text)
            self.folder_drop_label.setText(f"✅ {foldername}")
            self.folder_drop_label.setStyleSheet("""
                QLabel {
                    background-color: #1a3a1a;
                    border: 2px solid #4a4;
                    border-radius: 8px;
                    color: #8f8;
                    font-size: 14px;
                    padding: 20px;
                }
            """)
            
            # Auto-fill job name from folder structure if empty
            if not self.job_name_line_edit.text():
                # Try to extract job name from path (parent folder)
                parent = os.path.dirname(text)
                if parent:
                    self.job_name_line_edit.setText(os.path.basename(parent))
            
            # Auto-fill timeline name if empty
            if not self.timeline_name_line_edit.text():
                self.timeline_name_line_edit.setText(foldername)
        else:
            self.folder_drop_label.setText("📁 Drag and drop output folder here\n(where shot folder will be created)")
            self.folder_drop_label.setStyleSheet("""
                QLabel {
                    background-color: #2a2a2a;
                    border: 2px dashed #555;
                    border-radius: 8px;
                    color: #aaa;
                    font-size: 14px;
                    padding: 20px;
                }
            """)

    def dragEnterEvent(self, event):
        """Accept drag events with URLs."""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Handle drag move to highlight drop zones."""
        event.acceptProposedAction()

    def dropEvent(self, event):
        """Handle file/folder drops."""
        urls = event.mimeData().urls()
        if not urls:
            return

        file_path = urls[0].toLocalFile()
        drop_pos = event.position().toPoint()

        # Check which drop zone was targeted
        video_geo = self.video_drop_label.geometry()
        folder_geo = self.folder_drop_label.geometry()

        # Adjust for potential parent widget offsets
        if video_geo.contains(drop_pos) or self.video_drop_label.geometry().contains(self.video_drop_label.mapFromGlobal(self.mapToGlobal(drop_pos))):
            # Dropped on video zone
            if os.path.isfile(file_path):
                self.video_file_path_line_edit.setText(file_path)
                self.selected_video_file_path = file_path
        elif folder_geo.contains(drop_pos) or self.folder_drop_label.geometry().contains(self.folder_drop_label.mapFromGlobal(self.mapToGlobal(drop_pos))):
            # Dropped on folder zone
            if os.path.isdir(file_path):
                self.output_directory_path_line_edit.setText(file_path)
                self.selected_output_directory_path = file_path
            elif os.path.isfile(file_path):
                # If they dropped a file, use its parent directory
                parent_dir = os.path.dirname(file_path)
                self.output_directory_path_line_edit.setText(parent_dir)
                self.selected_output_directory_path = parent_dir
        else:
            # Dropped somewhere else - try to guess based on file type
            if os.path.isfile(file_path):
                # It's a file - assume it's the video
                ext = os.path.splitext(file_path)[1].lower()
                if ext in ['.mov', '.mp4', '.mxf', '.avi', '.mkv', '.r3d', '.braw', '.arx']:
                    self.video_file_path_line_edit.setText(file_path)
                    self.selected_video_file_path = file_path
            elif os.path.isdir(file_path):
                # It's a folder - assume it's the output directory
                self.output_directory_path_line_edit.setText(file_path)
                self.selected_output_directory_path = file_path

        event.acceptProposedAction()

    def _select_video_file(self) -> None:
        selected_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video File",
            "",
            "Video Files (*.mov *.mp4 *.mxf *.avi *.mkv *.r3d *.braw);;All Files (*)",
        )
        if selected_path:
            self.video_file_path_line_edit.setText(selected_path)
            self.selected_video_file_path = selected_path

    def _select_output_directory(self) -> None:
        selected_directory = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if selected_directory:
            self.output_directory_path_line_edit.setText(selected_directory)
            self.selected_output_directory_path = selected_directory

    def _validate_and_accept(self) -> None:
        video_file_path = self.video_file_path_line_edit.text().strip()
        output_directory_path = self.output_directory_path_line_edit.text().strip()
        shot_name = self.shot_name_line_edit.text().strip()

        # Simple validation - just check fields are filled
        if not video_file_path or not os.path.isfile(video_file_path):
            self.video_file_path_line_edit.setFocus()
            return

        if not output_directory_path or not os.path.isdir(output_directory_path):
            self.output_directory_path_line_edit.setFocus()
            return

        if not shot_name:
            self.shot_name_line_edit.setFocus()
            return
        if len(shot_name) > SHOT_TITLE_MAX_LENGTH:
            QMessageBox.warning(
                self,
                "Invalid Shot Name",
                f"Shot names must be {SHOT_TITLE_MAX_LENGTH} characters or fewer.",
            )
            self.shot_name_line_edit.setFocus()
            return
        
        # Validate DB options if enabled
        if self.add_to_db_checkbox.isChecked():
            if not self.job_name_line_edit.text().strip():
                self.job_name_line_edit.setFocus()
                return
            if not self.timeline_name_line_edit.text().strip():
                self.timeline_name_line_edit.setFocus()
                return

        self.selected_video_file_path = video_file_path
        self.selected_output_directory_path = output_directory_path
        self.entered_shot_name = shot_name
        self.add_to_db = self.add_to_db_checkbox.isChecked()
        self.create_tasks = self.create_tasks_checkbox.isChecked()
        self.job_name = self.job_name_line_edit.text().strip()
        self.timeline_name = self.timeline_name_line_edit.text().strip()

        self.accept()
# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Load stylesheet if available
    qss_path = os.path.join(SCRIPT_DIR, "styles.qss")
    if os.path.exists(qss_path):
        with open(qss_path, "r") as f:
            app.setStyleSheet(f.read())
    
    window = XMLImportPage()
    window.setWindowTitle("ShotBox - XML Import")
    window.resize(1200, 800)
    window.show()
    
    sys.exit(app.exec())
