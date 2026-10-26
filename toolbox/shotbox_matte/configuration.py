"""Shared desktop/Nuke configuration. No Qt or inference imports."""
import json
import os
from pathlib import Path

APPLICATION_ROOT = Path(__file__).resolve().parent
KEYS = ("application_root", "interpreter", "model_root", "model_config", "cache_root")

def preferences_path():
    return Path.home() / ".nuke" / "shotbox_preferences.json"

def default_interpreter():
    suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return APPLICATION_ROOT.parents[1] / "venv" / suffix


def _legacy_interpreter(value, *application_roots):
    """Recognize only the old managed runtime, never arbitrary overrides."""
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

def load_configuration():
    path = preferences_path()
    values = {"schema_version": 1, **dict.fromkeys(KEYS, "")}
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(saved, dict) or saved.get("schema_version") != 1:
            raise ValueError(f"Unsupported Matte configuration: {path}")
        if any(not isinstance(saved.get(key, ""), str) for key in KEYS):
            raise ValueError(f"Matte paths must be strings: {path}")
        values.update(saved)
    if _legacy_interpreter(values["interpreter"], values["application_root"], APPLICATION_ROOT):
        values["interpreter"] = ""
    # The shipped application is authoritative after moving the frontend.
    values["application_root"] = str(APPLICATION_ROOT)
    return values

def save_configuration(values):
    from app_cache import temporary_cache_task, cache_directory
    from plugins_install import InstallResult, _install_files
    @temporary_cache_task
    def save():
        import tempfile
        path = preferences_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        current = load_configuration()
        current.update({key: str(values[key]).strip() for key in KEYS if key in values})
        if _legacy_interpreter(current["interpreter"], current["application_root"], APPLICATION_ROOT):
            current["interpreter"] = ""
        current.update(schema_version=1, application_root=str(APPLICATION_ROOT))
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_directory("temp"),
                                             prefix=".shotbox-config-", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(current, stream, indent=2)
                stream.write("\n")
            result = _install_files([(temporary, path)], InstallResult(path.parent))
            if result.failures:
                raise OSError("; ".join(result.failures.values()))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    save()
