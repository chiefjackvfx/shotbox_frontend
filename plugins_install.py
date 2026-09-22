"""Install ShotBox's bundled plugins into an existing 3DE installation."""

from dataclasses import dataclass, field
import os
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
    for source in scripts:
        target = destination / source.name
        temporary = None
        try:
            data = source.read_bytes()
            exists = target.exists()
            if exists and target.read_bytes() == data:
                result.unchanged.append(source.name)
                continue
            with tempfile.NamedTemporaryFile(dir=destination, prefix=".shotbox-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
            # Avoid leaving the replacement readable only by the installing user.
            shutil.copymode(target if exists else source, temporary)
            os.replace(temporary, target)
            temporary = None
            (result.updated if exists else result.installed).append(source.name)
        except OSError as exc:
            result.failures[source.name] = str(exc)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as exc:
                    result.failures[source.name] = result.failures.get(source.name, "") + f"; temporary file cleanup failed: {exc}"
    return result
