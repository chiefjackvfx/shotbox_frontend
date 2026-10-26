"""Actual modal naming + native GPU generation + standalone root-network Read."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from precomp_workflow_gui import PrecompWorkflow  # noqa: E402
import nuke  # noqa: E402
from PySide6 import QtCore, QtWidgets  # noqa: E402
from shotbox_nuke import controller, process, readback  # noqa: E402

_workflow = None


class ReadbackWorkflow(PrecompWorkflow):
    def answer_dialog(self, accept, value):
        started = time.monotonic()
        evidence = {"accept": accept, "typed_name": value}
        self.report.setdefault("naming_dialogs", []).append(evidence)

        def answer():
            try:
                application = QtWidgets.QApplication.instance()
                dialog = next((widget for widget in application.topLevelWidgets()
                               if widget.isVisible() and widget.windowTitle() == "Name precomp"), None)
                if dialog is None:
                    if time.monotonic() - started > 15:
                        raise TimeoutError("Native Name precomp dialog did not appear")
                    QtCore.QTimer.singleShot(100, answer)
                    return
                evidence["title"] = dialog.windowTitle()
                inputs = [widget for widget in dialog.findChildren(QtWidgets.QLineEdit) if widget.isVisible() and widget.isEnabled()]
                assert inputs, "Native naming panel has no visible name input"
                field = next((widget for widget in inputs if widget.text() == self.group["sb_precomp_name"].value()), inputs[0])
                evidence["default_name"] = field.text()
                if accept:
                    field.setText(value)
                    field.editingFinished.emit()
                    path = self.report_path.with_name("nuke-readback-name-dialog.png")
                    assert dialog.grab().save(str(path))
                    evidence["image"] = str(path)
                buttons = [widget for widget in dialog.findChildren(QtWidgets.QAbstractButton) if widget.isVisible() and widget.isEnabled()]
                evidence["buttons"] = [widget.text() for widget in buttons]
                wanted = ("Generate", "OK", "Render", "Create") if accept else ("Cancel",)
                button = next(widget for widget in buttons if widget.text().replace("&", "").strip() in wanted)
                button.click()
                evidence["answered"] = True
            except Exception:
                self.report["dialog_error"] = traceback.format_exc()
                modal = QtWidgets.QApplication.activeModalWidget()
                if modal is not None and hasattr(modal, "reject"):
                    modal.reject()

        QtCore.QTimer.singleShot(100, answer)
        return evidence

    def launch(self, seed):
        self.group["sb_seed"].setValue(seed)
        replay = os.environ.get("SHOTBOX_READBACK_REPLAY_MANIFEST")
        if replay and not getattr(self, "replay_installed", False):
            original = process.ChildProcess

            class ReplayProcess(original):
                def __init__(self, python, application, attempt, arguments, terminal, progress, done):
                    if terminal == "result":
                        arguments = [str(HERE / "replay_worker.py"), arguments[-1], replay]
                    super().__init__(python, application, attempt, arguments, terminal, progress, done)

            process.ChildProcess = ReplayProcess
            self.replay_installed = True
            self.report.update(native_gpu_inference=False,
                fixture="actual naming/controller/capture/probe/preflight/CLI export/validation/publication/Read; test-only CPU replay engine using resampled previously verified native GPU mattes",
                replay_source_manifest=replay)
        if self.phase == "first" and not hasattr(self, "downstream"):
            self.downstream = nuke.nodes.NoOp(name="ExistingDownstream", inputs=[self.group])
            self.slots = nuke.nodes.Merge2(name="ExistingTwoSlots", inputs=[self.group, self.group])
            self.group["sb_precomp_name"].setValue("ShotBoxMatte")
            old_pending = self.group["sb_pending_manifest"].value()
            directory = self.script.parent / "../renders/precomp"
            before = set(directory.resolve().glob("*"))
            cancelled = self.answer_dialog(False, "")
            controller.generate(self.group, prompt_name=True)
            assert cancelled.get("answered"), self.report.get("dialog_error")
            assert controller.SESSION.current is None
            assert self.group["sb_pending_manifest"].value() == old_pending
            assert set(directory.resolve().glob("*")) == before
            self.report["passed"].append("actual native naming dialog Cancel creates no attempt or output version")
        answered = self.answer_dialog(True, "bam030_alpha")
        controller.generate(self.group, prompt_name=True)
        assert answered.get("answered"), self.report.get("dialog_error")
        assert self.group["sb_precomp_name"].value() == "bam030_alpha"
        self.current_manifest = Path(self.group["sb_pending_manifest"].value())

    def success(self):
        read = readback.find(self.group)
        assert read is not None and read.Class() == "Read"
        assert "." not in read.fullName(), "Completed Read must be in the root network"
        assert self.group.node(read.name()) is None
        assert self.downstream.input(0) is read
        assert self.slots.input(0) is read and self.slots.input(1) is read
        assert nuke.toNode("PrecompReview").input(0) is read
        assert read["raw"].value() and read["first"].value() == self.start_frame and read["last"].value() == self.end_frame
        if hasattr(self, "visible_read"):
            assert read is self.visible_read and read["sb_read_id"].value() == self.visible_read_id
        else:
            self.visible_read = read
            self.visible_read_id = read["sb_read_id"].value()
        self.report.setdefault("external_reads", []).append({"phase": self.phase, "name": read.fullName(),
            "id": read["sb_read_id"].value(), "file": read["file"].value(), "manifest": read["sb_read_manifest"].value(),
            "consumer": self.downstream.fullName(), "raw": read["raw"].value()})
        self.report["passed"].append(self.phase + ": actual native GPU completion installs a visible standalone raw Read in root network and rewires all existing downstream slots including Viewer")
        if self.report.get("replay_source_manifest"):
            self.report["passed"][-1] = self.report["passed"][-1].replace("actual native GPU completion", "actual controller/CLI completion with explicit CPU replay engine")
            self.report.setdefault("replay_provenance", []).append(json.loads(self.current_manifest.with_name("replay_fixture.json").read_text()))
        super().success()

    def render_reference(self, label, first, last):
        # Inspect actual standalone Read output; bypass intentionally inspects
        # the generating Group's live-input mode from the inherited test.
        from shotbox_nuke.capture import configure_write, preserve_host
        output = self.directory / "acceptance" / self.phase / (label + ".%04d.exr")
        output.parent.mkdir(parents=True, exist_ok=True)
        source = self.group if label == "bypass" else self.visible_read
        with preserve_host():
            write = nuke.nodes.Write(name="StandaloneReadAcceptance", inputs=[source])
            try:
                write["file"].setValue(str(output))
                write["file_type"].setValue("exr")
                configure_write(write, channels="all")
                nuke.execute(write, first, last, 1, views=["main"], continueOnError=False)
            finally:
                nuke.delete(write)
        assert all(Path(str(output) % frame).is_file() for frame in range(first, last + 1))
        return str(output)

    def finish(self, error=None):
        if self.report.get("replay_source_manifest"):
            self.report["passed"] = [entry.replace("native GPU", "CPU replay CLI") for entry in self.report["passed"]]
        if not error:
            self.timer.stop()
            positions = [(self.grade, 0, -120), (self.group, 0, 0), (self.visible_read, 220, 100),
                         (self.downstream, 220, 260), (self.slots, 400, 260), (nuke.toNode("PrecompReview"), 220, 420)]
            for candidate in nuke.allNodes():
                candidate["selected"].setValue(False)
            for candidate, x, y in positions:
                candidate.setXYpos(x, y)
                candidate["selected"].setValue(True)
            nuke.zoomToFitSelected()
            QtWidgets.QApplication.processEvents()
            main = next(widget for widget in QtWidgets.QApplication.topLevelWidgets() if widget.objectName() == "NukeMainWindow")
            image = self.report_path.with_name("nuke-readback-network.png")
            main.grab().save(str(image))
            self.report["network_image"] = str(image)
            nuke.scriptSave()
        super().finish(error)


def start():
    global _workflow
    _workflow = ReadbackWorkflow()
    try:
        _workflow.setup()
    except Exception:
        _workflow.finish(traceback.format_exc())


if __name__ == "__main__":
    start()
