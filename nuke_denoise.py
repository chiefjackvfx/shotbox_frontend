"""Background denoise supervisor. Runs outside both Shotbox Qt and Nuke Qt."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import shlex
import shutil
import sys
import time

import app_cache
from nuke_denoise_display import DenoiseCancelled, private_display, discover_tools, stop_process

EVENT_PREFIX = "SHOTBOX_DENOISE_EVENT "


def build_nuke_command(executable):
    # Neat's interactive profiling editor requires the NukeX launch mode.
    threads = min(16, max(1, (os.cpu_count() or 8) // 2))
    return [str(executable), "--nukex", "-q", "-m", str(threads)]


def build_launch_command(executable, *, qt_bootstrap=None, topdown=False):
    command = build_nuke_command(executable)
    if topdown:
        command.append("--topdown")
    if qt_bootstrap is not None:
        command += ["-i", "--tg", str(qt_bootstrap)]
    return command


def create_request(directory, media, executable):
    from denoise_template import create_template_request
    if not isinstance(media, dict):
        raise RuntimeError("Denoise requires original source media and the denoise template.")
    return create_template_request(directory, media, executable)


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
def _run_once(request_path, notify=emit):
    path = Path(request_path).resolve()
    directory = path.parent
    request = json.loads(path.read_text(encoding="utf-8"))
    cancelled_by_signal = False

    def cancel(*_):
        nonlocal cancelled_by_signal
        cancelled_by_signal = True

    def cancelled():
        return (cancelled_by_signal or (directory / "cancel").exists()
                or bool(request.get("cancel_path") and Path(request["cancel_path"]).exists()))

    old_handlers = {sig: signal.signal(sig, cancel) for sig in (signal.SIGTERM, signal.SIGINT)}
    nuke = None
    profiled = False
    terminal = None
    try:
        if request.get("workflow") != "template":
            raise RuntimeError("Denoise requires a template-based media request.")
        tools = discover_tools()
        display_options = {"notify": notify}
        if sys.platform == "win32":
            display_options["mode"] = request.get("windows_display", "visible")
        with app_cache.temporary_cache_lease(), private_display(directory, cancelled, tools, **display_options) as display:
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
                    "message": "Nuke launch mode: " + ("Qt terminal" if bootstrap else
                        "visible full GUI" if getattr(display, "visible", False) else "full GUI on private desktop")})
            notify({"stage": "debug", "source": "Supervisor",
                    "message": f"Launching {shlex.join(command)} on {display.number or 'current desktop'}"})
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
                                profiled = profiled or stage == "profile_ready"
                                if stage in ("complete", "prepared", "failed"):
                                    terminal = event
                                else:
                                    notify(event)
                    if stage == "profiling":
                        display.poll_profile()
                    if nuke.poll() is not None:
                        # Read any last events before deciding success.
                        if terminal is not None:
                            if nuke.returncode != 0 and terminal["stage"] in ("complete", "prepared"):
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
    terminal = {**terminal, "profiled": profiled, "directory": str(directory)}
    (directory / "result.json").write_text(json.dumps(terminal, indent=2), encoding="utf-8")
    notify(terminal)
    return terminal


@app_cache.temporary_cache_task
def run(request_path, notify=emit):
    path = Path(request_path).resolve()
    request = json.loads(path.read_text(encoding="utf-8"))
    if sys.platform != "win32" or request.get("windows_display") != "hidden":
        return _run_once(path, notify=notify)
    directory = path.parent
    attempts = []
    with app_cache.temporary_cache_lease():
        for mode in ("hidden", "visible"):
            if (directory / "cancel").exists():
                result = {"stage": "cancelled", "error": "Denoise cancelled.", "profiled": False}
                break
            attempt_dir = directory / (mode + "-attempt")
            attempt_dir.mkdir()
            script = attempt_dir / "template.nk"
            shutil.copy2(request["script"], script)
            attempt = {**request, "script": str(script), "windows_display": mode,
                       "cancel_path": str(directory / "cancel")}
            attempt_path = attempt_dir / "request.json"
            attempt_path.write_text(json.dumps(attempt, indent=2), encoding="utf-8")
            notify({"stage": "display_mode", "message": f"Trying {mode} Windows denoise.", "mode": mode})

            def relay(event):
                # The first attempt's failure is not the overall job result.
                if event["stage"] not in {"complete", "prepared", "failed", "cancelled"}:
                    notify(event)

            result = _run_once(attempt_path, notify=relay)
            attempts.append({"mode": mode, **result})
            (directory / "attempts.json").write_text(json.dumps(attempts, indent=2), encoding="utf-8")
            reason = result.get("error", "")
            retryable = (reason.startswith("Hidden denoise unavailable:")
                         or reason.startswith("Unexpected dialog during denoise:")
                         or "closed without a verified fresh Auto Profile" in reason
                         or "timed out while profiling" in reason)
            if (mode != "hidden" or result["stage"] != "failed" or not retryable
                    or result.get("profiled") or (attempt_dir / "profile-confirmed").exists()):
                break
            notify({"stage": "display_fallback", "message": "Hidden profiling did not complete; retrying visibly.",
                    "reason": reason})
        completed_dir = result.get("directory")
        if completed_dir:
            for name in ("profiled.nk", "media.json", "neat-version.json", "profile-confirmed", "host-events.jsonl"):
                source = Path(completed_dir) / name
                if source.is_file():
                    shutil.copy2(source, directory / name)
        result = {**result, "attempt_directory": completed_dir, "directory": str(directory)}
        (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        notify(result)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    result = run(args.request)
    sys.exit(0 if result["stage"] in ("complete", "prepared") else 1)
