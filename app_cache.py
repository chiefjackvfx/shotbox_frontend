"""Frontend-local disposable files shared by desktop tools."""

from functools import wraps
from contextlib import contextmanager
import json
import os
import uuid
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


@contextmanager
def _cache_process_lock():
    directory = CACHE_ROOT
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lease.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def temporary_cache_lease():
    """Keep temporary files alive across callbacks or another host process."""
    global _active_temp_tasks
    with _lock, _cache_process_lock():
        directory = cache_directory("temp") / ".active-tasks"
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / (uuid.uuid4().hex + ".json")
        marker.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        _active_temp_tasks += 1
    try:
        yield
    finally:
        with _lock:
            marker.unlink(missing_ok=True)
            _active_temp_tasks -= 1


def _external_temp_tasks():
    import psutil
    directory = CACHE_ROOT / "temp" / ".active-tasks"
    active = False
    if directory.is_dir():
        for marker in directory.glob("*.json"):
            try:
                pid = json.loads(marker.read_text(encoding="utf-8"))["pid"]
                process = psutil.Process(pid)
                if process.is_running() and process.create_time() <= marker.stat().st_mtime + 1:
                    active = True
                else:
                    marker.unlink(missing_ok=True)
            except (ValueError, KeyError, TypeError, psutil.NoSuchProcess):
                marker.unlink(missing_ok=True)
            except (OSError, psutil.AccessDenied):
                active = True  # Keep files if an owner's state cannot be verified.
    return active


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
    with _lock, _cache_process_lock():
        root = cache_directory()
        for path in root.iterdir():
            if path.name == ".lease.lock":
                continue
            if path.name == "temp" and (_active_temp_tasks or _external_temp_tasks()):
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
