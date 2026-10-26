"""Inspect native standalone Read properties using an existing finished cache."""
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
    from shotbox_nuke import capture, node, readback
    report = {"host": capture.host_identity(), "inference": False}
    report_path = Path(os.environ["SHOTBOX_READ_UI_REPORT"])

    def finish(error=None):
        report.update(status="failed" if error else "passed")
        if error:
            report["error"] = error
        report_path.write_text(json.dumps(report, indent=2))
        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptExit()

    def inspect():
        try:
            window = next(widget for widget in QtWidgets.QApplication.topLevelWidgets()
                          if widget.metaObject().className() == "MultiPanel" and widget.objectName() == read.name() and widget.isVisible())
            window.resize(650, 650)
            report["tabs"] = [[bar.tabText(i) for i in range(bar.count())] for bar in window.findChildren(QtWidgets.QTabBar)]
            report["visible_tabs"] = [[bar.tabText(i) for i in range(bar.count()) if bar.isTabVisible(i)]
                                      for bar in window.findChildren(QtWidgets.QTabBar)]
            assert ["Read", "OCIO", "Sequence", "Metadata", "ShotBox", "Node"] in report["visible_tabs"], report["visible_tabs"]
            for bar in window.findChildren(QtWidgets.QTabBar):
                if "Read" in [bar.tabText(i) for i in range(bar.count())]:
                    bar.setCurrentIndex(next(i for i in range(bar.count()) if bar.tabText(i) == "Read"))

            def save():
                try:
                    image = os.environ["SHOTBOX_READ_UI_IMAGE"]
                    assert window.grab().save(image)
                    report.update(image=image, read=read.fullName(), raw=read["raw"].value(), file=read["file"].value(),
                                  metadata_visible={name: read[name].visible() for name in ("sb_read_owner", "sb_read_id", "sb_read_manifest", "sb_read_pattern")})
                    assert not any(report["metadata_visible"].values())
                    assert read["sb_read_note"].visible()
                    report["informational_note"] = read["sb_read_note"].value()
                    assert "generate another version" in report["informational_note"]
                    for bar in window.findChildren(QtWidgets.QTabBar):
                        if "ShotBox" in [bar.tabText(i) for i in range(bar.count())]:
                            bar.setCurrentIndex(next(i for i in range(bar.count()) if bar.tabText(i) == "ShotBox"))

                    def save_info():
                        try:
                            info = Path(image).with_name(Path(image).stem + "-info" + Path(image).suffix)
                            assert window.grab().save(str(info))
                            report["info_image"] = str(info)
                            finish()
                        except Exception:
                            finish(traceback.format_exc())

                    QtCore.QTimer.singleShot(300, save_info)
                except Exception:
                    finish(traceback.format_exc())

            QtCore.QTimer.singleShot(500, save)
        except Exception:
            finish(traceback.format_exc())

    try:
        path = Path(os.environ["SHOTBOX_READ_UI_MANIFEST"])
        data = json.loads(path.read_text())
        nuke.root()["first_frame"].setValue(data["capture"]["start"])
        nuke.root()["last_frame"].setValue(data["capture"]["end"])
        nuke.root()["format"].setValue(nuke.addFormat("%d %d 1 VerifiedReadback" % (data["capture"]["width"], data["capture"]["height"])).name())
        nuke.frame(data["capture"]["start"])
        group = node.create()
        node.activate(group, path)
        read = readback.install(group, path)
        assert read is not None
        read.showControlPanel(True)
        QtCore.QTimer.singleShot(1000, inspect)
    except Exception:
        finish(traceback.format_exc())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-readback-properties.json"))
    parser.add_argument("--image", type=Path, default=Path("docs/nuke-readback-properties.png"))
    parser.add_argument("--nuke", default="/opt/Nuke17.1v2/Nuke17.1")
    args = parser.parse_args()
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="shotbox-read-ui-") as temporary:
        startup = Path(temporary)
        (startup / "menu.py").write_text("import runpy\nfrom PySide6 import QtCore\nQtCore.QTimer.singleShot(1500, lambda: runpy.run_path(" + repr(str(script)) + ")[\"start\"]())\n")
        environment = dict(os.environ, NUKE_PATH=str(startup) + os.pathsep + str(script.parents[1]),
                           SHOTBOX_READ_UI_REPORT=str(args.report.resolve()), SHOTBOX_READ_UI_IMAGE=str(args.image.resolve()),
                           SHOTBOX_READ_UI_MANIFEST=str(args.manifest.resolve()))
        with (startup / "nuke.log").open("w") as log:
            child = subprocess.Popen([args.nuke, "-q", "-m", "4"], env=environment, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = child.wait(timeout=90)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
                code = -1
        report = json.loads(args.report.read_text()) if args.report.exists() else {"status": "failed", "error": "No GUI report"}
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
