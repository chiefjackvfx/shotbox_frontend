"""Capture actual native ShotBox properties in Nuke GUI at useful panel widths.

Ordinary Python launcher: .venv/bin/python inspect_ui.py --output docs
Creates an isolated GUI fixture; never runs inference or edits preferences.
"""
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
    from shotbox_nuke import capture, node

    report_path = Path(os.environ["SHOTBOX_UI_REPORT"])
    output = Path(os.environ["SHOTBOX_UI_OUTPUT"])
    report = {"host": capture.host_identity(), "gui": nuke.GUI, "screenshots": [], "passed": []}
    state = {}

    def finish(error=None):
        if error:
            report.update(status="failed", error=error)
        else:
            report["status"] = "passed"
        report_path.write_text(json.dumps(report, indent=2))
        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptExit()

    def metadata(widget):
        return {"class": widget.metaObject().className(), "name": widget.objectName(),
                "title": widget.windowTitle(), "width": widget.width(), "height": widget.height(),
                "visible": widget.isVisible()}

    def capture_panel():
        try:
            window = state["window"]
            width, tab = state["jobs"].pop(0)
            # Nuke retains its native properties-card height when its outer
            # floating window shrinks. Keep height stable across tab captures.
            window.resize(width, min(650, state["height"]))
            # The tab labels are native Qt children; clicking them exercises
            # the actual visible panel without depending on Node.setTab indices.
            tab_bar = next(bar for bar in window.findChildren(QtWidgets.QTabBar)
                           if any(bar.tabText(i) == tab for i in range(bar.count())))
            tab_bar.setCurrentIndex(next(i for i in range(tab_bar.count()) if tab_bar.tabText(i) == tab))

            def snapshot():
                try:
                    image_path = output / ("nuke-ui-%d-%s.png" % (width, tab.lower()))
                    assert window.grab().save(str(image_path)), image_path
                    buttons = []
                    for candidate in window.findChildren(QtWidgets.QAbstractButton):
                        text = candidate.text().replace("&", "").strip()
                        if not candidate.isVisible() or not text:
                            continue
                        origin = candidate.mapTo(window, QtCore.QPoint(0, 0))
                        buttons.append({"text": text, "x": origin.x(), "y": origin.y(),
                                        "width": candidate.width(), "height": candidate.height()})
                    report["screenshots"].append({"requested_width": width, "tab": tab,
                        "image": str(image_path), "panel": metadata(window), "buttons": buttons})
                    assert window.width() <= width + 4, ("Native panel minimum width exceeds target", width, window.width())
                    for button in buttons:
                        assert button["x"] >= 0 and button["x"] + button["width"] <= window.width(), button
                        assert button["y"] >= 0 and button["y"] + button["height"] <= window.height(), button
                    if state["jobs"]:
                        QtCore.QTimer.singleShot(100, capture_panel)
                    else:
                        report["passed"].append("actual floating native properties panel captured at650and480pixels across all three ShotBox tabs")
                        report["passed"].append("visible button geometry remains within the properties window at both widths")
                        finish()
                except Exception:
                    finish(traceback.format_exc())

            QtCore.QTimer.singleShot(500, snapshot)
        except Exception:
            finish(traceback.format_exc())

    def inspect():
        try:
            application = QtWidgets.QApplication.instance()
            report["top_level_widgets"] = [metadata(widget) for widget in application.topLevelWidgets() if widget.isVisible()]
            window = next(widget for widget in application.topLevelWidgets()
                          if widget.metaObject().className() == "MultiPanel" and widget.objectName() == state["group"].name() and widget.isVisible())
            report["floating_window"] = metadata(window)
            report["tab_bars"] = [[bar.tabText(i) for i in range(bar.count())] for bar in window.findChildren(QtWidgets.QTabBar)]
            assert ["ShotBox", "Precomp", "Advanced", "Node"] in report["tab_bars"], report["tab_bars"]
            report["buttons"] = [{"text": button.text(), "visible": button.isVisible(), "class": button.metaObject().className()}
                                 for button in window.findChildren(QtWidgets.QAbstractButton) if button.text()]
            state.update(window=window, height=min(1000, application.primaryScreen().availableGeometry().height() - 80),
                         jobs=[(width, tab) for width in (650, 480) for tab in ("ShotBox", "Precomp", "Advanced")])
            if os.environ.get("SHOTBOX_UI_DISCOVERY") == "1":
                window.grab().save(str(output / "nuke-ui-discovery.png"))
                finish()
            else:
                capture_panel()
        except Exception:
            finish(traceback.format_exc())

    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and nuke.GUI
        nuke.root()["first_frame"].setValue(1001)
        nuke.root()["last_frame"].setValue(1025)
        source = nuke.nodes.Constant(name="PropertiesFixtureSource")
        group = node.create()
        state["group"] = group
        group.setInput(0, source)
        group["sb_color"].setValue("acescg")
        group["sb_precomp_name"].setValue("bam030_subject_alpha")
        group["sb_status"].setValue("Ready. Generate captures the input, creates alpha in the background and automatically reads the finished precomp.")
        try:
            from shotbox_nuke import ui
            ui.refresh(group)
        except ImportError:
            pass
        group.showControlPanel(True)
        QtCore.QTimer.singleShot(1000, inspect)
    except Exception:
        finish(traceback.format_exc())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", default="/opt/Nuke17.1v2/Nuke17.1")
    parser.add_argument("--output", type=Path, default=Path("docs"))
    parser.add_argument("--discovery", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = output / ("nuke-ui-discovery.json" if args.discovery else "nuke-ui-gui.json")
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="shotbox-ui-startup-") as temporary:
        startup = Path(temporary)
        (startup / "menu.py").write_text("import runpy\nfrom PySide6 import QtCore\nQtCore.QTimer.singleShot(1500, lambda: runpy.run_path(" + repr(str(script)) + ")[\"start\"]())\n")
        environment = dict(os.environ, NUKE_PATH=str(startup) + os.pathsep + str(script.parents[1]),
                           SHOTBOX_UI_REPORT=str(report), SHOTBOX_UI_OUTPUT=str(output), SHOTBOX_UI_DISCOVERY="1" if args.discovery else "0")
        log_path = startup / "nuke-ui-inspect.log"
        with log_path.open("w") as log:
            child = subprocess.Popen([args.nuke, "-q", "-m", "4"], env=environment, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = child.wait(timeout=120)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
                code = -1
        evidence = json.loads(report.read_text()) if report.exists() else {"status": "failed", "error": "No GUI report was written"}
        evidence["exit_code"] = code
        if code:
            evidence["status"] = "failed"
        report.write_text(json.dumps(evidence, indent=2))
        print(evidence["status"] + ": " + str(report))
        if evidence.get("error"):
            print(evidence["error"])
            print(log_path.read_text(errors="replace")[-5000:])
        return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
