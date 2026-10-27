r"""Open Shotbox's profiling setup visibly on Windows, without rendering.

Run from the frontend: venv\Scripts\python.exe test\denoise_windows_diagnostic.py
Optionally pass --request <failed-job-folder>\request.json.
Close the diagnostic Nuke window to finish and release the cache lease.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))

import app_cache


def latest_request():
    paths = sorted(app_cache.cache_directory("temp").glob("denoise-*/request.json"),
                   key=lambda path: path.stat().st_mtime, reverse=True)
    for path in paths:
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if request.get("workflow") == "template" and not request.get("diagnostic"):
            return path
    raise RuntimeError("No cached denoise request found. Run Denoise once, or pass --request <path>.")


def prepare_request(original_path, directory):
    from denoise_template import digest

    request = json.loads(Path(original_path).read_text(encoding="utf-8"))
    if request.get("workflow") != "template":
        raise RuntimeError("Select a template denoise request.json.")
    # Copy the exact worker template. Never edit the failed job or its outputs.
    script = directory / "template.nk"
    shutil.copy2(request["script"], script)
    request.update(script=str(script), sha256=digest(script), qt_terminal=False,
                   diagnostic=True, original_request=str(original_path),
                   output_pattern=(directory / "renders/diagnostic_####.exr").as_posix())
    path = directory / "request.json"
    path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return path, request


def start_host(request_path):
    """Called by menu.py in a new visible NukeX session; never renders/exits."""
    import nuke
    from PySide6 import QtCore
    from denoise_template import prepare
    from nuke_denoise_host import find_nodes

    path = Path(request_path)
    directory = path.parent
    request = json.loads(path.read_text(encoding="utf-8"))

    def status(stage, **values):
        report = {"stage": stage, "nuke": nuke.NUKE_VERSION_STRING, "render_started": False, **values}
        (directory / "diagnostic-status.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("SHOTBOX_VISIBLE_DIAGNOSTIC " + json.dumps(report), flush=True)

    def run():
        try:
            if not nuke.GUI or not nuke.env.get("nukex"):
                raise RuntimeError("Visible profiling diagnosis requires the NukeX GUI.")
            preferences = nuke.toNode("preferences")
            autosave = preferences.knob("AutoSaveName") if preferences is not None else None
            if autosave is not None:
                autosave.setValue(str(directory / "diagnostic.autosave"))
            os.chdir(directory)
            nuke.scriptClear(ignoreUnsavedChanges=True)
            status("loading")
            nuke.scriptOpen(request["script"])
            media = prepare(nuke, request)
            (directory / "media.json").write_text(json.dumps(media, indent=2), encoding="utf-8")
            noise, _ = find_nodes(nuke)
            root = nuke.root()
            first = int(root["first_frame"].value())
            root["proxy"].setValue(False)
            root.setFrame(first)
            status("preparing", frame=first)
            noise.sample("red", noise.width() / 2, noise.height() / 2, noise.width(), noise.height())
            nuke.scriptSaveAs(str(directory / "prepared.nk"), overwrite=True)
            status("profiling", frame=first)
            noise["Prepare_Profile..."].execute()
            status("profile_window_closed", frame=first)
        except Exception as error:
            status("failed", error=str(error), traceback=traceback.format_exc())
            nuke.message("Shotbox visible diagnostic failed:\n" + str(error) + "\n\nLogs: " + str(directory))

    QtCore.QTimer.singleShot(1500, run)


@app_cache.temporary_cache_task
@app_cache.temporary_cache_lease()
def diagnose(request_path=None):
    if sys.platform != "win32":
        raise RuntimeError("Run this diagnostic on the Windows machine with Nuke and Neat installed.")
    from nuke_denoise import build_launch_command
    from nuke_denoise_display import stop_process
    from nuke_denoise_windows import WindowsDisplay

    original = Path(request_path).resolve() if request_path else latest_request()
    directory = Path(tempfile.mkdtemp(prefix="denoise-visible-", dir=app_cache.cache_directory("temp")))
    path, request = prepare_request(original, directory)
    if not Path(request["nuke"]).is_file():
        raise RuntimeError(f"Nuke executable is missing: {request['nuke']}")
    startup = directory / "startup"
    startup.mkdir()
    (startup / "menu.py").write_text(
        "import runpy\nrunpy.run_path(" + repr(str(Path(__file__).resolve())) + ")[\"start_host\"]("
        + repr(str(path)) + ")\n", encoding="utf-8")
    # Reuse the worker's environment sanitization, but launch normally on the
    # user's desktop. This command is an explicit, visible diagnostic only.
    environment = WindowsDisplay(directory, lambda: False).environment(
        dict(os.environ, QT_SCALE_FACTOR="1", QT_AUTO_SCREEN_SCALE_FACTOR="0"))
    for key in ("SHOTBOX_DENOISE_DESKTOP", "SHOTBOX_DENOISE_WINDOWS_JOB",
                "SHOTBOX_DENOISE_SUPERVISOR_PID", "SHOTBOX_DENOISE_JOB_DIR"):
        environment.pop(key, None)
    for key, folder in (("NUKE_TEMP_DIR", "nuke-temp"), ("NUKE_DISK_CACHE", "nuke-cache"),
                        ("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "xdg-cache")):
        target = directory / folder
        target.mkdir()
        environment[key] = str(target)
    environment["TEMP"] = environment["TMP"] = environment["TMPDIR"]
    environment["NUKE_PATH"] = str(startup) + os.pathsep + environment.get("NUKE_PATH", "")
    command = build_launch_command(request["nuke"], topdown=request.get("topdown", False))
    print(f"Original job: {original}\nClip: {request['source']}\nDiagnostic logs: {directory}", flush=True)
    print("Opening visible NukeX. Screenshot any Neat dialog before dismissing it. No render will start.\n"
          "If the editor opens normally, report that too. Close diagnostic Nuke when finished.", flush=True)
    (directory / "launch.json").write_text(json.dumps({"command": command, "desktop": "visible"}, indent=2), encoding="utf-8")
    with (directory / "nuke.log").open("wb") as log:
        process = subprocess.Popen(command, env=environment, cwd=str(directory), stdout=log,
                                   stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            code = process.wait()
        finally:
            stop_process(process)
    print(f"Nuke exited with code {code}. Logs retained: {directory}", flush=True)
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", help="Failed job's request.json; defaults to the newest cached template job")
    args = parser.parse_args()
    try:
        diagnose(args.request)
    except KeyboardInterrupt:
        parser.exit(130, "Diagnostic cancelled.\n")
    except (RuntimeError, OSError, ValueError) as error:
        parser.exit(2, str(error) + "\n")
