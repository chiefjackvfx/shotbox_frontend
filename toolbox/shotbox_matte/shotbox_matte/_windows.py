"""DLL search paths for Shotbox's active Windows environment, including workers.

Only standard-library modules are imported here. Directory handles must survive
for the process lifetime: closing or collecting them removes the search paths.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from threading import Lock


_DLL_HANDLES: dict[str, object] = {}
_DLL_LOCK = Lock()


def configure_dlls(prefix: Path | str | None = None) -> tuple[Path, ...]:
    """Register active environment DLL directories once and retain their handles.

    The no-op on platforms without ``os.add_dll_directory`` keeps source-level
    tooling and the headless CLI portable. Missing optional wheel directories
    are skipped; errors registering an existing directory remain actionable.
    """
    add_directory = getattr(os, "add_dll_directory", None)
    if add_directory is None:
        return ()
    runtime = Path(sys.prefix if prefix is None else prefix).expanduser().resolve()
    site_packages = runtime / "Lib" / "site-packages"
    candidates = (
        runtime,
        runtime / "Scripts",
        site_packages / "torch" / "lib",
        site_packages / "PyQt6" / "Qt6" / "bin",
        site_packages / "OpenImageIO" / "bin",
        *sorted(site_packages.glob("*.libs"), key=lambda path: path.name.casefold()),
    )
    registered = []
    with _DLL_LOCK:
        for directory in candidates:
            if not directory.is_dir():
                continue
            canonical = directory.resolve()
            key = os.path.normcase(str(canonical))
            if key in _DLL_HANDLES:
                continue
            _DLL_HANDLES[key] = add_directory(str(canonical))
            registered.append(canonical)
    return tuple(registered)
