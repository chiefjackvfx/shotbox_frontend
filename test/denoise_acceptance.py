"""Opt-in real Nuke/Neat Video acceptance; writes only disposable cache fixtures.

Run with the frontend Python: python test/denoise_acceptance.py --nuke /path/to/Nuke
The GUI controller ignores existing edit locks; no lock API is contacted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))


def host_command(executable, mode, directory, environment, *, expected_version=None):
    """Keep even Windows fixture/reopen processes on an owned private desktop."""
    from contextlib import nullcontext
    directory = Path(directory)
    command = [executable, "--nukex", "-i", "-t", str(Path(__file__).resolve()),
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


def fixture(directory, expected_version=None):
    noise = startup_probe(directory, expected_version)
    directory = Path(directory)
    status_path = directory / "startup-status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["stage"] = "fixture_creation"
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    print("DENOISE_STARTUP " + json.dumps(status), flush=True)
    try:
        create_fixture(directory, noise)
        status["completed"].append("fixture_creation")
        status["stage"] = "complete"
    except Exception as exc:
        status["error"] = str(exc)
        raise
    finally:
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")


def create_fixture(directory, noise):
    import nuke
    directory = Path(directory)
    script = directory / "shot with spaces/scripts/test shot_v001.nk"
    script.parent.mkdir(parents=True)
    os.chdir(script.parent)
    root = nuke.root()
    root["format"].setValue(nuke.addFormat("512 512 1 DenoiseAcceptance").name())
    root["first_frame"].setValue(1001)
    root["last_frame"].setValue(1003)
    root["project_directory"].fromScript("[python {nuke.script_directory()}]")
    nuke.scriptSaveAs(str(script), overwrite=True)
    source = nuke.nodes.Read(file="../plates/plate.%04d.png", first=1001, last=1003)
    noise.setName("Reduce_Noise_v6_1")
    noise.setInput(0, source)
    write = nuke.nodes.Write(name="WriteDN1", inputs=[noise], file="../renders/denoise.####.exr")
    write["file_type"].setValue("exr")
    write["channels"].setValue("rgb")
    write["create_directories"].setValue(True)
    write["use_limit"].setValue(True)
    write["first"].setValue(1002)
    write["last"].setValue(1002)
    write["beforeRender"].setValue("import time; time.sleep(2)")
    other = nuke.nodes.Write(name="OtherWrite", inputs=[source], file="../renders/unwanted.####.exr")
    other["file_type"].setValue("exr")
    nuke.nodes.ModelBuilder(name="AcceptanceModelBuilder")
    root["proxy"].setValue(True)
    nuke.frame(1001)
    nuke.scriptSaveAs(str(script), overwrite=True)
    assert "WriteDN1" in script.read_text()
    (directory / "fixture.json").write_text(json.dumps({"script": str(script), "nuke": nuke.NUKE_VERSION_STRING,
        "profile": noise["ClipParams"].value(), "write_file": write["file"].value()}), encoding="utf-8")


def reopen(directory):
    import nuke
    directory = Path(directory)
    data = json.loads((directory / "fixture.json").read_text())
    script = Path(data["script"])
    os.chdir(script.parent)
    nuke.scriptOpen(str(script))
    root = nuke.root()
    assert int(root["frame"].value()) == 1001 and root["proxy"].value()
    write = nuke.toNode("WriteDN1")
    assert write["file"].value() == data["write_file"]
    assert write["use_limit"].value() and int(write["first"].value()) == 1002
    assert nuke.toNode("Reduce_Noise_v6_1")["ClipParams"].value() != data["profile"]
    assert nuke.toNode("AcceptanceModelBuilder").Class() == "ModelBuilder"
    root["proxy"].setValue(False)
    write["use_limit"].setValue(False)
    write["file"].setValue(str(directory / "reopened.####.exr"))
    nuke.execute(write, 1001, 1003)
    (directory / "reopen.json").write_text(json.dumps({"saved_frame": 1001, "saved_proxy": True,
        "profile_changed": True, "write_path_preserved": True, "write_limit_preserved": True,
        "fresh_process_render": True, "modelbuilder_preserved": True,
        "nukex": bool(nuke.env.get("nukex"))}), encoding="utf-8")


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
            name = "shutdown" if shutdown else ("crash" if kill else ("cancel-saved" if interrupt_stage else "complete"))
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
        controller.start(shot, data["script"])
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


def acceptance(executable, *, expected_version=None):
    import app_cache
    import numpy as np
    from PIL import Image
    from nuke_denoise import create_request, run, script_digest

    with app_cache.temporary_cache_lease():
        directory = Path(tempfile.mkdtemp(prefix="denoise-verification-", dir=app_cache.cache_directory("temp")))
        print("DENOISE_ACCEPTANCE " + str(directory), flush=True)
        plates = directory / "shot with spaces/plates"
        plates.mkdir(parents=True)
        outputs = directory / "shot with spaces/renders"
        outputs.mkdir()
        for frame in range(1001, 1004):
            rng = np.random.default_rng(frame + 100)
            pixels = np.clip(100 + rng.normal(0, 9, (512, 512, 3)), 0, 255).astype("uint8")
            Image.fromarray(pixels).save(plates / f"plate.{frame}.png")
            (outputs / f"denoise.{frame}.exr").write_bytes(b"old frame must be replaced")
        env = dict(os.environ)
        for name in ("NUKE_TEMP_DIR", "NUKE_DISK_CACHE", "TMPDIR", "XDG_CACHE_HOME"):
            target = directory / name.lower()
            target.mkdir()
            env[name] = str(target)
        env["TEMP"] = env["TMP"] = env["TMPDIR"]
        host_command(executable, "fixture", directory, env, expected_version=expected_version)
        data = json.loads((directory / "fixture.json").read_text())
        script = Path(data["script"])
        before = script_digest(script)
        cancel_dir = directory / "cancel-test"
        cancel_dir.mkdir()
        request = create_request(cancel_dir, script, executable)
        def cancel_profile(event):
            if event["stage"] == "profiling":
                (cancel_dir / "cancel").touch()
        cancelled = run(request, cancel_profile)
        assert cancelled["stage"] == "cancelled" and not cancelled["saved"]
        assert script_digest(script) == before
        result = gui_job(directory, executable)
        neat = json.loads((Path(result["job_directory"]) / "neat-version.json").read_text(encoding="utf-8"))
        assert neat["version"] == "6.1.2", neat
        host_command(executable, "reopen", directory, env)
        digest = script_digest(script)
        crashed = gui_job(directory, executable, "profiling", kill=True)
        assert script_digest(script) == digest
        shutdown_result = gui_job(directory, executable, "profiling", shutdown=True)
        assert script_digest(script) == digest
        cancelled_saved = gui_job(directory, executable, "saved")
        assert cancelled_saved["saved"]
        import OpenEXR
        for frame in range(1001, 1004):
            with OpenEXR.File(str(outputs / f"denoise.{frame}.exr")) as image:
                pixels = image.channels()["RGB"].pixels.astype("float32")
                assert pixels.shape == (512, 512, 3) and np.isfinite(pixels).all()
            assert (directory / f"reopened.{frame}.exr").is_file()
        assert not list(outputs.glob("unwanted.*"))
        verification = {"nuke": data["nuke"], "neat": neat, "python": sys.version, "gui": result,
                        "reopen": json.loads((directory / "reopen.json").read_text()),
                        "cancel_before_save": cancelled, "relative_paths": True, "paths_with_spaces": True,
                        "supervisor_crash_cleanup": crashed, "cancel_after_save": cancelled_saved,
                        "application_shutdown": shutdown_result,
                        "only_WriteDN1_rendered": True, "existing_frames_replaced": True}
        (directory / "verification.json").write_text(json.dumps(verification, indent=2))
        print(json.dumps(verification, indent=2), flush=True)
        return verification


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke")
    parser.add_argument("--mode", choices=("preflight", "fixture", "reopen"))
    parser.add_argument("--expected-version")
    parser.add_argument("--directory", type=Path)
    args = parser.parse_args()
    if args.mode == "preflight":
        startup_probe(args.directory, args.expected_version)
    elif args.mode == "fixture":
        fixture(args.directory, args.expected_version)
    elif args.mode == "reopen":
        reopen(args.directory)
    else:
        if not args.nuke:
            parser.error("--nuke is required")
        acceptance(args.nuke, expected_version=args.expected_version)
