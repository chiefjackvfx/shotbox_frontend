"""
Standalone 3DE project creator for EXR sequences.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import struct
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

import matchmove_helpers as shared_matchmove


# =============================================================================
# CONFIG / PRESETS
# =============================================================================

# Sensor sizes are kept in millimeters for readability. 3DE expects centimeters,
# so they are converted during the automation step.
CAMERA_PRESETS = {
    "Alexa 35": {
        "sensor_width_mm": 27.99,
        "sensor_height_mm": 19.22,
    },
    "Alexa LF": {
        "sensor_width_mm": 36.70,
        "sensor_height_mm": 25.54,
    },
}

THREEDE_CMD = ""

# Keep path layouts data-only so future pipeline changes stay localized.
OUTPUT_LAYOUTS = {
    "shots_matchmove": "{project_root}/shots/{shot}/matchmove",
    "assets_matchmove": "{project_root}/assets/matchmove/{shot}",
    "vfx_assets_matchmove": "{project_root}/VFX/assets/matchmove/{shot}",
}

DEFAULT_OUTPUT_LAYOUT = "shots_matchmove"
MATCHMOVE_WORK_DIRNAME = "work"
MATCHMOVE_EXPORT_DIRNAME = "export"
DEFAULT_FPS = 25.0
DEFAULT_FOCAL_LENGTH_MM = 35.0
DEFAULT_8BIT_COLOR_GAMMA = 2.2
DEFAULT_8BIT_COLOR_SOFTCLIP = 1.0
MAX_EXR_CLIPS = 5
INITIAL_EXR_CLIP_SLOTS = 1
SHOT_NAME_MAX_LENGTH = 50
PROJECT_FILENAME_RE = r"^{shot}_matchmove_v(\d{{3}})\.3de$"

DROP_STYLE_EMPTY = """
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
"""

DROP_STYLE_VALID = """
QLabel {
    background-color: #1a3a1a;
    border: 2px solid #4a4;
    border-radius: 8px;
    color: #8f8;
    font-size: 14px;
    padding: 20px;
}
"""

DROP_STYLE_INVALID = """
QLabel {
    background-color: #3a1a1a;
    border: 2px solid #a44;
    border-radius: 8px;
    color: #f99;
    font-size: 14px;
    padding: 20px;
}
"""

PRESET_BUTTON_STYLE = """
QPushButton {
    padding: 6px 12px;
    border: 1px solid #555;
    border-radius: 12px;
    background-color: #353535;
    color: #ddd;
}
QPushButton:hover {
    border-color: #f05a20;
}
QPushButton:checked {
    background-color: #f05a20;
    border-color: #f05a20;
    color: white;
}
"""


# =============================================================================
# FLOW LAYOUT
# =============================================================================

class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, h_spacing=-1, v_spacing=-1):
        super().__init__(parent)
        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._item_list: list[QLayoutItem] = []

    def __del__(self):
        while self.count():
            self.takeAt(0)

    def addItem(self, item: QLayoutItem) -> None:
        self._item_list.append(item)

    def count(self) -> int:
        return len(self._item_list)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._item_list:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        size += QSize(left + right, top + bottom)
        return size

    def horizontalSpacing(self) -> int:
        if self._h_space >= 0:
            return self._h_space
        return self._smart_spacing(QSizePolicy.ControlType.PushButton)

    def verticalSpacing(self) -> int:
        if self._v_space >= 0:
            return self._v_space
        return self._smart_spacing(QSizePolicy.ControlType.PushButton)

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)

        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0
        h_space = self.horizontalSpacing()
        v_space = self.verticalSpacing()

        for item in self._item_list:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue

            next_x = x + item.sizeHint().width() + h_space
            if next_x - h_space > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + v_space
                next_x = x + item.sizeHint().width() + h_space
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + bottom

    def _smart_spacing(self, control_type):
        parent = self.parent()
        if not parent:
            return -1
        if parent.isWidgetType():
            return parent.style().layoutSpacing(
                control_type,
                control_type,
                Qt.Orientation.Horizontal,
            )
        return parent.spacing()


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass(frozen=True)
class SequenceInfo:
    folder_path: str
    example_file: str
    sequence_path_pattern: str
    display_pattern: str
    prefix: str
    suffix: str
    padding: int
    first_frame: int
    last_frame: int
    frames: tuple[int, ...]
    width: int
    height: int
    header_pixel_aspect: float

    @property
    def frame_count(self) -> int:
        return len(self.frames)


@dataclass(frozen=True)
class ClipBuildRequest:
    slot_index: int
    clip_name: str
    camera_name: str
    lens_name: str
    sequence_info: SequenceInfo
    focal_length_mm: float
    sequence_start_frame: int
    sequence_end_frame: int

    @property
    def internal_frame_count(self) -> int:
        return self.sequence_end_frame - self.sequence_start_frame + 1


@dataclass(frozen=True)
class ProjectBuildRequest:
    project_root: str
    project_name: str
    shot_name: str
    clips: tuple[ClipBuildRequest, ...]
    camera_preset_name: str
    matchmove_dir: str
    export_dir: str
    fps: float
    project_dir: str
    project_path: str
    version: int

    @property
    def clip_count(self) -> int:
        return len(self.clips)


@dataclass(frozen=True)
class HeadlessRunResult:
    runtime_script_path: str
    status_path: str
    command: list[str]
    stdout: str
    stderr: str


# =============================================================================
# EXR DETECTION HELPERS
# =============================================================================

def normalize_user_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip())))


def sanitize_auto_shot_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    cleaned = cleaned.strip("_")
    return cleaned[:SHOT_NAME_MAX_LENGTH] or "shot"


def validate_shot_name(name: str) -> str:
    shot_name = name.strip()
    if not shot_name:
        raise ValueError("Shot name is required.")
    if len(shot_name) > SHOT_NAME_MAX_LENGTH:
        raise ValueError(
            f"Shot names must be {SHOT_NAME_MAX_LENGTH} characters or fewer."
        )
    if "/" in shot_name or "\\" in shot_name:
        raise ValueError("Shot name cannot contain path separators.")
    return shot_name


def format_focal_length_label(focal_length_mm: float) -> str:
    return f"{focal_length_mm:g}mm"


def build_unique_camera_name(
    shot_name: str,
    folder_path: str,
    slot_index: int,
    used_names: set[str],
) -> str:
    folder_name = sanitize_auto_shot_name(Path(folder_path).name)
    if not folder_name:
        folder_name = f"clip_{slot_index + 1:02d}"

    if folder_name.lower() == shot_name.lower():
        candidate = shot_name
    else:
        candidate = f"{shot_name}_{folder_name}"

    base_candidate = candidate
    suffix = 2
    while candidate.lower() in used_names:
        candidate = f"{base_candidate}_{suffix:02d}"
        suffix += 1

    used_names.add(candidate.lower())
    return candidate


def detect_exr_sequence(folder_path: str) -> SequenceInfo:
    normalized_folder = normalize_user_path(folder_path)
    folder = Path(normalized_folder)
    if not folder.is_dir():
        raise ValueError(f"EXR folder does not exist:\n{normalized_folder}")

    exr_files = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() == ".exr"
    )
    if not exr_files:
        raise ValueError(f"No EXR files found in:\n{normalized_folder}")

    sequence_groups: dict[tuple[str, str, int], list[tuple[int, Path]]] = {}
    for path in exr_files:
        match = re.match(r"^(.*?)(\d+)([^0-9]*\.exr)$", path.name, re.IGNORECASE)
        if not match:
            raise ValueError(
                "Could not detect frame numbers from EXR filenames.\n"
                f"Expected the last numeric block before .exr in:\n{path.name}"
            )

        prefix, frame_text, suffix = match.groups()
        frame_number = int(frame_text)
        key = (prefix, suffix, len(frame_text))
        sequence_groups.setdefault(key, []).append((frame_number, path))

    if len(sequence_groups) != 1:
        patterns = []
        for prefix, suffix, padding in sorted(sequence_groups):
            patterns.append(f"{prefix}{'#' * padding}{suffix}")
        raise ValueError(
            "Expected exactly one EXR sequence in the folder.\n"
            f"Found multiple patterns:\n- " + "\n- ".join(patterns)
        )

    (prefix, suffix, padding), frame_entries = next(iter(sequence_groups.items()))
    frame_numbers = [frame_number for frame_number, _ in frame_entries]
    duplicate_counter = Counter(frame_numbers)
    duplicate_frames = sorted(frame for frame, count in duplicate_counter.items() if count > 1)
    if duplicate_frames:
        duplicates_text = ", ".join(str(frame) for frame in duplicate_frames[:10])
        raise ValueError(
            "Duplicate EXR frames detected.\n"
            f"Frame numbers: {duplicates_text}"
        )

    frame_numbers = sorted(frame_numbers)
    expected_frames = list(range(frame_numbers[0], frame_numbers[-1] + 1))
    if frame_numbers != expected_frames:
        missing_frames = sorted(set(expected_frames) - set(frame_numbers))
        missing_text = ", ".join(str(frame) for frame in missing_frames[:15])
        extra = ""
        if len(missing_frames) > 15:
            extra = f" ... (+{len(missing_frames) - 15} more)"
        raise ValueError(
            "EXR sequence has frame gaps.\n"
            f"Missing frames: {missing_text}{extra}"
        )

    first_file = sorted(frame_entries, key=lambda item: item[0])[0][1]
    width, height, pixel_aspect = read_exr_resolution(str(first_file))
    display_pattern = f"{prefix}{'#' * padding}{suffix}"
    sequence_path_pattern = str(folder / display_pattern)

    return SequenceInfo(
        folder_path=normalized_folder,
        example_file=str(first_file),
        sequence_path_pattern=sequence_path_pattern.replace("\\", "/"),
        display_pattern=display_pattern,
        prefix=prefix,
        suffix=suffix,
        padding=padding,
        first_frame=frame_numbers[0],
        last_frame=frame_numbers[-1],
        frames=tuple(frame_numbers),
        width=width,
        height=height,
        header_pixel_aspect=pixel_aspect,
    )


def read_exr_resolution(file_path: str) -> tuple[int, int, float]:
    return read_exr_resolution_from_header(file_path)


def read_exr_resolution_from_header(file_path: str) -> tuple[int, int, float]:
    attributes = parse_exr_header_attributes(file_path)

    data_window_type, data_window_bytes = attributes.get("dataWindow", (None, None))
    if data_window_type != "box2i" or not data_window_bytes or len(data_window_bytes) < 16:
        raise ValueError(f"EXR dataWindow header is missing or invalid:\n{file_path}")

    min_x, min_y, max_x, max_y = struct.unpack("<iiii", data_window_bytes[:16])
    width = max_x - min_x + 1
    height = max_y - min_y + 1
    if width <= 0 or height <= 0:
        raise ValueError(f"EXR dataWindow produced an invalid resolution:\n{file_path}")

    pixel_aspect = 1.0
    pixel_aspect_type, pixel_aspect_bytes = attributes.get("pixelAspectRatio", (None, None))
    if pixel_aspect_type == "float" and pixel_aspect_bytes and len(pixel_aspect_bytes) >= 4:
        pixel_aspect = float(struct.unpack("<f", pixel_aspect_bytes[:4])[0])

    return width, height, pixel_aspect


def parse_exr_header_attributes(file_path: str) -> dict[str, tuple[str, bytes]]:
    with open(file_path, "rb") as handle:
        magic = handle.read(4)
        if len(magic) != 4 or int.from_bytes(magic, "little") != 20000630:
            raise ValueError(f"Not a valid EXR file:\n{file_path}")

        version = handle.read(4)
        if len(version) != 4:
            raise ValueError(f"Incomplete EXR version header:\n{file_path}")

        attributes: dict[str, tuple[str, bytes]] = {}
        while True:
            name = read_exr_c_string(handle)
            if not name:
                break

            attr_type = read_exr_c_string(handle)
            size_bytes = handle.read(4)
            if len(size_bytes) != 4:
                raise ValueError(f"Incomplete EXR attribute size for '{name}':\n{file_path}")

            size = int.from_bytes(size_bytes, "little", signed=False)
            value = handle.read(size)
            if len(value) != size:
                raise ValueError(f"Incomplete EXR attribute payload for '{name}':\n{file_path}")

            attributes[name] = (attr_type, value)

        return attributes


def read_exr_c_string(handle) -> str:
    chunks = bytearray()
    while True:
        char = handle.read(1)
        if char == b"":
            raise ValueError("Unexpected end of EXR header while reading a null-terminated string.")
        if char == b"\x00":
            return chunks.decode("utf-8", errors="replace")
        chunks.extend(char)


def resolve_requested_frame_range(
    sequence_info: SequenceInfo,
    start_text: str,
    end_text: str,
) -> tuple[int, int]:
    start_text = start_text.strip()
    end_text = end_text.strip()
    if not start_text and not end_text:
        return sequence_info.first_frame, sequence_info.last_frame
    if not start_text or not end_text:
        raise ValueError("Frame override start and end must both be filled or both be empty.")

    sequence_start = int(start_text)
    sequence_end = int(end_text)
    if sequence_start > sequence_end:
        raise ValueError("Frame override start must be less than or equal to the end.")
    if sequence_start < sequence_info.first_frame or sequence_end > sequence_info.last_frame:
        raise ValueError(
            "Frame override must stay inside the detected EXR range.\n"
            f"Detected range: {sequence_info.first_frame}-{sequence_info.last_frame}"
        )
    return sequence_start, sequence_end


# =============================================================================
# SAVE PATH / VERSION HELPERS
# =============================================================================

def build_output_directory(
    project_root: str,
    shot_name: str,
    layout_key: str = DEFAULT_OUTPUT_LAYOUT,
) -> str:
    if layout_key not in OUTPUT_LAYOUTS:
        raise KeyError(f"Unknown output layout: {layout_key}")
    normalized_root = normalize_user_path(project_root)
    template = OUTPUT_LAYOUTS[layout_key]
    return os.path.normpath(
        template.format(
            project_root=normalized_root,
            shot=shot_name,
        )
    )


def build_work_directory(matchmove_dir: str) -> str:
    return os.path.join(matchmove_dir, MATCHMOVE_WORK_DIRNAME)


def build_export_directory(matchmove_dir: str) -> str:
    return os.path.join(matchmove_dir, MATCHMOVE_EXPORT_DIRNAME)


def get_next_project_version(project_dir: str, shot_name: str) -> int:
    directory = Path(project_dir)
    if not directory.is_dir():
        return 1

    version_pattern = re.compile(
        PROJECT_FILENAME_RE.format(shot=re.escape(shot_name)),
        re.IGNORECASE,
    )
    versions = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = version_pattern.match(path.name)
        if match:
            versions.append(int(match.group(1)))
    return max(versions, default=0) + 1


def resolve_project_path(
    project_root: str,
    shot_name: str,
    layout_key: str = DEFAULT_OUTPUT_LAYOUT,
) -> tuple[str, str, str, str, int]:
    matchmove_dir = build_output_directory(project_root, shot_name, layout_key=layout_key)
    project_dir = build_work_directory(matchmove_dir)
    export_dir = build_export_directory(matchmove_dir)
    version = get_next_project_version(project_dir, shot_name)
    filename = f"{shot_name}_matchmove_v{version:03d}.3de"
    project_path = os.path.join(project_dir, filename)
    return matchmove_dir, project_dir, export_dir, project_path, version


# =============================================================================
# 3DE AUTOMATION HELPERS
# =============================================================================

def build_project_notes(request: ProjectBuildRequest) -> str:
    lines = [
        "ShotBox 3DE Project",
        f"Shot: {request.shot_name}",
        f"Camera preset: {request.camera_preset_name}",
        f"FPS: {request.fps:g}",
        f"8-bit conversion: gamma {DEFAULT_8BIT_COLOR_GAMMA:g}, softclip {DEFAULT_8BIT_COLOR_SOFTCLIP:g}",
        f"Clip count: {request.clip_count}",
    ]
    if request.project_name.strip():
        lines.insert(2, f"Project: {request.project_name.strip()}")

    for clip in request.clips:
        lines.append(
            f"Clip {clip.slot_index + 1}: {clip.camera_name} | "
            f"focal {clip.lens_name} | "
            f"{clip.sequence_info.sequence_path_pattern} | "
            f"{clip.sequence_start_frame}-{clip.sequence_end_frame} | "
            f"{clip.sequence_info.width}x{clip.sequence_info.height}"
        )
    return "\n".join(lines)


def build_3de_runtime_script(
    request: ProjectBuildRequest,
    runtime_script_path: str,
    status_path: str,
) -> str:
    camera_preset = CAMERA_PRESETS[request.camera_preset_name]
    config = {
        "project_dir": request.project_dir.replace("\\", "/"),
        "project_path": request.project_path.replace("\\", "/"),
        "status_path": status_path.replace("\\", "/"),
        "fps": float(request.fps),
        "sensor_width_cm": float(camera_preset["sensor_width_mm"]) / 10.0,
        "sensor_height_cm": float(camera_preset["sensor_height_mm"]) / 10.0,
        "film_aspect": float(camera_preset["sensor_width_mm"]) / float(camera_preset["sensor_height_mm"]),
        "color_gamma": float(DEFAULT_8BIT_COLOR_GAMMA),
        "color_softclip": float(DEFAULT_8BIT_COLOR_SOFTCLIP),
        "project_notes": build_project_notes(request),
        "runtime_script_path": runtime_script_path.replace("\\", "/"),
        "clips": [
            {
                "camera_name": clip.camera_name,
                "lens_name": clip.lens_name,
                "sequence_path": clip.sequence_info.sequence_path_pattern.replace("\\", "/"),
                **shared_matchmove.build_3de_frame_mapping(
                    clip.sequence_start_frame,
                    clip.sequence_end_frame,
                ),
                "image_width": clip.sequence_info.width,
                "image_height": clip.sequence_info.height,
                "pixel_aspect": float(clip.sequence_info.header_pixel_aspect),
                "focal_length_cm": float(clip.focal_length_mm) / 10.0,
            }
            for clip in request.clips
        ],
    }

    return f"""import json
