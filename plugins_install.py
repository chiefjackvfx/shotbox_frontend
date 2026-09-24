"""Install ShotBox's bundled plugins into an existing 3DE installation."""

from dataclasses import dataclass, field
import os
import re
from pathlib import Path
import shutil
import tempfile


@dataclass
class InstallResult:
    destination: Path
    installed: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


def bundled_scripts() -> list[Path]:
    source = Path(__file__).resolve().parent / "plugins" / "3de"
    if not source.is_dir():
        raise FileNotFoundError(f"Bundled 3DE plugins folder is missing: {source}")
    scripts = sorted(path for path in source.glob("*.py") if path.is_file())
    if not scripts:
        raise FileNotFoundError(f"No bundled 3DE plugins found in: {source}")
    return scripts


def resolve_destination(executable: str) -> Path:
    value = os.path.expanduser(os.path.expandvars(executable.strip()))
    if not value:
        raise ValueError("Select the 3DE Executable above to install plugins.")
    # Bare commands use PATH; explicit paths must point to a file.
    if os.path.basename(value) == value:
        value = shutil.which(value) or ""
    if not value or not Path(value).is_file():
        raise FileNotFoundError("3DE executable not found. Correct the 3DE Executable setting above.")
    resolved = Path(value).resolve()
    candidates = [root / "sys_data" / "py_scripts" for root in (resolved.parent, resolved.parent.parent)]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "Cannot locate 3DE's scripts folder. Select its executable above. Expected: "
        + " or ".join(str(path) for path in candidates)
    )


def install_3de_plugins(executable: str) -> InstallResult:
    destination = resolve_destination(executable)
    scripts = bundled_scripts()
    result = InstallResult(destination)
    return _install_files([(source, destination / source.name) for source in scripts], result)


def _install_files(files, result):
    for source, target in files:
        name = str(target.relative_to(result.destination))
        temporary = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            exists = target.exists()
            if exists and target.read_bytes() == data:
                result.unchanged.append(name)
                continue
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".shotbox-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            # Avoid leaving the replacement readable only by the installing user.
            shutil.copymode(target if exists else source, temporary)
            os.replace(temporary, target)
            temporary = None
            (result.updated if exists else result.installed).append(name)
        except OSError as exc:
            result.failures[name] = str(exc)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    result.failures[name] = result.failures.get(name, "") + f"; temporary file cleanup failed: {exc}"
    return result


def houdini_preferences_candidates() -> list[str]:
    home = Path.home()
    roots = (home, home / "Documents", home / "Library" / "Preferences" / "houdini")
    candidates = set()
    configured = os.environ.get("HOUDINI_USER_PREF_DIR", "")
    if configured and Path(os.path.expandvars(os.path.expanduser(configured))).is_dir():
        candidates.add(str(Path(os.path.expandvars(os.path.expanduser(configured))).resolve()))
    for root in roots:
        if root.is_dir():
            for child in root.iterdir():
                if re.fullmatch(r"houdini\d+\.\d+", child.name) and child.is_dir():
                    candidates.add(str(child.resolve()))
    return sorted(candidates, reverse=True)


def resolve_houdini_preferences(folder: str) -> Path:
    if not folder.strip():
        raise ValueError("Select a Houdini user preferences folder (for example houdini22.0).")
    path = Path(os.path.expandvars(os.path.expanduser(folder.strip()))).resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Houdini preferences folder does not exist: {path}")
    return path


def install_houdini_plugin(folder: str) -> InstallResult:
    destination = resolve_houdini_preferences(folder)
    source = Path(__file__).resolve().parent / "plugins" / "houdini"
    runtime = ("python/shotbox_3de_import.py", "toolbar/shotbox_3de_import.shelf")
    package = source / "shotbox_3de_import.json"
    for path in [source / name for name in runtime] + [package]:
        if not path.is_file():
            raise FileNotFoundError(f"Bundled Houdini plugin file missing: {path}")
    result = InstallResult(destination)
    _install_files([(source / name, destination / "shotbox_3de_import" / name) for name in runtime], result)
    if not result.failures:
        _install_files([(package, destination / "packages" / package.name)], result)
    return result
