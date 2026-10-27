"""Shared native test launch, startup and template-controller helpers."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))

def host_command(executable, mode, directory, environment, *, expected_version=None, script_path=None):
    """Keep even Windows test processes on an owned private desktop."""
    from contextlib import nullcontext
    directory = Path(directory)
    command = [executable, "--nukex", "-i", "-t", str(script_path or Path(__file__).resolve()),
               "--mode", mode, "--directory", str(directory)]
    if expected_version:
        command += ["--expected-version", expected_version]
    context = nullcontext()
    if sys.platform == "win32":
        from nuke_denoise_windows import WindowsDisplay
        # This verification thread may already own Qt windows. Fixture launches
        # need a desktop name, not an attachment for UI enumeration/capture.
        context = WindowsDisplay(directory, lambda: False, attach=False)
    log_path = directory / (mode + ".log")
    try:
        with context as display:
            with log_path.open("wb") as log:
                if display is None:
                    subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=90, check=True)
                else:
                    process = display.launch(command, display.environment(environment), str(directory), log)
                    if process.wait(timeout=90):
                        raise subprocess.CalledProcessError(process.returncode, command)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # Read after closing the file and process context, including on Windows.
        # The verifier must expose Nuke's actual error instead of only exit 1.
        reason = "timed out" if isinstance(exc, subprocess.TimeoutExpired) else f"exited with code {exc.returncode}"
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        status_path = directory / "startup-status.json"
        status = status_path.read_text(encoding="utf-8") if status_path.exists() else "Nuke did not reach the startup probe."
        raise RuntimeError(f"Nuke {mode} {reason}.\nLog: {log_path}\nStartup: {status}\n\n"
                           f"{tail or 'Nuke produced no log output.'}") from exc


def startup_probe(directory, expected_version=None):
    """Persist each stage before executing it, including failures inside Nuke."""
    directory = Path(directory)
    status = {"stage": "python", "completed": [], "expected_nuke": expected_version}

    def stage(name):
        status["stage"] = name
        (directory / "startup-status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        print("DENOISE_STARTUP " + json.dumps(status), flush=True)

    try:
        stage("import_nuke")
        import nuke
        status["nuke"] = nuke.NUKE_VERSION_STRING
        status["completed"].append("import_nuke")
        stage("nuke_version")
        if expected_version and nuke.NUKE_VERSION_STRING != expected_version:
            raise RuntimeError(f"Expected NukeX {expected_version}; running {nuke.NUKE_VERSION_STRING}.")
        status["completed"].append("nuke_version")
        stage("nukex_license")
        if not nuke.env.get("nukex"):
            raise RuntimeError("NukeX mode is unavailable. Check the NukeX license and executable.")
        status["completed"].append("nukex_license")
        stage("neat_plugin")
        noise = nuke.createNode("OFXcom.absoft.NeatVideo6_v6", inpanel=False)
        for knob in ("ClipParams", "Prepare_Profile..."):
            if noise.knob(knob) is None:
                raise RuntimeError(f"Neat Video v6 is missing required control {knob}.")
        status["neat_node_class"] = noise.Class()
        # Exact Neat 6.1.2 is verified from the editor title during profiling.
        status["neat_version_verification"] = "pending_editor_title"
        status["completed"].append("neat_plugin")
        stage("complete")
        return noise
    except Exception as exc:
        status["error"] = str(exc)
        stage(status["stage"])
        raise


def gui_job(directory, executable, interrupt_stage=None, kill=False, shutdown=False):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMainWindow
    import denoise_controller
    application = QApplication.instance() or QApplication([])
    quit_on_last_window_closed = application.quitOnLastWindowClosed()
    application.setQuitOnLastWindowClosed(False)
    window = QMainWindow()
    shot = {"id": 999999, "title": "Denoise acceptance", "nuke_in_use": "another-artist@workstation"}
    original_shot = dict(shot)
    controller = denoise_controller.DenoiseController(window)
    original_show = controller._show_event
    interrupted = False

    def show_event(event):
        nonlocal interrupted
        original_show(event)
        if event["stage"] == interrupt_stage and not interrupted:
            interrupted = True
            if kill:
                controller.process.kill()
            elif shutdown:
                assert not controller.shutdown()
            else:
                controller.cancel()

    controller._show_event = show_event
    original = denoise_controller.find_nuke_executable
    denoise_controller.find_nuke_executable = lambda *_: executable
    data = json.loads((directory / "fixture.json").read_text())
    start_time = time.monotonic()
    ticks = 0
    timer = QTimer()

    def poll():
        nonlocal ticks
        ticks += 1
        if not controller.busy:
            timer.stop()
            name = "shutdown" if shutdown else ("crash" if kill else ("cancel-render" if interrupt_stage else "complete"))
            controller.dialog.grab().save(str(directory / f"progress-{name}.png"))
            result = dict(controller.last_result)
            result.update(ui_ticks=ticks, existing_lock_ignored=shot == original_shot,
                          job_directory=str(controller.directory), popup_closed=not controller.dialog.isVisible(),
                          cleanup_complete=controller.process is None and controller._lease is None)
            (directory / "gui.json").write_text(json.dumps(result, indent=2))
            application.quit()
        elif time.monotonic() - start_time > 180:
            controller.cancel()

    try:
        controller.start(shot, data["media"])
        timer.timeout.connect(poll)
        timer.start(25)
        application.exec()
    finally:
        denoise_controller.find_nuke_executable = original
        application.setQuitOnLastWindowClosed(quit_on_last_window_closed)
        window.deleteLater()
    result = json.loads((directory / "gui.json").read_text())
    expected = "failed" if kill else ("cancelled" if interrupt_stage else "complete")
    assert result["stage"] == expected, result
    if not shutdown:
        assert result["popup_closed"] == (expected == "complete"), result
    assert result["cleanup_complete"], result
    if shutdown:
        assert controller.shutdown()
    assert result["existing_lock_ignored"] and result["ui_ticks"] > 10
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("preflight",))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    startup_probe(args.directory, args.expected_version)
