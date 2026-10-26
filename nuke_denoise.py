"""Background denoise supervisor. Runs outside both Shotbox Qt and Nuke Qt."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import shlex
import sys
import time

import app_cache
from nuke_denoise_display import DenoiseCancelled, private_display, discover_tools, stop_process

EVENT_PREFIX = "SHOTBOX_DENOISE_EVENT "


def build_nuke_command(executable):
    # Match the desktop launcher: full comps may contain NukeX-only controls
    # such as ModelBuilder's bake menu, even outside the denoise branch.
    threads = min(16, max(1, (os.cpu_count() or 8) // 2))
    return [str(executable), "--nukex", "-q", "-m", str(threads)]


def build_launch_command(executable, *, qt_bootstrap=None, topdown=False):
    command = build_nuke_command(executable)
    if topdown:
        command.append("--topdown")
    if qt_bootstrap is not None:
        command += ["-i", "--tg", str(qt_bootstrap)]
    return command


def script_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def create_request(directory, script, executable):
    script = Path(script).resolve()
    if script.suffix.lower() != ".nk" or not script.is_file():
        raise RuntimeError("The selected Nuke script does not exist.")
    if not os.access(script, os.W_OK):
        raise RuntimeError("The selected Nuke script is not writable.")
    request = {"script": str(script), "nuke": str(executable), "sha256": script_digest(script),
               "qt_terminal": True, "topdown": True}
    path = Path(directory) / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    return path


def emit(event):
    print(EVENT_PREFIX + json.dumps(event), flush=True)


def parse_event(line):
    if not line.startswith(EVENT_PREFIX):
        return None
    try:
        event = json.loads(line[len(EVENT_PREFIX):])
        return event if isinstance(event, dict) and isinstance(event.get("stage"), str) else None
    except (ValueError, TypeError):
        return None


def forward_nuke_output(path, offset, notify, *, final=False):
    """Mirror complete Nuke log lines while retaining partial lines for later."""
    with path.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(65536)
    end = len(data) if final else data.rfind(b"\n") + 1
    # Bound the pending line even if a plugin writes without newlines.
    if not end and len(data) == 65536:
        end = len(data)
    if end:
        notify({"stage": "debug", "source": "Nuke", "message": data[:end].decode("utf-8", errors="replace").rstrip()})
    return offset + end


@app_cache.temporary_cache_task
def run(request_path, notify=emit):
    path = Path(request_path).resolve()
    directory = path.parent
    request = json.loads(path.read_text(encoding="utf-8"))
    cancelled_by_signal = False

    def cancel(*_):
        nonlocal cancelled_by_signal
        cancelled_by_signal = True

    def cancelled():
        return cancelled_by_signal or (directory / "cancel").exists()

    old_handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    nuke = None
    saved = False
    terminal = None
    try:
        tools = discover_tools()
        with app_cache.temporary_cache_lease(), private_display(directory, cancelled, tools, notify=notify) as display:
            startup = directory / "startup"
            startup.mkdir()
            host = str(Path(__file__).with_name("nuke_denoise_host.py"))
            (startup / "menu.py").write_text(
                "import runpy\nrunpy.run_path(" + repr(host) + ")[\"start\"](" + repr(str(path)) + ")\n",
                encoding="utf-8",
            )
            bootstrap = None
            if request.get("qt_terminal"):
                bootstrap = directory / "qt-bootstrap.py"
                bootstrap.write_text(
                    "import runpy\nfrom PySide6.QtWidgets import QApplication\n"
                    "runpy.run_path(" + repr(host) + ")[\"start\"](" + repr(str(path)) + ")\n"
                    "QApplication.instance().exec()\n", encoding="utf-8",
                )
            command = build_launch_command(request["nuke"], qt_bootstrap=bootstrap, topdown=request.get("topdown", False))
            environment = display.environment(dict(os.environ, QT_SCALE_FACTOR="1", QT_AUTO_SCREEN_SCALE_FACTOR="0"))
            if sys.platform != "win32":
                environment.update(LANG="C.UTF-8", LC_ALL="C.UTF-8")
            for variable, folder in (("NUKE_TEMP_DIR", "nuke-temp"), ("NUKE_DISK_CACHE", "nuke-cache"),
                                     ("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "xdg-cache")):
                target = directory / folder
                target.mkdir()
                environment[variable] = str(target)
            environment["NUKE_PATH"] = str(startup) + os.pathsep + environment.get("NUKE_PATH", "")
            # Qt temporary files on Windows use TEMP/TMP rather than TMPDIR.
            environment["TEMP"] = environment["TMP"] = environment["TMPDIR"]
            for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
                environment.pop(key, None)
            notify({"stage": "starting"})
            notify({"stage": "debug", "source": "Supervisor",
                    "message": f"Launching {shlex.join(command)} on {display.number}"})
            log_offset = 0
            with (directory / "nuke.log").open("wb") as log:
                nuke = display.launch(command, environment, str(Path(request["script"]).parent), log)
                display.child = nuke
                offset, stage = 0, "starting"
                deadline = time.monotonic() + 120
                while True:
                    if cancelled():
                        raise DenoiseCancelled("Denoise cancelled.")
                    display._check()
                    log_offset = forward_nuke_output(directory / "nuke.log", log_offset, notify)
                    events = directory / "host-events.jsonl"
                    if events.exists():
                        with events.open(encoding="utf-8") as stream:
                            stream.seek(offset)
                            while line := stream.readline():
                                if not line.endswith("\n"):
                                    break
                                offset = stream.tell()
                                event = json.loads(line)
                                next_stage = event["stage"]
                                if next_stage != stage:
                                    stage = next_stage
                                    deadline = time.monotonic() + 300
                                saved = saved or stage == "saved"
                                if stage in ("complete", "failed"):
                                    terminal = event
                                else:
                                    notify(event)
                    if stage == "profiling":
                        display.poll_profile()
                    if nuke.poll() is not None:
                        # Read any last events before deciding success.
                        if terminal is not None:
                            if nuke.returncode != 0 and terminal["stage"] == "complete":
                                raise RuntimeError(f"Nuke exited with code {nuke.returncode} after rendering.")
                            break
                        if events.exists() and events.stat().st_size > offset:
                            continue
                        raise RuntimeError(f"Nuke exited with code {nuke.returncode} before completing denoise. See nuke.log.")
                    if stage != "rendering" and time.monotonic() > deadline:
                        raise RuntimeError(f"Denoise timed out while {stage}. See the job logs and profile-window.png.")
                    time.sleep(.1)
    except DenoiseCancelled as exc:
        terminal = {"stage": "cancelled", "error": str(exc)}
    except Exception as exc:
        terminal = {"stage": "failed", "error": str(exc)}
    finally:
        stop_process(nuke)
        if nuke is not None:
            forward_nuke_output(directory / "nuke.log", log_offset, notify, final=True)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    terminal = {**terminal, "saved": saved, "directory": str(directory)}
    (directory / "result.json").write_text(json.dumps(terminal, indent=2), encoding="utf-8")
    notify(terminal)
    return terminal


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    result = run(args.request)
    sys.exit(0 if result["stage"] == "complete" else 1)
