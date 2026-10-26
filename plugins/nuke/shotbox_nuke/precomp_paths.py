"""PrecompSwitcher-compatible output paths without importing its Nuke UI.

Final EXRs live in an exclusively allocated version folder; job records stay
in the private cache. Existing precomps, including another tool's output,
are never reused or overwritten.
"""
from __future__ import annotations

import ast
import ntpath
import os
from pathlib import Path, PureWindowsPath
import re
from string import Formatter
import sys


DEFAULT_TEMPLATE = "{script_dir}/../renders/precomp/{precomp_name}{ver}/{precomp_name}{ver}.####.{ext}"
_TOKENS = {"script_dir", "shot_code", "precomp_name", "write_name", "ver", "ext"}


def _config_candidates():
    """Prefer the actual loaded installation, then normal import search paths."""
    for key in ("precomp_config", "precomp_switcher.precomp_config"):
        module = sys.modules.get(key)
        source = getattr(module, "__file__", None)
        if source:
            yield Path(source).with_suffix(".py")
    for entry in sys.path:
        if entry:
            yield Path(entry) / "precomp_config.py"
            yield Path(entry) / "precomp_switcher" / "precomp_config.py"
    # Source checkouts commonly sit beside one another. Resolve this file first
    # so a plugin installed using a symlink still finds that sibling checkout.
    for parent in Path(__file__).resolve().parents:
        yield parent / "PrecompSwitcher" / "precomp_switcher" / "precomp_config.py"


def config_template(config_path: str | Path | None = None) -> str:
    """Read a literal WRITE_FILE_TEMPLATE safely; never execute config code.

    An unavailable installation uses the shared default. A present config with
    a disabled or computed template needs an explicit ShotBox template override.
    """
    path = Path(config_path) if config_path is not None else next(
        (candidate for candidate in _config_candidates() if candidate.is_file()), None)
    if path is None:
        return DEFAULT_TEMPLATE
    try:
        source = path.read_text(encoding="utf-8-sig")
        if len(source) > 1024 * 1024:
            raise ValueError("PrecompSwitcher config exceeds 1 MiB.")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise ValueError(f"Cannot read PrecompSwitcher config {path}: {exc}") from exc
    value = None
    found = False
    for statement in tree.body:
        targets = statement.targets if isinstance(statement, ast.Assign) else (
            [statement.target] if isinstance(statement, ast.AnnAssign) else [])
        if any(isinstance(target, ast.Name) and target.id == "WRITE_FILE_TEMPLATE" for target in targets):
            found = True
            try:
                value = ast.literal_eval(statement.value)
            except (ValueError, TypeError, SyntaxError) as exc:
                raise ValueError("PrecompSwitcher WRITE_FILE_TEMPLATE must be a literal string; "
                                 "set the ShotBox precomp template override.") from exc
    if not found:
        return DEFAULT_TEMPLATE
    if not isinstance(value, str) or not value.strip():
        raise ValueError("PrecompSwitcher WRITE_FILE_TEMPLATE is disabled; set the ShotBox precomp template override.")
    return value


def _precomp_name(name: str) -> str:
    if not isinstance(name, str) or name.strip() in ("", ".", "..") or any(c in name for c in "/\\\x00"):
        raise ValueError("Precomp name must be a name, without path separators or traversal.")
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    if not clean:
        raise ValueError("Precomp name must contain letters, numbers, underscores or hyphens.")
    return clean


def normalize_name(name: str) -> str:
    """Validate an artist-entered name and return its actual filename fragment."""
    return _precomp_name(name)


def _resolve(script_path: str | Path, name: str, version: int, template: str) -> str:
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("Precomp version must be a positive integer.")
    if not isinstance(template, str) or not template.strip():
        raise ValueError("Set a precomp output template.")
    if any(c in template for c in "\x00\r\n[]$"):
        raise ValueError("Precomp template must be a literal path, without Nuke expressions.")
    try:
        fields = list(Formatter().parse(template))
    except ValueError as exc:
        raise ValueError(f"Invalid precomp template: {exc}") from exc
    tokens = set()
    for _, field, spec, conversion in fields:
        if field is not None:
            if field not in _TOKENS or spec or conversion:
                raise ValueError(f"Unsupported precomp template token: {{{field}}}.")
            tokens.add(field)
    # The final sequence gets a unique immediate parent reserved atomically.
    normalized_template = template.replace("\\", "/")
    parent_name = normalized_template.rsplit("/", 2)[-2] if "/" in normalized_template else ""
    if "ver" not in tokens or "{ver}" not in parent_name:
        raise ValueError("Precomp template needs {ver} in the output's immediate parent folder.")
    name = _precomp_name(name)
    script = os.fspath(script_path) if script_path is not None else ""
    if tokens & {"script_dir", "shot_code"} and (not script or script == "Root"):
        raise ValueError("Save the Nuke script before generating a precomp.")
    windows = bool(PureWindowsPath(script).drive or PureWindowsPath(template).drive)
    if script and script != "Root":
        if windows:
            if not ntpath.isabs(script):
                raise ValueError("Windows Nuke script path must be absolute.")
            script_dir = ntpath.dirname(ntpath.normpath(script)).replace("\\", "/")
            shot_code = ntpath.basename(ntpath.dirname(script_dir))
        else:
            script_dir = str(Path(script).expanduser().absolute().parent)
            shot_code = Path(script_dir).parent.name
    else:
        script_dir, shot_code = "", ""
    if "shot_code" in tokens:
        shot_code = _precomp_name(shot_code)
    result = template.format(script_dir=script_dir, shot_code=shot_code, precomp_name=name,
                             write_name=f"Write_{name}", ver=f"_v{version:03d}", ext="exr")
    if windows:
        result = ntpath.normpath(result).replace("\\", "/")
        if not ntpath.isabs(result):
            raise ValueError("Precomp template must resolve to an absolute path.")
    else:
        result = os.path.normpath(result)
        if not os.path.isabs(result):
            raise ValueError("Precomp template must resolve to an absolute path.")
    filename = result.rsplit("/", 1)[-1]
    if (not filename.endswith(".exr") or filename.count("####") != 1
            or result.count("#") != 4 or any(c in result for c in "\x00\r\n[]${}%")):
        raise ValueError("Precomp template must produce an EXR sequence with one #### frame token and a safe filename.")
    if filename in (".####.exr", "####.exr") or any(c in filename for c in '<>:"|?*'):
        raise ValueError("Precomp template has an unsafe output filename.")
    return result


def preview(script_path: str | Path, name: str, version: int = 1, template: str | None = None) -> str:
    """Return the resolved POSIX-style EXR sequence path without creating it."""
    return _resolve(script_path, name, version, config_template() if template is None else template)


def allocate(script_path: str | Path, name: str, template: str | None = None) -> dict:
    """Reserve the first free version directory with an exclusive mkdir.

    Existing folders are preserved, including legacy two-digit version names.
    """
    template = config_template() if template is None else template
    name = _precomp_name(name)
    for version in range(1, 1000000):
        pattern = _resolve(script_path, name, version, template)
        if os.name != "nt" and PureWindowsPath(pattern).drive:
            raise ValueError("Windows precomp paths can only be allocated on Windows.")
        directory = Path(pattern).parent
        directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            directory.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return {"directory": directory, "pattern": pattern, "version": version,
                "precomp_name": name, "template": template}
    raise ValueError("No available precomp version below v1000000.")
