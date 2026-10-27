"""Run on Windows: verify real Nuke/Neat denoise, cancellation, and batch jobs.

All media, scripts, profiles, renders, and reports are generated in .cache/temp.
No production shots or database writes are used.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_cache

TARGET_NUKE = "17.1v2"


def resolve_executable(executable=None):
    """Qualification never falls back to a different installed Nuke version."""
    path = Path(executable) if executable else Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Nuke17.1v2/Nuke17.1.exe"
    if not path.is_file():
        raise RuntimeError(f"NukeX {TARGET_NUKE} executable not found: {path}. "
                           'Pass --nuke "C:\\path\\to\\Nuke17.1.exe".')
    return str(path.resolve())


def preflight(executable, directory):
    from test.denoise_test_support import host_command
    directory.mkdir()
    environment = dict(os.environ)
    for name in ("NUKE_TEMP_DIR", "NUKE_DISK_CACHE", "TMPDIR", "XDG_CACHE_HOME"):
        target = directory / name.lower()
        target.mkdir()
        environment[name] = str(target)
    environment["TEMP"] = environment["TMP"] = environment["TMPDIR"]
    host_command(executable, "preflight", directory, environment, expected_version=TARGET_NUKE)
    status = json.loads((directory / "startup-status.json").read_text(encoding="utf-8"))
    if status.get("stage") != "complete" or status.get("nuke") != TARGET_NUKE:
        raise RuntimeError(f"NukeX {TARGET_NUKE} preflight did not complete: {status}")
    return status


class Mirror:
    def __init__(self, log, console):
        self.log, self.console = log, console

    def write(self, text):
        self.log.write(text)
        self.log.flush()
        return self.console.write(text)

    def flush(self):
        self.log.flush()
        self.console.flush()


@app_cache.temporary_cache_task
def verify(executable=None):
    if sys.platform != "win32":
        raise RuntimeError("Run this verification on the Windows machine with NukeX and Neat Video installed.")
    from test.denoise_template_acceptance import acceptance as single_acceptance
    with app_cache.temporary_cache_lease():
        directory = Path(tempfile.mkdtemp(prefix="denoise-windows-verification-", dir=app_cache.cache_directory("temp")))
        print(f"Windows denoise verification: {directory}", flush=True)
        report = {"success": False, "platform": platform.platform(), "python": sys.version,
                  "target_nuke": TARGET_NUKE, "target_neat": "6.1.2",
                  "ui_entry_points": "pending_manual_verification",
                  "stage": "executable_selection", "started_at_utc": datetime.now(timezone.utc).isoformat()}
        with (directory / "console.log").open("w", encoding="utf-8") as log:
            try:
                with redirect_stdout(Mirror(log, sys.stdout)), redirect_stderr(Mirror(log, sys.stderr)):
                    executable = resolve_executable(executable)
                    report["nuke_executable"] = executable
                    print(f"Nuke executable: {executable} (required {TARGET_NUKE})", flush=True)
                    report["stage"] = "preflight"
                    report["preflight"] = preflight(executable, directory / "preflight")
                    report["stage"] = "single"
                    report["single"] = single_acceptance(executable, expected_version=TARGET_NUKE)
                    report["batch"] = report["single"].get("batch")
                report["stage"] = "complete"
                report["success"] = True
            except Exception as exc:
                report.update(error=str(exc), traceback=traceback.format_exc())
                status_path = directory / "preflight/startup-status.json"
                if status_path.is_file():
                    report["preflight"] = json.loads(status_path.read_text(encoding="utf-8"))
                log.write(report["traceback"])
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        path = directory / "verification.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PASS" if report["success"] else "FAIL", flush=True)
        if report.get("error"):
            print(f"Failed stage: {report['stage']}", flush=True)
            print(report["error"], flush=True)
        print(f"Report: {path}\nLog: {directory / 'console.log'}", flush=True)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", help="NukeX 17.1v2 executable; defaults to Program Files/Nuke17.1v2/Nuke17.1.exe")
    arguments = parser.parse_args()
    try:
        result = verify(arguments.nuke)
        sys.exit(0 if result["success"] else 1)
    except RuntimeError as error:
        parser.exit(2, str(error) + "\n")
