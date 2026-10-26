"""Exec a private-display child that dies if its supervisor disappears."""
import ctypes
import json
import os
from pathlib import Path
import signal
import sys


def record_process(process, directory):
    record = {"pid": process.pid, "created": process.create_time()}
    receipt = Path(directory) / f"child-{process.pid}.json"
    temporary = receipt.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(receipt)


def main():
    expected_parent = int(os.environ["SHOTBOX_DENOISE_SUPERVISOR_PID"])
    if os.getppid() != expected_parent:
        return 1
    directory = Path(os.environ["SHOTBOX_DENOISE_JOB_DIR"])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Could not supervise the denoise child")
    if os.getppid() != expected_parent:
        return 1
    # Record identity before exec so crash cleanup cannot target a reused PID.
    import psutil
    record_process(psutil.Process(), directory)
    os.execvpe(sys.argv[1], sys.argv[1:], os.environ)


if __name__ == "__main__":
    sys.exit(main())
