"""Run on Windows: verify real Nuke/Neat denoise, cancellation, and batch jobs.

All media, scripts, profiles, renders, and reports are generated in .cache/temp.
No production shots or database writes are used.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import tempfile
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_cache


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
    from nuke_headless_tasks import find_nuke_executable
    from settings import get_settings_manager
    from test.denoise_acceptance import acceptance as single_acceptance
    from test.denoise_batch_acceptance import acceptance as batch_acceptance
    executable = executable or find_nuke_executable([get_settings_manager().get("nuke_exe_path", "")])
    if not executable:
        raise RuntimeError("Nuke was not found. Pass --nuke followed by the full path to Nuke17.1.exe.")
    with app_cache.temporary_cache_lease():
        directory = Path(tempfile.mkdtemp(prefix="denoise-windows-verification-", dir=app_cache.cache_directory("temp")))
        print(f"Windows denoise verification: {directory}", flush=True)
        report = {"success": False, "platform": platform.platform(), "python": sys.version,
                  "nuke_executable": str(executable), "started_at_utc": datetime.now(timezone.utc).isoformat()}
        with (directory / "console.log").open("w", encoding="utf-8") as log:
            try:
                with redirect_stdout(Mirror(log, sys.stdout)), redirect_stderr(Mirror(log, sys.stderr)):
                    report["single"] = single_acceptance(str(executable))
                    report["batch"] = batch_acceptance(str(executable))
                report["success"] = True
            except Exception as exc:
                report.update(error=str(exc), traceback=traceback.format_exc())
                log.write(report["traceback"])
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        path = directory / "verification.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("PASS" if report["success"] else "FAIL", flush=True)
        if report.get("error"):
            print(report["error"], flush=True)
        print(f"Report: {path}\nLog: {directory / 'console.log'}", flush=True)
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", help="Full path to Nuke17.1.exe; otherwise uses ShotBox settings/discovery")
    arguments = parser.parse_args()
    try:
        result = verify(arguments.nuke)
        sys.exit(0 if result["success"] else 1)
    except RuntimeError as error:
        parser.exit(2, str(error) + "\n")