import os
import sys
import traceback

import tde4


CONFIG = {json.dumps(config, indent=4)}


def write_status(success, error="", traceback_text=""):
    payload = {{
        "success": bool(success),
        "error": error,
        "traceback": traceback_text,
        "project_path": CONFIG["project_path"],
        "runtime_script_path": CONFIG["runtime_script_path"],
    }}
    with open(CONFIG["status_path"], "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def main():
    tde4.newProject()

    removed_cameras = 0
    for camera in list(tde4.getCameraList(0) or []):
        tde4.deleteCamera(camera)
        removed_cameras += 1

    removed_lenses = 0
    for lens in list(tde4.getLensList(0) or []):
        tde4.deleteLens(lens)
        removed_lenses += 1

    os.makedirs(CONFIG["project_dir"], exist_ok=True)

    tde4.setProjectNotes(CONFIG["project_notes"])
    created_cameras = []

    print(
        "Cleared startup items:",
        "cameras=%d" % removed_cameras,
        "lenses=%d" % removed_lenses,
    )

    for clip in CONFIG["clips"]:
        camera = tde4.createCamera("SEQUENCE")
        lens = tde4.createLens()

        tde4.setCurrentCamera(camera)
        tde4.setCameraName(camera, clip["camera_name"])
        tde4.setCameraLens(camera, lens)
        tde4.setLensName(lens, clip["lens_name"])

        tde4.setCameraPath(camera, clip["sequence_path"])
        tde4.setCameraSequenceAttr(
            camera,
            clip["sequence_start"],
            clip["sequence_end"],
            1,
        )
        tde4.setCameraImageWidth(camera, clip["image_width"])
        tde4.setCameraImageHeight(camera, clip["image_height"])
        tde4.setCameraFrameOffset(camera, clip["frame_offset"])
        tde4.setCameraFPS(camera, CONFIG["fps"])
        tde4.setCameraPlaybackRange(camera, clip["playback_start"], clip["playback_end"])
        tde4.setCameraCalculationRange(camera, clip["playback_start"], clip["playback_end"])
        tde4.setCameraFrameRangeCalculationFlag(camera, 1)
        tde4.setCurrentFrame(camera, clip["playback_start"])

        tde4.setCameraFocusMode(camera, "FOCUS_USE_FROM_LENS")
        tde4.setCameraFocalLengthMode(camera, "FOCAL_USE_FROM_LENS")
        tde4.setCamera8BitColorGamma(camera, CONFIG["color_gamma"])
        tde4.setCamera8BitColorSoftclip(camera, CONFIG["color_softclip"])

        tde4.setLensFBackWidth(lens, CONFIG["sensor_width_cm"])
        tde4.setLensFBackHeight(lens, CONFIG["sensor_height_cm"])
        tde4.setLensFilmAspect(lens, CONFIG["film_aspect"])
        tde4.setLensPixelAspect(lens, clip["pixel_aspect"])
        tde4.setLensFocalLength(lens, clip["focal_length_cm"])

        created_cameras.append(camera)

        print("Created camera:", clip["camera_name"])
        print("Created lens:", clip["lens_name"])
        print("8-bit conversion:", f"gamma={{CONFIG['color_gamma']}}", f"softclip={{CONFIG['color_softclip']}}")
        print("Sequence path:", clip["sequence_path"])
        print("Sequence range:", f"{{clip['sequence_start']}}-{{clip['sequence_end']}}")
        print("Playback range:", f"{{clip['playback_start']}}-{{clip['playback_end']}}")
        print("Frame offset:", clip["frame_offset"])

    if created_cameras:
        tde4.setCurrentCamera(created_cameras[0])
        tde4.setCurrentFrame(created_cameras[0], CONFIG["clips"][0]["playback_start"])

    saved = tde4.saveProject(CONFIG["project_path"], 0)
    if not saved:
        raise RuntimeError(f"3DE reported saveProject failure for: {{CONFIG['project_path']}}")

    print("Saved project:", CONFIG["project_path"])


try:
    main()
except Exception as error:
    tb = traceback.format_exc()
    print("ERROR:", error)
    print(tb)
    write_status(False, str(error), tb)
    raise
else:
    write_status(True)
"""


def run_headless_3de(
    request: ProjectBuildRequest,
    log_callback,
) -> HeadlessRunResult:
    three_de_path = shared_matchmove.resolve_3de_executable()
    temp_dir = tempfile.gettempdir()

    runtime_script_fd, runtime_script_path = tempfile.mkstemp(
        prefix="shotbox_3de_",
        suffix=".py",
        dir=temp_dir,
        text=True,
    )
    os.close(runtime_script_fd)

    status_fd, status_path = tempfile.mkstemp(
        prefix="shotbox_3de_",
        suffix=".json",
        dir=temp_dir,
        text=True,
    )
    os.close(status_fd)
    os.unlink(status_path)

    script_text = build_3de_runtime_script(request, runtime_script_path, status_path)
    with open(runtime_script_path, "w", encoding="utf-8") as handle:
        handle.write(script_text)

    command = [
        three_de_path,
        "-no_gui",
        "-run_script",
        runtime_script_path,
    ]
    log_callback(f"Launching 3DE headless: {shlex.join(command)}")

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    if completed.stdout.strip():
        for line in completed.stdout.strip().splitlines():
            log_callback(f"[3DE stdout] {line}")
    if completed.stderr.strip():
        for line in completed.stderr.strip().splitlines():
            log_callback(f"[3DE stderr] {line}")

    status_payload = {}
    if os.path.isfile(status_path):
        with open(status_path, "r", encoding="utf-8") as handle:
            status_payload = json.load(handle)

    if (
        completed.returncode != 0
        or not status_payload.get("success")
        or not os.path.isfile(request.project_path)
    ):
        status_error = status_payload.get("error") or "Unknown 3DE headless error."
        status_traceback = status_payload.get("traceback", "").strip()
        stdout_text = completed.stdout.strip()
        stderr_text = completed.stderr.strip()
        extra_parts = [
            f"Return code: {completed.returncode}",
            f"Runtime script: {runtime_script_path}",
            f"Status file: {status_path}",
        ]
        if stdout_text:
            extra_parts.append(f"Stdout:\n{stdout_text}")
        if stderr_text:
            extra_parts.append(f"Stderr:\n{stderr_text}")
        if status_traceback:
            extra_parts.append(f"Traceback:\n{status_traceback}")
        raise RuntimeError(
            "3DE headless project creation failed.\n"
            f"Error: {status_error}\n"
            + "\n".join(extra_parts)
        )

    return HeadlessRunResult(
        runtime_script_path=runtime_script_path,
        status_path=status_path,
        command=command,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def cleanup_headless_artifacts(result: HeadlessRunResult) -> None:
    for path in (result.runtime_script_path, result.status_path):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def open_3de_project(project_path: str) -> list[str]:
    three_de_path = shared_matchmove.resolve_3de_executable()
    command = [three_de_path, "-open", project_path]
    subprocess.Popen(command, **shared_matchmove._background_launch_kwargs())
    return command


# =============================================================================
# UI
# =============================================================================

class PathDropLabel(QLabel):
    pathDropped = pyqtSignal(str)

    def __init__(self, default_text: str, drop_kind: str, parent=None):
        super().__init__(default_text, parent)
        self.default_text = default_text
        self.drop_kind = drop_kind
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(95)
        self.setWordWrap(True)
        self.setAcceptDrops(True)
        self.show_empty()

    def show_empty(self) -> None:
        self.setText(self.default_text)
        self.setStyleSheet(DROP_STYLE_EMPTY)

    def show_valid(self, label: str) -> None:
        self.setText(label)
        self.setStyleSheet(DROP_STYLE_VALID)

    def show_invalid(self, label: str) -> None:
        self.setText(label)
        self.setStyleSheet(DROP_STYLE_INVALID)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return

        for url in urls:
            dropped_path = url.toLocalFile()
            if not dropped_path:
                continue
            normalized = self._coerce_dropped_path(dropped_path)
            if normalized:
                self.pathDropped.emit(normalized)
                event.acceptProposedAction()
                return

        event.ignore()

    def _coerce_dropped_path(self, path: str) -> Optional[str]:
        normalized = normalize_user_path(path)
        if self.drop_kind == "exr_folder":
            if os.path.isdir(normalized):
                return normalized
            if os.path.isfile(normalized) and normalized.lower().endswith(".exr"):
                return os.path.dirname(normalized)
            return None

        if os.path.isdir(normalized):
            return normalized
        if os.path.isfile(normalized):
            return os.path.dirname(normalized)
        return normalized


class PresetChipGroup(QWidget):
    def __init__(self, title: str, preset_names: list[str], default_name: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(title)
        label.setStyleSheet("font-weight: bold; color: #ddd;")
        layout.addWidget(label)

        container = QWidget()
        self._flow_layout = FlowLayout(container, margin=0, h_spacing=6, v_spacing=6)
        layout.addWidget(container)

        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)

        for preset_name in preset_names:
            button = QPushButton(preset_name)
            button.setCheckable(True)
            button.setStyleSheet(PRESET_BUTTON_STYLE)
            self._button_group.addButton(button)
            self._flow_layout.addWidget(button)
            if preset_name == default_name:
                button.setChecked(True)

    def current_value(self) -> str:
        checked = self._button_group.checkedButton()
        if checked is None:
            raise RuntimeError("No preset selected.")
        return checked.text()


class ExrClipSlot(QWidget):
    def __init__(self, set_path_callback, browse_callback, clear_callback, remove_callback, parent=None):
        super().__init__(parent)
        self._set_path_callback = set_path_callback
        self._browse_callback = browse_callback
        self._clear_callback = clear_callback
        self._remove_callback = remove_callback
        self.sequence_info: Optional[SequenceInfo] = None
        self.slot_number = 1
        self.setMinimumWidth(320)
        self.setMaximumWidth(420)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.title_label = QLabel("Clip 1")
        self.title_label.setStyleSheet("font-weight: bold; color: #ddd;")
        layout.addWidget(self.title_label)

        self.drop_label = PathDropLabel(
            "🎞️ Clip 1\nDrag EXR folder here",
            "exr_folder",
        )
        self.drop_label.setMinimumHeight(72)
        self.drop_label.pathDropped.connect(lambda path: self._set_path_callback(self, path))
        layout.addWidget(self.drop_label)

        self.path_line_edit = QLineEdit()
        self.path_line_edit.setPlaceholderText("EXR folder path...")
        self.path_line_edit.editingFinished.connect(
            lambda: self._set_path_callback(self, self.path_line_edit.text())
        )
        layout.addWidget(self.path_line_edit)

        self.info_label = QLabel("No clip selected.")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #9aa;")
        layout.addWidget(self.info_label)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Focal length (mm):"))

        self.focal_length_spin = QDoubleSpinBox()
        self.focal_length_spin.setRange(0.001, 1000.0)
        self.focal_length_spin.setDecimals(3)
        self.focal_length_spin.setValue(DEFAULT_FOCAL_LENGTH_MM)
        controls_row.addWidget(self.focal_length_spin)

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(lambda: self._browse_callback(self))
        controls_row.addWidget(browse_button)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(lambda: self._clear_callback(self))
        controls_row.addWidget(self.clear_button)

        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(lambda: self._remove_callback(self))
        controls_row.addWidget(self.remove_button)

        controls_row.addStretch(1)
        layout.addLayout(controls_row)

        self.set_slot_number(1, removable=False)
        self.show_empty()

    def set_slot_number(self, slot_number: int, *, removable: bool) -> None:
        self.slot_number = slot_number
        self.title_label.setText(f"Clip {slot_number}")
        self.drop_label.default_text = f"🎞️ Clip {slot_number}\nDrag EXR folder here"
        if not self.current_path():
            self.drop_label.show_empty()
        self.remove_button.setEnabled(removable)

    def set_path_text(self, path: str) -> None:
        self.path_line_edit.blockSignals(True)
        self.path_line_edit.setText(path)
        self.path_line_edit.blockSignals(False)

    def current_path(self) -> str:
        return self.path_line_edit.text().strip()

    def show_empty(self, *, reset_focal: bool = False) -> None:
        self.sequence_info = None
        self.set_path_text("")
        if reset_focal:
            self.focal_length_spin.setValue(DEFAULT_FOCAL_LENGTH_MM)
        self.drop_label.show_empty()
        self.info_label.setText("No clip selected.")
        self.info_label.setStyleSheet("color: #9aa;")
        self.clear_button.setEnabled(False)

    def show_invalid(self, folder_label: str, details: str) -> None:
        self.sequence_info = None
        self.drop_label.show_invalid(f"⚠ {folder_label}\nInvalid EXR clip")
        self.info_label.setText(details)
        self.info_label.setStyleSheet("color: #f99;")
        self.clear_button.setEnabled(bool(self.current_path()))

    def show_valid(self, sequence_info: SequenceInfo) -> None:
        self.sequence_info = sequence_info
        self.drop_label.show_valid(f"✅ {Path(sequence_info.folder_path).name}")
        self.info_label.setText(
            f"{sequence_info.display_pattern} | "
            f"{sequence_info.first_frame}-{sequence_info.last_frame} | "
            f"{sequence_info.width}x{sequence_info.height}"
        )
        self.info_label.setStyleSheet("color: #8f8;")
        self.clear_button.setEnabled(True)


class Create3DEProjectWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.clip_slots: list[ExrClipSlot] = []
        self._last_auto_shot_name = ""

        self.setWindowTitle("Create 3DE Project")
        self.resize(1040, 900)
        self._build_ui()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        intro = QLabel(
            "Add one or more EXR sequence folders, pick a shared camera preset, set focal length per clip, then build a 3DE project ready for tracking."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #bbb;")
        main_layout.addWidget(intro)

        instructions = QLabel(
            "Each clip becomes its own 3DE sequence camera. The camera preset is shared across all clips. Lens names use the focal length."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #9aa;")
        main_layout.addWidget(instructions)

        clip_header_row = QHBoxLayout()
        clip_header_label = QLabel("Clips")
        clip_header_label.setStyleSheet("font-weight: bold; color: #ddd;")
        clip_header_row.addWidget(clip_header_label)
        clip_header_row.addStretch(1)
        main_layout.addLayout(clip_header_row)

        self.clip_slots_container = QWidget()
        self.clip_slots_layout = FlowLayout(
            self.clip_slots_container,
            margin=0,
            h_spacing=12,
            v_spacing=12,
        )
        main_layout.addWidget(self.clip_slots_container)

        for _ in range(INITIAL_EXR_CLIP_SLOTS):
            self._add_clip_slot()

        self.output_drop_label = PathDropLabel(
            "📁 Drag and drop project root here\n(default save path is shots/{shot}/matchmove/work/... and creates matchmove/export)",
            "directory",
        )
        self.output_drop_label.pathDropped.connect(self._set_output_root_path)
        main_layout.addWidget(self.output_drop_label)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Output location:"))
        self.output_root_edit = QLineEdit()
        self.output_root_edit.setPlaceholderText("Project root path...")
        self.output_root_edit.textChanged.connect(self._refresh_output_root_visual)
        output_row.addWidget(self.output_root_edit)
        output_browse = QPushButton("Browse")
        output_browse.clicked.connect(self._browse_output_root)
        output_row.addWidget(output_browse)
        main_layout.addLayout(output_row)

        details_row = QHBoxLayout()
        details_row.addWidget(QLabel("Shot:"))
        self.shot_name_edit = QLineEdit()
        self.shot_name_edit.setMaxLength(SHOT_NAME_MAX_LENGTH)
        self.shot_name_edit.setPlaceholderText("sho010")
        details_row.addWidget(self.shot_name_edit)

        details_row.addWidget(QLabel("Project name:"))
        self.project_name_edit = QLineEdit()
        self.project_name_edit.setPlaceholderText("Optional metadata only")
        details_row.addWidget(self.project_name_edit)
        main_layout.addLayout(details_row)

        self.camera_group = PresetChipGroup(
            "Camera preset",
            list(CAMERA_PRESETS.keys()),
            next(iter(CAMERA_PRESETS)),
        )
        main_layout.addWidget(self.camera_group)

        numeric_row = QHBoxLayout()
        numeric_row.addWidget(QLabel("FPS:"))
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(0.001, 1000.0)
        self.fps_spin.setDecimals(3)
        self.fps_spin.setValue(DEFAULT_FPS)
        numeric_row.addWidget(self.fps_spin)
        numeric_row.addStretch(1)
        main_layout.addLayout(numeric_row)

        override_row = QHBoxLayout()
        override_row.addWidget(QLabel("Frame override start:"))
        self.frame_start_edit = QLineEdit()
        self.frame_start_edit.setValidator(QIntValidator())
        self.frame_start_edit.setPlaceholderText("Auto")
        override_row.addWidget(self.frame_start_edit)

        override_row.addWidget(QLabel("end:"))
        self.frame_end_edit = QLineEdit()
        self.frame_end_edit.setValidator(QIntValidator())
        self.frame_end_edit.setPlaceholderText("Auto")
        override_row.addWidget(self.frame_end_edit)
        override_row.addStretch(1)
        main_layout.addLayout(override_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.create_button = QPushButton("Create 3DE Project")
        self.create_button.setStyleSheet(
            "background-color: #f05a20; color: white; font-weight: bold; padding: 8px 16px;"
        )
        self.create_button.clicked.connect(self._create_project)
        button_row.addWidget(self.create_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_row.addWidget(close_button)
        main_layout.addLayout(button_row)

        log_label = QLabel("Log")
        log_label.setStyleSheet("font-weight: bold; color: #ddd;")
        main_layout.addWidget(log_label)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMinimumHeight(220)
        main_layout.addWidget(self.log_output)

    def log_message(self, message: str) -> None:
        self.log_output.appendPlainText(message)
        QApplication.processEvents()

    def _add_clip_slot(self) -> None:
        slot = ExrClipSlot(
            set_path_callback=self._set_clip_folder_path,
            browse_callback=self._browse_clip_folder,
            clear_callback=self._clear_clip_slot,
            remove_callback=self._remove_clip_slot,
            parent=self,
        )
        self.clip_slots_layout.addWidget(slot)
        self.clip_slots.append(slot)
        self.clip_slots_container.updateGeometry()
        self._refresh_clip_slot_numbers()

    def _capture_slot_state(self, slot: ExrClipSlot) -> Optional[dict]:
        path = slot.current_path()
        if not path:
            return None
        return {
            "path": path,
            "sequence_info": slot.sequence_info,
            "focal_length_mm": float(slot.focal_length_spin.value()),
            "info_text": slot.info_label.text(),
        }

    def _capture_non_empty_slot_states(self) -> list[dict]:
        states = []
        for slot in self.clip_slots:
            state = self._capture_slot_state(slot)
            if state is not None:
                states.append(state)
        return states[:MAX_EXR_CLIPS]

    def _apply_slot_state(self, slot: ExrClipSlot, state: Optional[dict]) -> None:
        if state is None:
            slot.show_empty(reset_focal=True)
            return

        slot.focal_length_spin.setValue(float(state["focal_length_mm"]))
        slot.set_path_text(str(state["path"]))
        sequence_info = state["sequence_info"]
        if sequence_info is not None:
            slot.show_valid(sequence_info)
        else:
            slot.show_invalid(Path(str(state["path"])).name or str(state["path"]), str(state["info_text"]))

    def _sync_clip_slots_to_state(self, states: list[dict]) -> None:
        target_slots = min(
            MAX_EXR_CLIPS,
            max(1, len(states) + (0 if len(states) >= MAX_EXR_CLIPS else 1)),
        )

        while len(self.clip_slots) < target_slots:
            self._add_clip_slot()
        while len(self.clip_slots) > target_slots:
            slot = self.clip_slots.pop()
            self.clip_slots_layout.removeWidget(slot)
            slot.deleteLater()

        for index, slot in enumerate(self.clip_slots):
            state = states[index] if index < len(states) else None
            self._apply_slot_state(slot, state)

        self.clip_slots_container.updateGeometry()
        self._refresh_clip_slot_numbers()

    def _compact_clip_slots(self) -> None:
        self._sync_clip_slots_to_state(self._capture_non_empty_slot_states())

    def _remove_clip_slot(self, slot: ExrClipSlot) -> None:
        if slot not in self.clip_slots:
            return
        slot.show_empty(reset_focal=True)
        self._compact_clip_slots()

    def _refresh_clip_slot_numbers(self) -> None:
        removable = len(self.clip_slots) > 1
        for slot_number, slot in enumerate(self.clip_slots, start=1):
            slot.set_slot_number(slot_number, removable=removable)

    def _slot_index(self, slot: ExrClipSlot) -> int:
        return self.clip_slots.index(slot)

    def _browse_clip_folder(self, slot: ExrClipSlot) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select EXR Folder", "")
        if selected:
            self._set_clip_folder_path(slot, selected)

    def _browse_output_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select Project Root", "")
        if selected:
            self._set_output_root_path(selected)

    def _coerce_clip_input_path(self, path: str) -> str:
        normalized = normalize_user_path(path)
        if os.path.isfile(normalized) and normalized.lower().endswith(".exr"):
            return os.path.dirname(normalized)
        return normalized

    def _set_clip_folder_path(self, slot: ExrClipSlot, path: str, *, log_errors: bool = True) -> Optional[SequenceInfo]:
        index = self._slot_index(slot)
        raw_path = path.strip()
        if not raw_path:
            self._clear_clip_slot(slot)
            return None

        normalized = self._coerce_clip_input_path(raw_path)
        slot.set_path_text(normalized)

        try:
            detected = shared_matchmove.detect_exr_sequence(normalized)
        except Exception as error:
            slot.show_invalid(Path(normalized).name or normalized, str(error))
            if log_errors:
                self.log_message(f"Clip {index + 1}: {error}")
            return None

        slot.show_valid(detected)
        if index == 0:
            self._auto_fill_shot_name(detected.folder_path)

        self._compact_clip_slots()

        self.log_message(
            f"Clip {index + 1}: detected {detected.display_pattern} "
            f"({detected.first_frame}-{detected.last_frame}, "
            f"{detected.width}x{detected.height})"
        )
        return detected

    def _clear_clip_slot(self, slot: ExrClipSlot) -> None:
        slot.show_empty(reset_focal=True)
        self._compact_clip_slots()

    def _set_output_root_path(self, path: str) -> None:
        normalized = normalize_user_path(path) if path.strip() else ""
        self.output_root_edit.setText(normalized)
        self._refresh_output_root_visual()

    def _refresh_output_root_visual(self) -> None:
        text = self.output_root_edit.text().strip()
        if not text:
            self.output_drop_label.show_empty()
            return

        normalized = normalize_user_path(text)
        if os.path.isfile(normalized):
            self.output_drop_label.show_invalid(f"⚠ {Path(normalized).name}\nOutput root cannot be a file")
            return

        folder_name = Path(normalized).name or normalized
        if os.path.isdir(normalized):
            self.output_drop_label.show_valid(f"✅ {folder_name}")
        else:
            self.output_drop_label.show_valid(f"✅ {folder_name}\n(directory will be created if needed)")

    def _auto_fill_shot_name(self, folder_path: str) -> None:
        guessed_name = sanitize_auto_shot_name(Path(folder_path).name)
        current_value = self.shot_name_edit.text().strip()
        if not current_value or current_value == self._last_auto_shot_name:
            self.shot_name_edit.setText(guessed_name)
            self._last_auto_shot_name = guessed_name

    def _build_request(self) -> ProjectBuildRequest:
        output_root = self.output_root_edit.text().strip()
        if not output_root:
            raise ValueError("Output location is required.")

        shot_name = validate_shot_name(self.shot_name_edit.text())
        camera_preset_name = self.camera_group.current_value()

        clip_requests: list[ClipBuildRequest] = []
        used_camera_names: set[str] = set()

        for index, slot in enumerate(self.clip_slots):
            clip_path = slot.current_path()
            if not clip_path:
                continue

            normalized_clip_path = self._coerce_clip_input_path(clip_path)
            sequence_info = slot.sequence_info
            if (
                not sequence_info
                or normalize_user_path(sequence_info.folder_path) != normalize_user_path(normalized_clip_path)
            ):
                sequence_info = self._set_clip_folder_path(slot, normalized_clip_path, log_errors=False)
            if not sequence_info:
                raise ValueError(f"Clip {index + 1} is not a valid EXR sequence.")

            sequence_start_frame, sequence_end_frame = resolve_requested_frame_range(
                sequence_info,
                self.frame_start_edit.text(),
                self.frame_end_edit.text(),
            )
            focal_length_mm = float(slot.focal_length_spin.value())
            clip_requests.append(
                ClipBuildRequest(
                    slot_index=index,
                    clip_name=Path(sequence_info.folder_path).name,
                    camera_name=shared_matchmove.build_unique_camera_name(
                        shot_name,
                        sequence_info.folder_path,
                        index,
                        used_camera_names,
                    ),
                    lens_name=shared_matchmove.format_focal_length_label(focal_length_mm),
                    sequence_info=sequence_info,
                    focal_length_mm=focal_length_mm,
                    sequence_start_frame=sequence_start_frame,
                    sequence_end_frame=sequence_end_frame,
                )
            )

        if not clip_requests:
            raise ValueError("At least one EXR clip is required.")

        matchmove_dir, project_dir, export_dir, project_path, version = resolve_project_path(output_root, shot_name)

        return ProjectBuildRequest(
            project_root=normalize_user_path(output_root),
            project_name=self.project_name_edit.text().strip(),
            shot_name=shot_name,
            clips=tuple(clip_requests),
            camera_preset_name=camera_preset_name,
            matchmove_dir=matchmove_dir,
            export_dir=export_dir,
            fps=float(self.fps_spin.value()),
            project_dir=project_dir,
            project_path=project_path,
            version=version,
        )

    def _create_project(self) -> None:
        try:
            request = self._build_request()
        except Exception as error:
            self.log_message(str(error))
            QMessageBox.warning(self, "Validation Error", str(error))
            return

        self.create_button.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        headless_result: Optional[HeadlessRunResult] = None

        try:
            self.log_message(
                "Creating project "
                f"{Path(request.project_path).name} "
                f"with {request.clip_count} clip(s)"
            )
            self.log_message(f"Project root: {request.project_root}")
            self.log_message(f"Matchmove folder: {request.matchmove_dir}")
            self.log_message(f"Work folder: {request.project_dir}")
            self.log_message(f"Export folder: {request.export_dir}")
            self.log_message(f"Resolved save path: {request.project_path}")
            self.log_message(f"Camera preset: {request.camera_preset_name}")
            self.log_message(
                f"8-bit conversion: gamma {DEFAULT_8BIT_COLOR_GAMMA:g} | "
                f"softclip {DEFAULT_8BIT_COLOR_SOFTCLIP:g}"
            )
            for clip in request.clips:
                self.log_message(
                    f"Clip {clip.slot_index + 1}: {clip.camera_name} | "
                    f"focal {clip.lens_name} | "
                    f"{clip.sequence_start_frame}-{clip.sequence_end_frame} | "
                    f"{clip.sequence_info.width}x{clip.sequence_info.height} | "
                    f"pixel aspect {clip.sequence_info.header_pixel_aspect:g}"
                )

            Path(request.project_dir).mkdir(parents=True, exist_ok=True)
            Path(request.export_dir).mkdir(parents=True, exist_ok=True)
            headless_result = shared_matchmove.run_headless_3de(request, self.log_message)
            self.log_message(f"Saved 3DE project: {request.project_path}")

            try:
                gui_command = shared_matchmove.open_3de_project(request.project_path)
            except Exception as error:
                launch_text = shlex.join(
                    [shared_matchmove.resolve_3de_executable(must_exist=False), "-open", request.project_path]
                )
                self.log_message(
                    "3DE project was created, but opening the GUI failed.\n"
                    f"Project: {request.project_path}\n"
                    f"Launch command: {launch_text}\n"
                    f"Error: {error}"
                )
                QMessageBox.warning(
                    self,
                    "3DE GUI Launch Failed",
                    "The project was created, but 3DE could not be opened automatically.\n\n"
                    f"Project: {request.project_path}",
                )
            else:
                self.log_message(f"Opened 3DE GUI: {shlex.join(gui_command)}")
                QMessageBox.information(
                    self,
                    "Project Created",
                    f"3DE project created:\n{request.project_path}",
                )

            if headless_result is not None:
                shared_matchmove.cleanup_headless_artifacts(headless_result)
        except Exception as error:
            self.log_message(str(error))
            QMessageBox.critical(self, "3DE Creation Failed", str(error))
        finally:
            QApplication.restoreOverrideCursor()
            self.create_button.setEnabled(True)


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main() -> int:
    app = QApplication(sys.argv)
    window = Create3DEProjectWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
