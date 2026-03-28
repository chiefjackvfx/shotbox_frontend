import sys
import os
from pathlib import Path
import importlib.util
import importlib.machinery

def _load_extension(name: str, file_path: str):
    p = Path(file_path)
    if not p.exists():
        return None
    loader = importlib.machinery.ExtensionFileLoader(name, str(p))
    spec = importlib.util.spec_from_file_location(name, str(p), loader=loader)
    if spec is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        loader.exec_module(module)
    except Exception:
        return None
    return module

script_module = None

# 1) Try normal import
try:
    import fusionscript as script_module  # type: ignore
except ImportError:
    # 2) Try env var
    lib_path = os.getenv("RESOLVE_SCRIPT_LIB")
    if lib_path:
        script_module = _load_extension("fusionscript", lib_path)

    # 3) Try default install locations
    if not script_module:
        ext = ".so"
        if sys.platform.startswith("darwin"):
            base = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/"
        elif sys.platform.startswith("win") or sys.platform.startswith("cygwin"):
            ext = ".dll"
            base = r"C:\Program Files\Blackmagic Design\DaVinci Resolve\\"
        elif sys.platform.startswith("linux"):
            base = "/opt/resolve/libs/Fusion/"
        else:
            base = ""

        if base:
            script_module = _load_extension("fusionscript", os.path.join(base, "fusionscript" + ext))

if script_module:
    # Re-export so "import this_file" behaves like "import fusionscript"
    sys.modules[__name__] = script_module
else:
    raise ImportError("Could not locate module dependencies for fusionscript")
