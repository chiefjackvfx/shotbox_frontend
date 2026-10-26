"""Install ShotBox's bundled plugins into an existing 3DE installation."""

from dataclasses import dataclass, field
import os
import re
from pathlib import Path
import shutil
import tempfile

from app_cache import temporary_cache_task


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


NUKE_BLOCK_START = "# BEGIN SHOTBOX MANAGED NUKE PLUGINS"
NUKE_BLOCK_END = "# END SHOTBOX MANAGED NUKE PLUGINS"


def nuke_startup_text(original, source):
    """Replace only our managed block or a recognized standalone legacy call."""
    import ast
    text = original
    if text.count(NUKE_BLOCK_START) != text.count(NUKE_BLOCK_END) or text.count(NUKE_BLOCK_START) > 1:
        raise ValueError("Malformed Shotbox startup block; correct init.py before installing.")
    if NUKE_BLOCK_START in text:
        start, end = text.index(NUKE_BLOCK_START), text.index(NUKE_BLOCK_END) + len(NUKE_BLOCK_END)
        text = text[:start] + text[end:].lstrip("\r\n")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise ValueError("Nuke init.py has invalid Python; correct it before installing.") from exc
    lines = text.splitlines(keepends=True)
    remove = set()
    top_level = {id(node) for node in tree.body}
    for expression in ast.walk(tree):
        if not isinstance(expression, ast.Expr) or not isinstance(expression.value, ast.Call):
            continue
        call = expression.value
        if not (isinstance(call.func, ast.Attribute) and call.func.attr == "pluginAddPath"):
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
            continue
        value = call.args[0].value.replace("\\", "/").rstrip("/").lower()
        recognized = "shotbox" in value and (value.endswith("/integrations/nuke") or value.endswith("/plugins/nuke"))
        if not recognized:
            continue
        if (id(expression) not in top_level or not isinstance(call.func.value, ast.Name)
                or call.func.value.id != "nuke" or ";" in lines[expression.lineno - 1]):
            raise ValueError("Custom Shotbox plugin registration found. Remove that registration manually, then retry.")
        remove.update(range(expression.lineno - 1, expression.end_lineno))
    preserved = "".join(line for index, line in enumerate(lines) if index not in remove)
    block = (NUKE_BLOCK_START + "\nimport nuke\nnuke.pluginAddPath(" + repr(str(source)) + ")\n"
             + NUKE_BLOCK_END + "\n")
    return preserved.rstrip("\r\n") + ("\n\n" if preserved.strip() else "") + block


def nuke_plugin_status(nuke_home=None):
    destination = Path(nuke_home) if nuke_home else Path.home() / ".nuke"
    path = destination / "init.py"
    if not path.exists():
        return "Not installed. Install the integration, then restart Nuke."
    text = path.read_text(encoding="utf-8-sig")
    source = Path(__file__).resolve().parent / "plugins" / "nuke"
    expected = "nuke.pluginAddPath(" + repr(str(source)) + ")"
    if NUKE_BLOCK_START in text and expected in text:
        return "Managed integration registered. Restart Nuke after plugin updates."
    return "Not registered for this frontend. Install / Update to register it."


@temporary_cache_task
def install_nuke_plugin(nuke_home=None, configuration=None):
    import json
    from app_cache import cache_directory
    from toolbox.shotbox_matte.configuration import APPLICATION_ROOT, load_configuration
    source = Path(__file__).resolve().parent / "plugins" / "nuke"
    for relative in ("init.py", "menu.py", "shotbox_nuke/__init__.py"):
        if not (source / relative).is_file():
            raise FileNotFoundError(f"Bundled Nuke integration is missing: {source / relative}")
    destination = Path(nuke_home) if nuke_home else Path.home() / ".nuke"
    startup = destination / "init.py"
    original = startup.read_bytes() if startup.exists() else b""
    updated = nuke_startup_text(original.decode("utf-8-sig"), source)
    preferences = destination / "shotbox_preferences.json"
    values = json.loads(preferences.read_text(encoding="utf-8")) if preferences.exists() else {"schema_version": 1}
    if not isinstance(values, dict) or values.get("schema_version") != 1:
        raise ValueError("Unsupported existing Nuke preferences; correct them before installation.")
    values.update(configuration if configuration is not None else load_configuration())
    values.update(schema_version=1, application_root=str(APPLICATION_ROOT))
    result = InstallResult(destination)
    with tempfile.TemporaryDirectory(prefix="nuke-install-", dir=cache_directory("temp")) as directory:
        work = Path(directory)
        startup_source, preferences_source = work / "init.py", work / "shotbox_preferences.json"
        startup_source.write_text(updated, encoding="utf-8")
        preferences_source.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
        if original and original != updated.encode("utf-8") and not (destination / "init.py.shotbox-backup").exists():
            backup = work / "backup"
            backup.write_bytes(original)
            _install_files([(backup, destination / "init.py.shotbox-backup")], result)
        if not result.failures:
            _install_files([(preferences_source, preferences)], result)
        if not result.failures:
            _install_files([(startup_source, startup)], result)
    return result
