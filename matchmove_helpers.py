from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


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

THREEDE_CMD_ENV_VAR = "SHOTBOX_3DE_CMD"
THREEDE_CMD = os.environ.get(THREEDE_CMD_ENV_VAR, "").strip()

WINDOWS_THREEDE_CANDIDATES = (
    r"C:\Users\Giger\Documents\3DE4_win64_r8.0v2\bin\3DE4.exe",
    r"C:\Users\Giger\Documents\3DE4_win64_r8.0v2\3DE4.exe",
    r"%ProgramFiles%\3DE4\bin\3DE4.exe",
    r"%ProgramFiles%\3DEqualizer4\bin\3DE4.exe",
    r"%ProgramFiles(x86)%\3DE4\bin\3DE4.exe",
    r"%ProgramFiles(x86)%\3DEqualizer4\bin\3DE4.exe",
    "3DE4.exe",
)

POSIX_THREEDE_CANDIDATES = (
    "/home/rockybtw/Documents/3DE4_linux64_r8.1/bin/run_3DE4",
    "/opt/3DE4/bin/run_3DE4",
    "/usr/local/bin/run_3DE4",
    "/Applications/3DE4.app/Contents/MacOS/3DE4",
    "/Applications/3DEqualizer4.app/Contents/MacOS/3DE4",
    "run_3DE4",
    "3DE4",
)

SHOT_ASSETS_DIRNAME = "Shot_Assets"
MATCHMOVE_DIRNAME = "matchmove"
MATCHMOVE_WORK_DIRNAME = "work"
MATCHMOVE_EXPORT_DIRNAME = "export"
PROJECT_FILENAME_RE = r"^{shot}_matchmove_v(\d{{3}})\.3de$"
GENERIC_PROJECT_FILENAME_RE = re.compile(
    r"^(?P<base>.+)_matchmove_v(?P<version>\d{3})\.3de$",
    re.IGNORECASE,
)

DEFAULT_FPS = 25.0
DEFAULT_FOCAL_LENGTH_MM = 35.0
DEFAULT_8BIT_COLOR_GAMMA = 2.2
DEFAULT_8BIT_COLOR_SOFTCLIP = 1.0


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
class MatchmoveClipRequest:
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
class MatchmoveProjectRequest:
    project_name: str
    shot_name: str
    clips: tuple[MatchmoveClipRequest, ...]
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


def normalize_user_path(path: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(path.strip())))


def _is_path_like(command: str) -> bool:
    if not command:
        return False
    return (
        "/" in command
        or "\\" in command
        or command.startswith(".")
        or (len(command) > 1 and command[1] == ":")
    )


def _read_configured_3de_path() -> str:
    try:
        from settings import get_settings_manager

        settings = get_settings_manager()
        return str(settings.get("threede_exe_path", "") or "").strip()
    except Exception:
        return ""


def _iter_3de_candidates() -> list[str]:
    candidates: list[str] = []
    if THREEDE_CMD:
        candidates.append(THREEDE_CMD)
    configured_3de_path = _read_configured_3de_path()
    if configured_3de_path:
        candidates.append(configured_3de_path)

    if os.name == "nt":
        candidates.extend(WINDOWS_THREEDE_CANDIDATES)
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(env_name, "").strip()
            if not root:
                continue
            try:
                for child in sorted(Path(root).glob("3DE*")):
                    candidates.append(str(child / "bin" / "3DE4.exe"))
            except OSError:
                continue
    else:
        candidates.extend(POSIX_THREEDE_CANDIDATES)

    unique_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = str(candidate or "").strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(cleaned)
    return unique_candidates


def resolve_3de_executable(must_exist: bool = True) -> str:
    tried_paths: list[str] = []
    fallback_candidate = ""

    for candidate in _iter_3de_candidates():
        if _is_path_like(candidate):
            resolved = normalize_user_path(candidate)
            tried_paths.append(resolved)
            if os.path.isfile(resolved):
                return resolved
            if not fallback_candidate:
                fallback_candidate = resolved
            continue

        resolved = shutil.which(candidate)
        tried_paths.append(resolved or candidate)
        if resolved:
            return resolved
        if not fallback_candidate:
            fallback_candidate = candidate

    if not must_exist:
        return fallback_candidate

    searched_text = "\n- ".join(tried_paths or ["3DE4.exe", "run_3DE4"])
    raise FileNotFoundError(
        "Could not locate the 3DE executable.\n"
        f"Set {THREEDE_CMD_ENV_VAR} or add 3DE to PATH.\n"
        f"Searched:\n- {searched_text}"
    )


def _background_launch_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
        if creationflags:
            kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    return kwargs


