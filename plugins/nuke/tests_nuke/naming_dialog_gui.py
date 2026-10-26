"""Actual Nuke Qt naming-dialog keyboard, preview and cancellation acceptance.

Ordinary Python launcher; never starts a worker or GPU inference.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback


def start():
    import nuke
    from PySide6 import QtCore, QtTest, QtWidgets
    from shotbox_nuke import capture, controller, naming, node, precomp_paths
    from shotbox_nuke.naming_dialog import NamingDialog

    output = Path(os.environ["SHOTBOX_NAMING_OUTPUT"])
    report_path = output / "nuke-naming-ui.json"
    report = {"host": capture.host_identity(), "gui": bool(nuke.GUI), "native_gpu_inference": False,
              "passed": [], "screenshots": [], "keyboard_events": []}
    clipboard = QtWidgets.QApplication.clipboard()
    original_mime = clipboard.mimeData()
    saved_mime = QtCore.QMimeData()
    if original_mime is not None:
        for fmt in original_mime.formats():
            saved_mime.setData(fmt, original_mime.data(fmt))
    active_dialogs = []

    def finish(error=None):
        for dialog in active_dialogs:
            dialog.reject()
        clipboard.setMimeData(saved_mime)
        report.update(status="failed" if error else "passed")
        if error:
            report["error"] = error
        report_path.write_text(json.dumps(report, indent=2))
        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptExit()

    def widget(dialog, cls, name):
        result = dialog.findChild(cls, name)
        assert result is not None, ("Dialog widget missing", name)
        return result

    def name_field(dialog):
        return widget(dialog, QtWidgets.QLineEdit, "precompName")

    def generate_button(dialog):
        return next(button for button in dialog.findChildren(QtWidgets.QAbstractButton)
                    if button.text().replace("&", "").strip() == "Generate")

    def all_text(dialog):
        return "\n".join(label.text() for label in dialog.findChildren(QtWidgets.QLabel))

    def record_field(field, expected, label):
        assert field.text() == expected, (label, field.text(), expected)
        report["keyboard_events"].append({"action": label, "text": field.text(), "cursor": field.cursorPosition()})

    def modal_answer(accept, value=None):
        state = {"answered": False}
        started = time.monotonic()

        def answer():
            dialog = next((candidate for candidate in QtWidgets.QApplication.topLevelWidgets()
                           if isinstance(candidate, QtWidgets.QDialog) and candidate.isVisible() and candidate.windowTitle() == "Name precomp"), None)
            if dialog is None:
                assert time.monotonic() - started < 15, "Naming dialog did not open"
                QtCore.QTimer.singleShot(50, answer)
                return
            if value is not None:
                field = name_field(dialog)
                field.selectAll()
                QtTest.QTest.keyClick(field, QtCore.Qt.Key_Backspace)
                QtTest.QTest.keyClicks(field, value)
            if accept:
                QtTest.QTest.keyClick(name_field(dialog), QtCore.Qt.Key_Return)
            else:
                button = next(button for button in dialog.findChildren(QtWidgets.QAbstractButton)
                              if button.text().replace("&", "").strip() == "Cancel")
                QtTest.QTest.mouseClick(button, QtCore.Qt.LeftButton)
            state["answered"] = True

        QtCore.QTimer.singleShot(100, answer)
        return state

    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and nuke.GUI
        base = Path(os.environ["SHOTBOX_NAMING_FIXTURE"])
        base.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="case-", dir=base))
        script = directory / "shot/nuke/bam030.nk"
        script.parent.mkdir(parents=True)
        nuke.root()["first_frame"].setValue(1001)
        nuke.root()["last_frame"].setValue(1025)
        nuke.scriptSaveAs(str(script), overwrite=True)
        source = nuke.nodes.Constant(name="NamingFixtureSource")
        group = node.create()
        group.setInput(0, source)
        group["sb_color"].setValue("acescg")
        group["sb_precomp_name"].setValue("")
        precomp_directory = (script.parent / "../renders/precomp").resolve()
        before_pending = group["sb_pending_manifest"].value()
        cancelled = modal_answer(False, "cancel this")
        controller.generate(group, prompt_name=True)
        assert cancelled["answered"]
        assert controller.SESSION.current is None and not controller.RUNS
        assert group["sb_pending_manifest"].value() == before_pending
        assert group["sb_precomp_name"].value() == ""
        assert not precomp_directory.exists()
        report["passed"].append("actual Generate opens the new dialog; Cancel leaves the saved name, pending attempt, session and output filesystem unchanged")

        dialog = NamingDialog(group)
        active_dialogs.append(dialog)
        dialog.show()
        QtTest.QTest.qWait(100)
        field = name_field(dialog)
        field.setFocus()
        field.clear()
        QtTest.QTest.keyClicks(field, "hero")
        QtTest.QTest.keyClick(field, QtCore.Qt.Key_Space)
        record_field(field, "hero_", "Space maps immediately to underscore")
        assert dialog.isVisible(), "Space must not activate the default Generate button"
        QtTest.QTest.keyClicks(field, "matte")
        record_field(field, "hero_matte", "typing continues after underscore")
        field.setCursorPosition(4)
        QtTest.QTest.keyClick(field, QtCore.Qt.Key_Space)
        record_field(field, "hero__matte", "Space insertion in middle")
        assert field.cursorPosition() == 5
        QtTest.QTest.keyClick(field, QtCore.Qt.Key_Backspace)
        record_field(field, "hero_matte", "Backspace preserves cursor and existing text")
        assert field.cursorPosition() == 4
        QtTest.QTest.keyClick(field, QtCore.Qt.Key_Delete)
        record_field(field, "heromatte", "Delete edits at current cursor")
        QtTest.QTest.keyClicks(field, " blue")
        record_field(field, "hero_bluematte", "continued typing at middle cursor")
        clipboard.setText("A B-/C!é9")
        field.selectAll()
        QtTest.QTest.keyClick(field, QtCore.Qt.Key_V, QtCore.Qt.ControlModifier)
        record_field(field, "A_B__C__9", "paste sanitizes spaces punctuation slash and non-ASCII immediately")
        assert field.cursorPosition() == len(field.text())
        field.setText("__hero_matte__")
        record_field(field, "__hero_matte__", "leading and trailing underscores remain while editing")
        report["passed"].append("actual Qt keyboard events cover Space, typing, middle insertion, cursor position, Backspace, Delete and Ctrl+V paste with live ASCII sanitization")

        field.clear()
        QtTest.QTest.keyClick(field, QtCore.Qt.Key_Return)
        assert dialog.isVisible() and not generate_button(dialog).isEnabled()
        field.setText("___")
        QtTest.QTest.keyClick(field, QtCore.Qt.Key_Return)
        assert dialog.isVisible() and not generate_button(dialog).isEnabled()
        error_label = widget(dialog, QtWidgets.QLabel, "nameError")
        assert error_label.isVisible() and error_label.text().strip()
        report["invalid_name_message"] = error_label.text()
        dialog.resize(480, dialog.sizeHint().height())
        QtTest.QTest.qWait(100)
        invalid_image = output / "nuke-naming-ui-invalid.png"
        assert dialog.grab().save(str(invalid_image))
        report["invalid_name_image"] = str(invalid_image)
        assert not precomp_directory.exists()
        report["passed"].append("empty and underscore-only names keep the dialog open with an inline error, Generate disabled and no attempt allocation")

        field.setText("bam030_subject")
        expected_one = precomp_paths.preview(str(script), "bam030_subject", version=1)
        assert "bam030_subject_v001.####.exr" in all_text(dialog), all_text(dialog)
        assert expected_one in all_text(dialog), all_text(dialog)
        assert generate_button(dialog).isEnabled()
        Path(expected_one).parent.mkdir(parents=True)
        field.setText("bam030_subject_changed")
        field.setText("bam030_subject")
        expected_two = precomp_paths.preview(str(script), "bam030_subject", version=2)
        assert "bam030_subject_v002.####.exr" in all_text(dialog), all_text(dialog)
        assert expected_two in all_text(dialog), all_text(dialog)
        assert not Path(expected_two).parent.exists()
        report.update(script=str(script), fixture_directory=str(directory), preview_v001=expected_one, preview_v002=expected_two)
        report["passed"].append("filename and full path update live; an existing v001 directory advances the preview to v002 without reserving or creating that version")
        for width in (620, 480):
            dialog.resize(width, dialog.sizeHint().height())
            QtTest.QTest.qWait(150)
            image = output / ("nuke-naming-ui-%d.png" % width)
            assert dialog.grab().save(str(image))
            assert dialog.width() <= width + 4, ("Dialog minimum width exceeds target", width, dialog.width())
            button_rects = []
            for button in dialog.findChildren(QtWidgets.QAbstractButton):
                if not button.isVisible():
                    continue
                point = button.mapTo(dialog, QtCore.QPoint(0, 0))
                assert point.x() >= 0 and point.y() >= 0
                assert point.x() + button.width() <= dialog.width() and point.y() + button.height() <= dialog.height()
                button_rects.append({"text": button.text(), "x": point.x(), "y": point.y(), "width": button.width(), "height": button.height()})
            report["screenshots"].append({"image": str(image), "width": dialog.width(), "height": dialog.height(), "buttons": button_rects})
        dialog.reject()
        accepted = modal_answer(True, "__ hero matte __")
        assert naming.prompt(group)
        assert accepted["answered"] and group["sb_precomp_name"].value() == "hero_matte"
        assert controller.SESSION.current is None and not controller.RUNS
        assert group["sb_pending_manifest"].value() == before_pending
        assert not list(directory.rglob("integration.json"))
        report["passed"].append("Enter accepts a valid normalized name, trims outer underscores and updates the node through the actual prompt API without starting generation")
        report["accepted_name"] = group["sb_precomp_name"].value()
        finish()
    except Exception:
        finish(traceback.format_exc())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", default="/opt/Nuke17.1v2/Nuke17.1")
    parser.add_argument("--output", type=Path, default=Path("docs"))
    parser.add_argument("--fixture", type=Path, default=Path("test-runs/nuke-naming-ui"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    here = Path(__file__).resolve()
    report_path = output / "nuke-naming-ui.json"
    with tempfile.TemporaryDirectory(prefix="shotbox-naming-startup-") as temporary:
        startup = Path(temporary)
        (startup / "menu.py").write_text("import runpy\nfrom PySide6 import QtCore\nQtCore.QTimer.singleShot(1500, lambda: runpy.run_path(" + repr(str(here)) + ")[\"start\"]())\n")
        environment = dict(os.environ, NUKE_PATH=str(startup) + os.pathsep + str(here.parents[1]),
                           SHOTBOX_NAMING_OUTPUT=str(output), SHOTBOX_NAMING_FIXTURE=str(args.fixture.resolve()))
        with (startup / "nuke.log").open("w") as log:
            child = subprocess.Popen([args.nuke, "-q", "-m", "4"], env=environment, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = child.wait(timeout=90)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.wait(timeout=10)
                code = -1
        report = json.loads(report_path.read_text()) if report_path.exists() else {"status": "failed", "error": "No naming UI report"}
        report["exit_code"] = code
        if code:
            report["status"] = "failed"
        report_path.write_text(json.dumps(report, indent=2))
        print(report["status"] + ": " + str(report_path))
        if report.get("error"):
            print(report["error"])
            print((startup / "nuke.log").read_text(errors="replace")[-4000:])
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
