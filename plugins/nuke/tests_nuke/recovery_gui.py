"""Native GUI recovery/navigation acceptance with tiny alpha fixtures, no AI."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback


def start():
    import nuke
    from PySide6 import QtCore, QtGui, QtWidgets
    import shotbox_nuke
    from shotbox_nuke import cache, capture, controller, node, precomp_paths, preferences, process, readback, storage, ui

    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))
    from alpha_smoke import synthetic_alpha
    output = Path(os.environ["SHOTBOX_RECOVERY_OUTPUT"])
    directory = Path(os.environ["SHOTBOX_RECOVERY_DIRECTORY"])
    report_path = output / "nuke-recovery-gui.json"
    report = {"host": capture.host_identity(), "native_gpu_inference": False,
              "fixture": "tiny native synthetic alpha fixtures; controlled probe response and native capture cancellation exception; no worker inference",
              "passed": [], "screenshots": [], "states": {}, "navigation": []}
    state = {"original_preferences": preferences.load, "original_process": process.ChildProcess,
             "original_capture": capture.capture, "original_this_node": nuke.thisNode,
             "original_message": nuke.message, "original_open_url": QtGui.QDesktopServices.openUrl}

    def finish(error=None):
        preferences.load = state["original_preferences"]
        process.ChildProcess = state["original_process"]
        capture.capture = state["original_capture"]
        nuke.thisNode = state["original_this_node"]
        nuke.message = state["original_message"]
        QtGui.QDesktopServices.openUrl = state["original_open_url"]
        if error:
            report.update(status="failed", error=error)
        else:
            report["status"] = "passed"
        report_path.write_text(json.dumps(report, indent=2))
        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptExit()

    def safe(function):
        try:
            function()
        except Exception:
            finish(traceback.format_exc())

    def schedule(function, delay=100):
        QtCore.QTimer.singleShot(delay, lambda: safe(function))

    def button_states(group):
        return {key: {"label": group[key].label(), "enabled": group[key].enabled(), "visible": group[key].visible()}
                for key in ("sb_generate", "sb_cancel", "sb_find", "sb_view", "sb_log", "sb_relink") if group.knob(key)}

    def panel():
        application = QtWidgets.QApplication.instance()
        return next(widget for widget in application.topLevelWidgets()
                    if widget.metaObject().className() == "MultiPanel"
                    and widget.objectName() == state["group"].name() and widget.isVisible())

    def screenshots(label, continuation, tabs=("ShotBox",)):
        group = state["group"]
        ui.refresh(group)
        window = panel()
        jobs = [(width, tab) for width in (650, 480) for tab in tabs]
        report["states"][label] = button_states(group)
        buttons = report["states"][label]
        assert set(("sb_generate", "sb_cancel", "sb_find", "sb_view")).issubset(buttons)
        assert buttons["sb_generate"]["enabled"] == (label != "running"), buttons
        assert buttons["sb_cancel"]["enabled"] == (label == "running"), buttons
        for key in ("sb_find", "sb_view"):
            assert buttons[key]["enabled"] == (label != "idle"), buttons

        def next_image():
            width, tab = jobs.pop(0)
            window.resize(width, 700)
            bars = window.findChildren(QtWidgets.QTabBar)
            bar = next(bar for bar in bars if any(bar.tabText(index) == tab for index in range(bar.count())))
            bar.setCurrentIndex(next(index for index in range(bar.count()) if bar.tabText(index) == tab))

            def save():
                image = output / ("nuke-recovery-%s-%d-%s.png" % (label, width, tab.lower()))
                assert window.grab().save(str(image))
                buttons = []
                for button in window.findChildren(QtWidgets.QAbstractButton):
                    if not button.isVisible() or not button.text().strip():
                        continue
                    point = button.mapTo(window, QtCore.QPoint(0, 0))
                    item = {"text": button.text().replace("&", ""), "enabled": button.isEnabled(),
                            "x": point.x(), "y": point.y(), "width": button.width(), "height": button.height()}
                    assert item["x"] >= 0 and item["x"] + item["width"] <= window.width(), item
                    assert item["y"] >= 0 and item["y"] + item["height"] <= window.height(), item
                    buttons.append(item)
                report["screenshots"].append({"state": label, "width": width, "tab": tab, "image": str(image),
                    "actual_width": window.width(), "height": window.height(), "buttons": buttons})
                schedule(next_image if jobs else continuation)

            schedule(save, 500)

        schedule(next_image)

    def publish(group, name):
        receipt, data = synthetic_alpha(group, directory / "jobs")
        data["precomp"] = storage.promote_alpha(receipt.parent, data)
        data["precomp_request"] = {"name": name}
        target = precomp_paths.allocate(state["script"], name)
        storage.commit(receipt.parent, data, output_directory=target["directory"],
                       output_pattern=Path(str(target["pattern"]).replace("####", "%04d")).name)
        node.activate(group, receipt)
        read = readback.install(group, receipt)
        storage.cleanup(receipt.parent, data)
        return receipt, data, read

    def overlaps(first, second):
        return (first.xpos() < second.xpos() + second.screenWidth()
                and first.xpos() + first.screenWidth() > second.xpos()
                and first.ypos() < second.ypos() + second.screenHeight()
                and first.ypos() + first.screenHeight() > second.ypos())

    def navigate(group, read, nested=False):
        found = readback.reveal(group)
        assert found is read and read["selected"].value()
        parent = nuke.toNode(group.fullName().rpartition(".")[0]) if nested else nuke.root()
        assert all(not candidate["selected"].value() for candidate in nuke.allNodes(group=parent) if candidate is not read)
        window = readback.view(group)
        viewer = window.node()
        assert viewer.input(window.activeInput()) is read
        assert viewer["channels"].value() == "alpha"
        assert viewer.fullName().rpartition(".")[0] == read.fullName().rpartition(".")[0]
        report["navigation"].append({"nested": nested, "read": read.fullName(), "viewer": viewer.fullName(),
                                     "active_input": window.activeInput(), "channels": viewer["channels"].value(),
                                     "selected": read["selected"].value(), "zoom": nuke.zoom()})
        return viewer, window

    def functional():
        group = state["group"]
        group.setXYpos(0, 0)
        blockers = [nuke.nodes.NoOp(name="ArtistNode%d" % index) for index in range(4)]
        for index, blocker in enumerate(blockers):
            blocker.setXYpos(180 + index * 120, 0)
        backdrop = nuke.nodes.BackdropNode(name="ArtistBackdrop")
        backdrop.setXYpos(120, -100)
        backdrop["bdwidth"].setValue(600)
        backdrop["bdheight"].setValue(260)
        backdrop["note_font_size"].setValue(80)
        backdrop["label"].setValue("Artist network")
        receipt, data, read = publish(group, "recovery_alpha")
        state.update(receipt=receipt, data=data, read=read)
        assert not any(overlaps(read, candidate) for candidate in blockers + [group]), [(candidate.name(), candidate.xpos(), candidate.ypos()) for candidate in blockers]
        if overlaps(read, backdrop):
            # A clear interior position is valid; the title and borders are not.
            assert read.xpos() >= backdrop.xpos() + 16
            assert read.xpos() + read.screenWidth() <= backdrop.xpos() + backdrop["bdwidth"].value() - 16
            assert read.ypos() >= backdrop.ypos() + 132
            assert read.ypos() + read.screenHeight() <= backdrop.ypos() + backdrop["bdheight"].value() - 16
        read.setXYpos(732, 310)
        readback.install(group, receipt)
        assert (read.xpos(), read.ypos()) == (732, 310)
        state["identity"] = read["sb_read_id"].value()
        report["passed"].append("new Read avoids occupied graph positions; updating an existing Read preserves the artist's position")
        nuke.showDag(nuke.root())
        nuke.connectViewer(0, state["source"])
        viewer_window = nuke.activeViewer()
        viewer = viewer_window.node()
        viewer.setInput(1, blockers[0])
        viewer.setInput(3, blockers[1])
        viewer.setInput(5, read)
        viewer["channels"].setValue("rgba")
        preserved = {index: viewer.input(index) for index in (0, 1, 3)}
        actual_viewer, actual_window = navigate(group, read)
        assert actual_viewer is viewer and actual_window.activeInput() == 5
        assert all(viewer.input(index) is item for index, item in preserved.items())
        viewer.setInput(5, None)
        _, active = navigate(group, read)
        assert active.activeInput() == 2
        assert all(viewer.input(index) is item for index, item in preserved.items())
        report["passed"].append("Find selects/frames the root Read; View chooses existing or free Viewer input, displays alpha and preserves all unrelated inputs")
        container = nuke.nodes.Group(name="NestedArtistNetwork")
        with container:
            nested_source = nuke.nodes.Constant(name="NestedSource")
            nested = node.create()
            nested.setInput(0, nested_source)
        _, _, nested_read = publish(nested, "nested_recovery_alpha")
        root_connections = [viewer.input(index) for index in range(viewer.inputs())]
        navigate(nested, nested_read, nested=True)
        assert [viewer.input(index) for index in range(viewer.inputs())] == root_connections
        report["passed"].append("Find and View work inside the nested parent network without changing the root Viewer's connections")
        nuke.showDag(nuke.root())
        from shotbox_nuke import relink
        old_directory = Path(data["storage"]["output_directory"])
        moved = directory / "artist-moved-and-renamed"
        moved.mkdir()
        for index, record in enumerate(data["precomp"]["files"], 1001):
            shutil.move(str(old_directory / record["path"]), str(moved / ("artist_alpha.%04d.exr" % index)))
        selected = moved / "artist_alpha.1001.exr"
        damaged = moved / "artist_alpha.1002.exr"
        original_bytes = damaged.read_bytes()
        corrupted = bytearray(original_bytes)
        corrupted[len(corrupted) // 2] ^= 1
        damaged.write_bytes(corrupted)
        prior_receipt = receipt.read_bytes()
        prior_read = read["file"].toScript()
        prior_active = group["sb_active_manifest"].value()
        prior_group_knobs = {knob.name(): knob.toScript() for knob in group.allKnobs()}
        prior_read_knobs = {knob.name(): knob.toScript() for knob in read.allKnobs()}
        try:
            relink.relink(group, selected, manifest_path=receipt)
        except Exception as exc:
            report["corrupt_relink_error"] = str(exc)
        else:
            raise AssertionError("Corrupt moved alpha sequence was accepted")
        assert receipt.read_bytes() == prior_receipt and read["file"].toScript() == prior_read
        assert group["sb_active_manifest"].value() == prior_active
        assert {knob.name(): knob.toScript() for knob in group.allKnobs()} == prior_group_knobs
        assert {knob.name(): knob.toScript() for knob in read.allKnobs()} == prior_read_knobs
        damaged.write_bytes(original_bytes)
        relink.relink(group, selected, manifest_path=receipt)
        assert readback.find(group) is read and read["sb_read_id"].value() == state["identity"]
        assert read["file"].value() == str(moved / "artist_alpha.%04d.exr")
        relinked_receipt = group["sb_active_manifest"].value()
        relinked_file = read["file"].value()
        nuke.Undo.undo()
        assert group["sb_active_manifest"].value() == prior_active
        assert read["file"].toScript() == prior_read
        nuke.Undo.redo()
        assert group["sb_active_manifest"].value() == relinked_receipt
        assert read["file"].value() == relinked_file
        assert cache.validate_cache(group["sb_active_manifest"].value(), checksums=True)["valid"]
        shotbox_nuke.reload_result(group)
        read["reload"].execute()
        report["passed"].append("Relink recovers moved/renamed alpha EXRs; same-size corruption is refused without changing the prior receipt, Group or Read")
        report["passed"].append("one native Undo restores both Group receipt and Read sequence; one Redo restores the verified moved sequence")
        report["moved_sequence"] = read["file"].value()
        report["relinked_receipt"] = group["sb_active_manifest"].value()
        report["original_receipt"] = str(receipt)
        report["output_read_id"] = read["sb_read_id"].value()
        screenshots("success", start_pending, ("ShotBox", "Advanced"))

    class ProbeFixture:
        def __init__(self, python, application, attempt, arguments, terminal, progress, done):
            assert terminal == "probe", "Capture abort unexpectedly launched a worker"
            self.done = done
            self.process = None
            state["probe"] = self
            report.setdefault("probe_launches", []).append({"attempt": str(attempt), "terminal": terminal})

        def start(self):
            pass

        def stop(self, *args, **kwargs):
            pass

    def start_pending():
        group = state["group"]
        process.ChildProcess = ProbeFixture
        nuke.thisNode = lambda: group
        state["messages"], state["urls"] = [], []
        nuke.message = lambda text: state["messages"].append(str(text))
        QtGui.QDesktopServices.openUrl = lambda url: state["urls"].append(url.toLocalFile()) or True
        controller.generate(group, prompt_name=False)
        run = controller.RUNS[group["sb_uuid"].value()]
        state["run"] = run
        pending_log = run.directory / "stderr.log"
        pending_log.write_text("Latest pending attempt diagnostic\n")
        shotbox_nuke.action("log")
        assert state["urls"][-1] == str(pending_log), state["urls"]
        report["passed"].append("Open log chooses the latest pending attempt rather than the older active result")
        screenshots("running", cancel_capture)

    def cancel_capture():
        def cancel(*args, **kwargs):
            raise nuke.CancelledError("Native acceptance capture abort")

        capture.capture = cancel
        run = state["run"]
        previous = state["group"]["sb_active_manifest"].value()
        state["probe"].done({"type": "probe", "report": {"models": {}}}, None, 0)
        assert run.data["state"] == "cancelled", run.data
        assert controller.SESSION.current is None
        assert state["group"]["sb_active_manifest"].value() == previous
        assert readback.find(state["group"]) is state["read"]
        assert set(run.directory.iterdir()) == {run.path}
        report["native_cancelled_error"] = {"class": str(nuke.CancelledError), "state": run.data["state"], "message": run.data.get("message")}
        report["passed"].append("the actual native nuke.CancelledError during capture records cancelled, reaps the attempt, removes bulk files and keeps the prior active Read")
        controller.generate(state["group"], prompt_name=False)
        state["run"] = controller.RUNS[state["group"]["sb_uuid"].value()]

        def fail(*args, **kwargs):
            raise RuntimeError("Intentional latest capture failure")

        capture.capture = fail
        state["probe"].done({"type": "probe", "report": {"models": {}}}, None, 0)
        assert state["run"].data["state"] == "failed"
        shotbox_nuke.action("log")
        assert "Intentional latest capture failure" in state["messages"][-1]
        assert str(state["run"].path) in state["messages"][-1]
        report["passed"].append("after cleanup Open log reports the latest failed receipt instead of the older successful result")
        screenshots("failed", complete)

    def complete():
        nuke.scriptSave()
        report["script"] = str(state["script"])
        report["passed"].append("actual 650/480-pixel native properties panels retain visible controls within their window in idle, running, successful and failed states")
        finish()

    try:
        assert nuke.GUI and nuke.NUKE_VERSION_STRING == "17.1v2"
        shotbox_nuke.register()
        directory.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        application = here.parents[2] / "toolbox" / "shotbox_matte"
        prefs = state["original_preferences"]()
        prefs.update(application_root=str(application), interpreter=str(application / ".venv/bin/python"), cache_root=str(directory / "jobs"))
        preferences.load = lambda: dict(prefs)
        nuke.root()["format"].setValue(nuke.addFormat("16 12 1 ShotBoxAlphaSmoke").name())
        nuke.root()["first_frame"].setValue(1001)
        nuke.root()["last_frame"].setValue(1003)
        script = directory / "shot/nuke/recovery.nk"
        script.parent.mkdir(parents=True, exist_ok=True)
        state["script"] = script
        nuke.scriptSaveAs(str(script), overwrite=True)
        source = nuke.nodes.Constant(name="RecoverySource")
        source["color"].setValue([0.25, 0.5, 0.75, 0.9])
        source.setXYpos(-200, -120)
        group = node.create()
        group.setName("ShotBoxRecovery")
        group.setInput(0, source)
        group["sb_precomp_name"].setValue("recovery_alpha")
        state.update(group=group, source=source)
        group.showControlPanel(True)
        schedule(lambda: screenshots("idle", functional), 1000)
    except Exception:
        finish(traceback.format_exc())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", default="/opt/Nuke17.1v2/Nuke17.1")
    parser.add_argument("--output", type=Path, default=Path("docs"))
    parser.add_argument("--directory", type=Path, default=Path("test-runs/nuke-recovery"))
    args = parser.parse_args()
    output = args.output.resolve()
    directory = args.directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="case-", dir=directory))
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="shotbox-recovery-startup-") as temporary:
        startup = Path(temporary)
        (startup / "menu.py").write_text("import runpy\nfrom PySide6 import QtCore\nQtCore.QTimer.singleShot(1500, lambda: runpy.run_path(" + repr(str(script)) + ")[\"start\"]())\n")
        environment = dict(os.environ, NUKE_PATH=str(startup) + os.pathsep + str(script.parents[1]),
            SHOTBOX_RECOVERY_OUTPUT=str(output), SHOTBOX_RECOVERY_DIRECTORY=str(directory))
        log = Path("/tmp/shotbox-recovery-gui.log")
        with log.open("w") as stream:
            child = subprocess.Popen([args.nuke, "-q", "-m", "4"], env=environment, stdout=stream, stderr=subprocess.STDOUT)
            try:
                code = child.wait(timeout=150)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
                code = -1
        report_path = output / "nuke-recovery-gui.json"
        report = json.loads(report_path.read_text()) if report_path.exists() else {"status": "failed", "error": "No final report"}
        report["exit_code"] = code
        if code:
            report["status"] = "failed"
        report_path.write_text(json.dumps(report, indent=2))
        print(report["status"] + ": " + str(report_path))
        if report.get("error"):
            print(report["error"])
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
