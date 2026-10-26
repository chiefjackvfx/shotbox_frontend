"""Persistent standard-library preferences; never install or launch a runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .cache import atomic_write_json
from .protocol import ProtocolError

PREFERENCE_KEYS = ("application_root", "interpreter", "model_root", "model_config", "cache_root")


def frontend_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "app_cache.py").is_file() and (parent / "toolbox").is_dir():
            return parent
    raise ProtocolError("Cannot locate Shotbox frontend; reinstall the Nuke integration from Shotbox.")


def application_root() -> Path:
    return frontend_root() / "toolbox" / "shotbox_matte"


def default_cache_root() -> Path:
    return frontend_root() / ".cache" / "temp" / "nuke-matte"


def default_path() -> Path:
    return Path.home() / ".nuke" / "shotbox_preferences.json"


def load(path: str | Path | None = None) -> dict:
    path = Path(path) if path is not None else default_path()
    if not path.exists():
        return {"schema_version": 1, **dict.fromkeys(PREFERENCE_KEYS, "")}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProtocolError(f"Cannot read ShotBox preferences: {exc}") from exc
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ProtocolError("Unsupported ShotBox preferences schema.")
    if any(not isinstance(data.get(key, ""), str) for key in PREFERENCE_KEYS):
        raise ProtocolError("ShotBox preference paths must be strings.")
    return {"schema_version": 1, **{key: data.get(key, "") for key in PREFERENCE_KEYS}}


def save(preferences: dict, path: str | Path | None = None) -> None:
    data = {"schema_version": 1}
    for key in PREFERENCE_KEYS:
        value = preferences.get(key, "")
        if not isinstance(value, str):
            raise ProtocolError("ShotBox preference paths must be strings.")
        data[key] = value
    atomic_write_json(path or default_path(), data)


def interpreter_candidates(application_root: str | Path, *, windows: bool | None = None) -> list[Path]:
    windows = os.name == "nt" if windows is None else windows
    root = Path(application_root).expanduser()
    names = (".venv-windows", ".venv") if windows else (".venv", ".venv-linux")
    suffix = Path("Scripts/python.exe") if windows else Path("bin/python")
    return [root / name / suffix for name in names]


def resolve_interpreter(application_root: str | Path, override: str | Path | None = None,
                        *, windows: bool | None = None) -> Path:
    candidates = [Path(override).expanduser()] if override else interpreter_candidates(application_root, windows=windows)
    for candidate in candidates:
        if candidate.is_file():
            # A venv's executable is commonly a symlink to system Python.
            # Preserve its invoked location so Python discovers pyvenv.cfg.
            return Path(os.path.abspath(candidate))
    raise ProtocolError("ShotBox Python was not found. Run launch.bat --setup-only or ./launch.sh --setup-only, "
                        "then set the application directory or interpreter override.")


def suggested_cache_root(script_path: str | Path | None, preferred: str = "") -> Path | None:
    if preferred:
        return Path(preferred).expanduser().resolve()
    if script_path and str(script_path) not in ("Root", "Untitled"):
        return Path(script_path).expanduser().resolve().parent / ".shotbox-cache"
    return None
