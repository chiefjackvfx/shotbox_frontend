"""Capture a saved native Read graph through QScreen (DAG uses OpenGL)."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import traceback


def start():
    import nuke
    from PySide6 import QtCore, QtWidgets
    report_path = Path(os.environ["SHOTBOX_GRAPH_REPORT"])

    def finish(report):
        report_path.write_text(json.dumps(report, indent=2))
        nuke.root().setModified(False)
        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptExit()

    try:
        group = nuke.toNode("ShotBoxMatte_bam030")
        assert group is not None
        from shotbox_nuke import readback
        read = readback.find(group)
        assert read is not None
        targets = [group, read] + [nuke.toNode(name) for name in ("UpstreamGrade", "ExistingDownstream", "ExistingTwoSlots", "PrecompReview")]
        nuke.showDag(nuke.root())
        for candidate in nuke.allNodes():
            candidate["selected"].setValue(candidate in targets)
        main = next(widget for widget in QtWidgets.QApplication.topLevelWidgets() if widget.objectName() == "NukeMainWindow")
        main.showNormal()
        main.resize(1440, 1000)
        main.raise_()
        main.activateWindow()
        nuke.zoomToFitSelected()

        def screenshot():
            try:
                image = os.environ["SHOTBOX_GRAPH_IMAGE"]
                pixmap = main.screen().grabWindow(int(main.winId()))
                assert not pixmap.isNull() and pixmap.save(image)
                finish({"status": "passed", "image": image, "method": "QScreen.grabWindow(native Nuke window ID)",
                        "nodes": [candidate.fullName() for candidate in targets], "script": nuke.root().name()})
            except Exception:
                finish({"status": "failed", "error": traceback.format_exc()})

        QtCore.QTimer.singleShot(1500, screenshot)
    except Exception:
        finish({"status": "failed", "error": traceback.format_exc()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-readback-network.json"))
    parser.add_argument("--image", type=Path, default=Path("docs/nuke-readback-network.png"))
    parser.add_argument("--nuke", default="/opt/Nuke17.1v2/Nuke17.1")
    args = parser.parse_args()
    here = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="shotbox-graph-ui-") as temporary:
        startup = Path(temporary)
        (startup / "menu.py").write_text("import runpy\nfrom PySide6 import QtCore\nQtCore.QTimer.singleShot(4000, lambda: runpy.run_path(" + repr(str(here)) + ")[\"start\"]())\n")
        environment = dict(os.environ, NUKE_PATH=str(startup) + os.pathsep + str(here.parents[1]),
                           SHOTBOX_GRAPH_REPORT=str(args.report.resolve()), SHOTBOX_GRAPH_IMAGE=str(args.image.resolve()))
        with (startup / "nuke.log").open("w") as log:
            child = subprocess.Popen([args.nuke, "-q", "-m", "4", str(args.script.resolve())], env=environment, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = child.wait(timeout=90)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
                code = -1
        report = json.loads(args.report.read_text()) if args.report.exists() else {"status": "failed", "error": "No graph report"}
        report["exit_code"] = code
        if code:
            report["status"] = "failed"
        args.report.write_text(json.dumps(report, indent=2))
        print(report["status"] + ": " + str(args.report))
        if report.get("error"):
            print(report["error"])
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
