"""Protect active Nuke working files from Shotbox Settings → Clear Cache."""
import json
import os
from pathlib import Path
import uuid
from contextlib import contextmanager
from .preferences import frontend_root


@contextmanager
def _cache_process_lock():
    directory = frontend_root() / ".cache"
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


def acquire():
    with _cache_process_lock():
        directory = frontend_root() / ".cache" / "temp" / ".active-tasks"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (uuid.uuid4().hex + ".json")
        path.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
        return path


def release(path):
    if path is not None:
        Path(path).unlink(missing_ok=True)
