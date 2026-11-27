"""Filesystem and executable helpers for job and shot CG assets."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import app_cache
from asset_paths import asset_path


START_FRAME = 1001


def validate_asset_name(value: str) -> str:
    name = value.strip()
    if (not name or name in (".", "..") or name.endswith((".", " "))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.split(".")[0].upper() in {
                "CON", "PRN", "AUX", "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }):
        raise ValueError("Enter a valid asset folder name without path separators or reserved characters.")
    return name


def resolve_cg_root(parent: Path, assets_kind: str) -> Path:
    """Resolve a job/shot CG directory, including the legacy folder spelling."""
    return asset_path(parent, assets_kind, "cg")


def asset_destination(cg_root: Path, name: str) -> Path:
    return Path(cg_root) / validate_asset_name(name)


def validate_new_destination(destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"This asset already exists. Choose a new name:\n{destination}")
    # A case-only duplicate must also be rejected on shared Windows storage.
    if destination.parent.is_dir() and any(
        child.name.casefold() == destination.name.casefold() for child in destination.parent.iterdir()
    ):
        raise FileExistsError(f"An asset with this name already exists:\n{destination}")


def resolve_houdini(configured: str = "") -> tuple[Path, Path]:
    """Resolve Houdini and hython from one installation; explicit settings win."""
    executable = "houdini.exe" if os.name == "nt" else "houdini"
    worker_executable = "hython.exe" if os.name == "nt" else "hython"
    candidates = [configured] if configured.strip() else []
    configured_problem = ""
    if not candidates:
        if os.environ.get("HFS"):
            candidates.append(str(Path(os.environ["HFS"]) / "bin" / executable))
        candidates.append(executable)
    for value in candidates:
        expanded = os.path.expandvars(os.path.expanduser(value.strip()))
        found = shutil.which(expanded)
        if not found:
            configured_problem = f"Could not run the configured Houdini executable:\n{expanded}"
            continue
        houdini = Path(found).resolve()
        hython = houdini.with_name(worker_executable)
        if hython.is_file() and os.access(hython, os.X_OK):
            return houdini, hython
        configured_problem = f"Could not run the matching hython executable:\n{hython}"
    if configured.strip():
        raise FileNotFoundError(
            f"{configured_problem}\n\nUpdate Houdini Executable in Settings "
            "to point to an installed Houdini with its matching hython."
        )
    raise FileNotFoundError(
        "Could not locate Houdini and its matching hython. "
        "Set Houdini Executable in Settings, set HFS, or add Houdini to PATH."
    )


def worker_environment(hython: Path, work_dir: Path) -> dict[str, str]:
    """Use only factory Houdini paths, excluding personal startup UI and packages."""
    root = hython.parent.parent
    env = dict(os.environ)
    for key in list(env):
        if (key.startswith("HOUDINI_") and key not in {
            "HOUDINI_LICENSE_SERVER", "HOUDINI_SCRIPT_LICENSE", "HOUDINI_LMINFO_VERBOSE",
        }) or key in {"PYTHONPATH", "PYTHONHOME", "HHP", "HH", "HSITE", "HFS"}:
            env.pop(key, None)
    # Houdini 22 ignores overrides without this version-expansion token.
    prefs = work_dir / "preferences__HVER__"
    packages = work_dir / "packages"
    packages.mkdir(exist_ok=True)
    env.update({
        "HFS": str(root),
        "HOUDINI_USER_PREF_DIR": str(prefs),
        "HOUDINI_NO_ENV_FILE": "1",
        "HOUDINI_PATH": (root / "houdini").as_posix(),
        "HOUDINI_SCRIPT_PATH": (root / "houdini" / "scripts").as_posix(),
        "HOUDINI_PACKAGE_DIR": str(packages),
        "HOUDINI_PACKAGE_SKIP": "1",
        "HOUDINI_TEMP_DIR": str(work_dir),
        "TMPDIR": str(work_dir), "TEMP": str(work_dir), "TMP": str(work_dir),
        "PATH": str(hython.parent) + os.pathsep + env.get("PATH", ""),
    })
    if os.name != "nt":
        env["LD_LIBRARY_PATH"] = str(root / "dsolib") + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    return env


def discover_camera_exports(job: dict, job_assets: Path, convert_path=lambda path: path,
                            job_matchmove: Path | None = None, *, include_blender=False) -> list[Path]:
    """Inspect known matchmove export directories, never execute their contents."""
    configured_job = job.get("matchmove_path")
    roots = [job_matchmove or (Path(convert_path(configured_job)) if configured_job
             else asset_path(job_assets.parent, "job_assets", "matchmove"))]
    vfx = job_assets.parent
    for timeline in job.get("timelines", []) or []:
        shots = timeline.get("shots", []) or []
        shot_roots = [Path(convert_path(shot["base_path"])) for shot in shots if shot.get("base_path")]
        timeline_root = shot_roots[0].parent if shot_roots else vfx / str(timeline.get("title") or "")
        configured = timeline.get("matchmove_path")
        if configured:
            roots.append(Path(convert_path(configured)))
        elif timeline_root != vfx:
            roots.append(asset_path(timeline_root, "timeline_assets", "matchmove"))
        for shot in shots:
            if shot.get("matchmove_path"):
                roots.append(Path(convert_path(shot["matchmove_path"])))
            elif shot.get("base_path"):
                roots.append(asset_path(Path(convert_path(shot["base_path"])), "shot_assets", "matchmove"))
    return discover_exports_in_matchmove(roots, include_blender=include_blender)


def discover_exports_in_matchmove(roots: list[Path], *, include_blender=False) -> list[Path]:
    """List exports only in explicitly supplied matchmove folders."""
    suffixes = ("_hou.py", "_blender.py") if include_blender else ("_hou.py",)
    exports = {}
    for root in roots:
        directory = root / "export"
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.is_file() and path.name.lower().endswith(suffixes):
                exports[os.path.normcase(str(path.resolve()))] = path
    return sorted(exports.values(), key=lambda path: (path.name.casefold(), str(path).casefold()))


def source_signature(paths: list[Path]) -> list[tuple[str, int, int]]:
    return [(str(path.resolve()), path.stat().st_size, path.stat().st_mtime_ns) for path in paths]


def longest_duration(summaries: list[dict]) -> int | None:
    if not summaries or any(summary.get("duration") is None for summary in summaries):
        return None
    return max(int(summary["duration"]) for summary in summaries)


def list_cg_projects(cg_root: Path) -> list[Path]:
    directory = Path(cg_root)
    if not directory.is_dir():
        return []
    projects = []
    for folder in sorted(directory.iterdir(), key=lambda path: path.name.casefold()):
        if not folder.is_dir():
            continue
        pattern = re.compile(r"^" + re.escape(folder.name) + r"_v(\d+)\.(?:hip|blend)$", re.IGNORECASE)
        versions = [(int(match.group(1)), path.name, path) for path in folder.iterdir()
                    if path.is_file() and (match := pattern.fullmatch(path.name))]
        if versions:
            projects.append(max(versions)[2])
    return projects


@app_cache.temporary_cache_task
def publish_scene(prepared: Path, cg_root: Path, name: str) -> Path:
    """Publish a saved scene exclusively, rolling back only our own files/directories."""
    destination = asset_destination(cg_root, name)
    validate_new_destination(destination)
    if prepared.suffix not in (".hip", ".blend"):
        raise ValueError("Expected a .hip or .blend scene.")
    if not prepared.is_file() or prepared.stat().st_size == 0:
        raise RuntimeError("The worker did not write a nonempty scene file.")
    created_dirs = []
    output = destination / f"{destination.name}_v001{prepared.suffix}"
    owns_output = False
    try:
        # Record each directory separately; never remove unowned parents.
        missing = []
        parent = destination
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            try:
                directory.mkdir()
                created_dirs.append(directory)
            except FileExistsError:
                if directory == destination or not directory.is_dir():
                    raise
        if destination not in created_dirs:
            raise FileExistsError(f"This asset already exists: {destination}")
        for subfolder in ("geo", "assets"):
            directory = destination / subfolder
            directory.mkdir()
            created_dirs.append(directory)
        with output.open("xb") as stream:
            owns_output = True
            with prepared.open("rb") as source:
                shutil.copyfileobj(source, stream)
            stream.flush()
            os.fsync(stream.fileno())
        return output
    except Exception:
        if owns_output:
            output.unlink(missing_ok=True)
        for directory in reversed(created_dirs):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def resolve_blender(configured: str = "") -> Path:
    value = os.path.expanduser(os.path.expandvars(configured.strip())) or "blender"
    found = shutil.which(value)
    if not found:
        raise FileNotFoundError(f"Could not run Blender ({value}). Set Blender Executable in Settings.")
    return Path(found).resolve()


def scene_application(project: Path) -> str:
    return "Blender" if project.suffix.lower() == ".blend" else "Houdini"


def open_scene(project: Path, configured: str = "") -> None:
    if not project.is_file():
        raise FileNotFoundError(f"This {scene_application(project)} scene is unavailable:\n{project}")
    if scene_application(project) == "Blender":
        executable = resolve_blender(configured)
        env = dict(os.environ)
    else:
        executable, _ = resolve_houdini(configured)
        env = dict(os.environ, JOB=str(project.parent))
    # Passing the hip at startup bypasses personal 123.py (new-scene startup).
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
              "cwd": str(project.parent), "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen([str(executable), str(project.resolve())], **kwargs)