def sanitize_auto_shot_name(name: str, max_length: int = 50) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    cleaned = cleaned.strip("_")
    return cleaned[:max_length] or "shot"


def validate_shot_name(name: str, max_length: int = 50) -> str:
    shot_name = name.strip()
    if not shot_name:
        raise ValueError("Shot name is required.")
    if len(shot_name) > max_length:
        raise ValueError(f"Shot names must be {max_length} characters or fewer.")
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


def build_shot_assets_directory(shot_root: str) -> str:
    return os.path.join(normalize_user_path(shot_root), SHOT_ASSETS_DIRNAME)


def build_shot_matchmove_directory(shot_root: str) -> str:
    return os.path.join(build_shot_assets_directory(shot_root), MATCHMOVE_DIRNAME)


def build_work_directory(matchmove_dir: str) -> str:
    return os.path.join(normalize_user_path(matchmove_dir), MATCHMOVE_WORK_DIRNAME)


def build_export_directory(matchmove_dir: str) -> str:
    return os.path.join(normalize_user_path(matchmove_dir), MATCHMOVE_EXPORT_DIRNAME)


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


def resolve_project_path(matchmove_dir: str, shot_name: str) -> tuple[str, str, str, int]:
    normalized_matchmove_dir = normalize_user_path(matchmove_dir)
    project_dir = build_work_directory(normalized_matchmove_dir)
    export_dir = build_export_directory(normalized_matchmove_dir)
    version = get_next_project_version(project_dir, shot_name)
    filename = f"{shot_name}_matchmove_v{version:03d}.3de"
    project_path = os.path.join(project_dir, filename)
    return project_dir, export_dir, project_path, version


def find_latest_matchmove_project(matchmove_dir: str, shot_name: str) -> Optional[Path]:
    work_dir = Path(build_work_directory(matchmove_dir))
    if not work_dir.is_dir():
        return None

    version_pattern = re.compile(
        PROJECT_FILENAME_RE.format(shot=re.escape(shot_name)),
        re.IGNORECASE,
    )
    best_path: Optional[Path] = None
    best_version = -1
    for path in work_dir.iterdir():
        if not path.is_file():
            continue
        match = version_pattern.match(path.name)
        if not match:
            continue
        version = int(match.group(1))
        if version > best_version:
            best_version = version
            best_path = path
    return best_path


def list_matchmove_projects(matchmove_dir: str) -> list[Path]:
    work_dir = Path(build_work_directory(matchmove_dir))
    if not work_dir.is_dir():
        return []

    projects = [
        path
        for path in work_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".3de"
    ]

    def _sort_key(path: Path):
        match = GENERIC_PROJECT_FILENAME_RE.match(path.name)
        if match:
            return (
                0,
                match.group("base").lower(),
                -int(match.group("version")),
                path.name.lower(),
            )
        return (1, path.name.lower())

    return sorted(projects, key=_sort_key)


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


def list_valid_precomp_sequences(shot_root: str) -> list[SequenceInfo]:
    precomp_dir = Path(normalize_user_path(shot_root)) / "renders" / "precomp"
    if not precomp_dir.is_dir():
        return []

    sequences: list[SequenceInfo] = []
    for child in sorted(precomp_dir.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir():
            continue
        if child.name.lower() == "previews":
            continue
        try:
            sequences.append(detect_exr_sequence(str(child)))
        except Exception:
            continue
    return sequences


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


def build_project_notes(request: MatchmoveProjectRequest) -> str:
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
    request: MatchmoveProjectRequest,
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
                "sequence_start": clip.sequence_start_frame,
                "sequence_end": clip.sequence_end_frame,
                "frame_count": clip.internal_frame_count,
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
        tde4.setCameraFPS(camera, CONFIG["fps"])
        tde4.setCameraPlaybackRange(camera, clip["sequence_start"], clip["sequence_end"])
        tde4.setCameraCalculationRange(camera, clip["sequence_start"], clip["sequence_end"])
        tde4.setCameraFrameRangeCalculationFlag(camera, 1)
        tde4.setCurrentFrame(camera, clip["sequence_start"])

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

    if created_cameras:
        tde4.setCurrentCamera(created_cameras[0])
        tde4.setCurrentFrame(created_cameras[0], CONFIG["clips"][0]["sequence_start"])

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
    request: MatchmoveProjectRequest,
    log_callback: Callable[[str], None],
) -> HeadlessRunResult:
    three_de_path = resolve_3de_executable()
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
    three_de_path = resolve_3de_executable()
    command = [three_de_path, "-open", project_path]
    subprocess.Popen(command, **_background_launch_kwargs())
    return command
