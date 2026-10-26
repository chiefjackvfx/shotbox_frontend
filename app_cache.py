"""Frontend-local disposable files shared by desktop tools."""

from functools import wraps
from pathlib import Path
import shutil
from threading import RLock


CACHE_ROOT = Path(__file__).resolve().parent / ".cache"
_lock = RLock()
_active_temp_tasks = 0


def cache_directory(category=""):
    with _lock:
        path = CACHE_ROOT / category
        path.mkdir(parents=True, exist_ok=True)
        return path


def temporary_cache_task(function):
    """Protect files needed by a running export or external application."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        global _active_temp_tasks
        with _lock:
            _active_temp_tasks += 1
        try:
            return function(*args, **kwargs)
        finally:
            with _lock:
                _active_temp_tasks -= 1
    return wrapped


def clear_cache_files():
    """Remove disposable contents; keep active tasks' temporary files."""
    result = {"removed": 0, "active_temp": False, "errors": []}
    with _lock:
        root = cache_directory()
        for path in root.iterdir():
            if path.name == "temp" and _active_temp_tasks:
                result["active_temp"] = True
                continue
            try:
                if path.is_symlink() or not path.is_dir():
                    path.unlink()
                else:
                    shutil.rmtree(path)
                result["removed"] += 1
            except OSError as exc:
                result["errors"].append(f"{path.name}: {exc}")
    return result
