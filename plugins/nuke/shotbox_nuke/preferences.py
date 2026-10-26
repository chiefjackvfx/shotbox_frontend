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


def _legacy_interpreter(value, *application_roots):
    """Recognize the old managed runtime without changing custom overrides."""
    import ntpath

    def normalized(path):
        value = str(path)
        if "\\" in value or ntpath.splitdrive(value)[0]:
            return ntpath.normcase(ntpath.normpath(value))
        return os.path.normcase(os.path.abspath(os.path.expanduser(value)))

    return bool(value) and any(
        normalized(value) == normalized(str(root).rstrip("/\\") + "/" + name + "/" + suffix)
        for root in application_roots if root
        for name in (".venv", ".venv-windows", ".venv-linux")
        for suffix in ("Scripts/python.exe", "bin/python")
    )


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
    values = {"schema_version": 1, **dict.fromkeys(PREFERENCE_KEYS, ""), **data}
    if _legacy_interpreter(values["interpreter"], values["application_root"], application_root()):
        values["interpreter"] = ""
    return values


def save(preferences: dict, path: str | Path | None = None) -> None:
    data = load(path)
    for key in PREFERENCE_KEYS:
        value = preferences.get(key, data.get(key, ""))
        if not isinstance(value, str):
            raise ProtocolError("ShotBox preference paths must be strings.")
        data[key] = value
    if _legacy_interpreter(data["interpreter"], data["application_root"], application_root()):
        data["interpreter"] = ""
    atomic_write_json(path or default_path(), data)


def interpreter_candidates(application_root: str | Path, *, windows: bool | None = None) -> list[Path]:
    windows = os.name == "nt" if windows is None else windows
    root = Path(application_root).expanduser()
    if root.name == "shotbox_matte" and root.parent.name == "toolbox":
        root = root.parents[1]
    suffix = Path("Scripts/python.exe") if windows else Path("bin/python")
    return [root / "venv" / suffix]


def resolve_interpreter(application_root: str | Path, override: str | Path | None = None,
                        *, windows: bool | None = None) -> Path:
    if _legacy_interpreter(override, application_root):
        override = None
    candidates = [Path(override).expanduser()] if override else interpreter_candidates(application_root, windows=windows)
    for candidate in candidates:
        if candidate.is_file():
            # A venv's executable is commonly a symlink to system Python.
            # Preserve its invoked location so Python discovers pyvenv.cfg.
            return Path(os.path.abspath(candidate))
    raise ProtocolError("Shotbox Python was not found. Run the main Shotbox installer or updater, "
                        "then install/update the Nuke integration from Shotbox Settings → Plugins.")


def suggested_cache_root(script_path: str | Path | None, preferred: str = "") -> Path | None:
    if preferred:
        return Path(preferred).expanduser().resolve()
    if script_path and str(script_path) not in ("Root", "Untitled"):
        return Path(script_path).expanduser().resolve().parent / ".shotbox-cache"
    return None
