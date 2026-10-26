"""Real Nuke 17.1v2 GUI/QProcess acceptance with explicitly controlled CPU AI.

Launch from a temporary NUKE_PATH/menu.py via QTimer.singleShot(1000, start).
SHOTBOX_WORKFLOW_REPORT selects the JSON report; SHOTBOX_WORKFLOW_CACHE may
select a persistent cache. SHOTBOX_GUI_NATIVE=1 runs a short real GPU job with
the unmodified probe/worker QProcess and skips controlled failure phases.
No user preferences are read or written.
"""
import json
import math
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import traceback

PACKAGE = Path(__file__).resolve().parents[1]
APPLICATION = PACKAGE.parents[1] / "toolbox" / "shotbox_matte"
sys.path.insert(0, str(PACKAGE))

import nuke  # noqa: E402
from PySide6 import QtCore  # noqa: E402
from shotbox_nuke import cache, capture, controller, lifecycle, node, preferences, process, render  # noqa: E402

_workflow = None


def close(actual, expected):
    assert math.isclose(actual, expected, abs_tol=1e-5), (actual, expected)


class Workflow:
    def __init__(self):
        self.native = os.environ.get("SHOTBOX_GUI_NATIVE") == "1"
        default_report = "nuke-gui-native.json" if self.native else "nuke-gui-workflow.json"
        self.report_path = Path(os.environ.get("SHOTBOX_WORKFLOW_REPORT", APPLICATION / "docs" / default_report))
        self.directory = Path(os.environ.get("SHOTBOX_WORKFLOW_CACHE", tempfile.mkdtemp(prefix="shotbox-gui-cpu-")))
        self.report = {"host": capture.host_identity(), "platform": platform.platform(), "gui": bool(nuke.GUI),
                       "fixture": ("actual native GPU inference; unmodified probe, worker, preflight and validation QProcess" if self.native else
                                   "controlled deterministic CPU engine; actual Nuke capture, QProcess, preflight, CLI export, bridge validation and activation"),
                       "native_gpu_inference": self.native, "cache_root": str(self.directory),
                       "passed": [], "attempts": [], "processes": [], "stage_events": []}
        self.phase = "setup"
        self.started = time.monotonic()
        self.timeout_seconds = int(os.environ.get("SHOTBOX_WORKFLOW_TIMEOUT", "150"))
        self.finished = False
        self.timer = QtCore.QTimer()
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.poll)
        self.original_process = process.ChildProcess
        self.original_preferences = preferences.load
        self.original_transition = controller.Run.transition
        self.cancel_sent = False
        self.action_done = False
        self.force_sent = False

    def setup(self):
        assert nuke.NUKE_VERSION_STRING == "17.1v2", nuke.NUKE_VERSION_STRING
        assert nuke.GUI, "This test requires actual GUI mode and its event loop."
        assert not nuke.env.get("nc") and not nuke.env.get("indie")
        lifecycle.register()
        nuke.root()["first_frame"].setValue(-2)
        nuke.root()["last_frame"].setValue(0)
        format_spec = "16 12 1 ShotBoxWorkflowNative" if self.native else "32 24 1 ShotBoxWorkflow"
        format_name = nuke.addFormat(format_spec).name()
        nuke.root()["format"].setValue(format_name)
        nuke.root()["fps"].setValue(24)
        source = nuke.nodes.Constant(name="WorkflowSource")
        source["format"].setValue(format_name)
        source["color"].setValue([0.25, 0.5, 0.75, 0.3])
        expression = nuke.nodes.Expression(name="WorkflowAnimatedPixels", inputs=[source])
        expression["expr0"].setValue("0.25 + frame*0.01 + x/64")
        expression["expr1"].setValue("y/48")
        retime = nuke.nodes.TimeOffset(name="WorkflowRetime", inputs=[expression])
        retime["time_offset"].setValue(3)
        self.grade = nuke.nodes.Grade(name="WorkflowGrade", inputs=[retime])
        self.grade["multiply"].setValue(0.75)
        prefs = self.original_preferences()
        prefs.update(schema_version=1, application_root=str(APPLICATION),
                     interpreter=str(preferences.resolve_interpreter(APPLICATION, prefs.get("interpreter") or None)),
                     cache_root=str(self.directory))
        preferences.load = lambda: dict(prefs)
        self.group = node.create()
        self.group.setInput(0, self.grade)
        self.other = node.create()
        self.other.setInput(0, self.grade)
        lifecycle.reconcile()
        for group in (self.group, self.other):
            group["sb_storage"].setValue("Custom cache")
            group["sb_color"].setValue("linear-srgb")
            group["sb_height"].setValue(64 if self.native else 32)
            group["sb_precision"].setValue("float")
            group["sb_cache_root"].setValue(str(self.directory))
        fixture_script = str(Path(__file__).with_name("controlled_worker.py"))
        workflow = self
        original = process.ChildProcess

        class ControlledProcess(original):
            def __init__(self, python, application, attempt, arguments, terminal, progress, done):
                rewritten = list(arguments)
                if terminal == "probe":
                    rewritten = [fixture_script, "probe", str(Path(attempt) / "runtime.json")]
                elif terminal == "result":
                    rewritten = [fixture_script, "worker", str(Path(attempt) / "worker.json")]
                super().__init__(python, application, attempt, rewritten, terminal, progress, done)
                entry = {"terminal": terminal, "arguments": rewritten, "attempt": str(attempt)}
                workflow.report["processes"].append(entry)
                self.process.started.connect(lambda: entry.update(pid=int(self.process.processId())))

        if not self.native:
            process.ChildProcess = ControlledProcess

        def transition(run, stage):
            workflow.report["stage_events"].append({"attempt": run.owner.attempt_id, "stage": stage})
            return workflow.original_transition(run, stage)

        controller.Run.transition = transition
        nuke.frame(7)
        nuke.root()["proxy"].setValue(True)
        self.phase = "success"
        self.launch(42)
        for candidate in (self.group, self.other):
            try:
                controller.generate(candidate)
                raise AssertionError("Session busy check failed")
            except RuntimeError as exc:
                assert "already running" in str(exc), exc
        self.report["passed"].append("duplicate Generate and second-node Generate rejected while busy")
        self.group.setName("RenamedDuringGeneration")
        self.timer.start()

    def launch(self, seed):
        self.action_done = False
        self.force_sent = False
        self.group["sb_seed"].setValue(seed)
        controller.generate(self.group, prompt_name=False)
        self.current_manifest = Path(self.group["sb_pending_manifest"].value())

    def record_attempt(self, expected):
        data = cache.read_manifest(self.current_manifest)
        assert data["state"] == expected, data
        self.report["attempts"].append({"phase": self.phase, "state": data["state"], "manifest": str(self.current_manifest)})
        return data

    def worker_ready(self):
        return self.current_manifest.with_name("controlled-worker-ready").is_file()

    def owned_pid(self):
        return int(controller.RUNS[self.group["sb_uuid"].value()].child.process.processId())

    def assert_reaped(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        raise AssertionError("Owned worker is still alive after shutdown: " + str(pid))

    def success(self):
        data = cache.read_manifest(self.current_manifest, require_complete=True)
        assert self.group["sb_active_manifest"].value() == str(self.current_manifest)
        assert nuke.root()["proxy"].value() is True
        assert nuke.frame() == 7
        self.active = str(self.current_manifest)
        nuke.root()["proxy"].setValue(False)
        # Render both references: Node.sample on a live TimeOffset graph does
        # not provide a reliable render-context oracle in Nuke 17.1v2.
        verification = self.directory / "render-verification"
        verification.mkdir()
        owned = []
        try:
            with capture.preserve_host():
                for name, upstream in (("direct", self.grade), ("generated", self.group)):
                    write = nuke.nodes.Write(name="WorkflowReferenceWrite", inputs=[upstream])
                    owned.append(write)
                    write["file"].setValue(str(verification / (name + ".%04d.exr")))
                    write["file_type"].setValue("exr")
                    write["datatype"].setValue("32 bit float")
                    write["channels"].setValue("rgba")
                    write["raw"].setValue(True)
                    write["frame_mode"].setValue("offset")
                    write["frame"].setValue("3")
                    nuke.execute(write, -2, 0, 1, views=["main"], continueOnError=False)
            reads = []
            sequences = [self.current_manifest.parent / "capture_v001/capture.%04d.exr",
                         verification / "direct.%04d.exr", verification / "generated.%04d.exr"]
            if self.native:
                first = cache.resolve_owned(self.current_manifest.parent, data["outputs"][0]["path"])
                sequences.append(first.with_name(first.name.rsplit(".", 2)[0] + ".%04d.exr"))
            for sequence in sequences:
                read = nuke.nodes.Read(file=str(sequence))
                owned.append(read)
                reads.append(read)
                read["raw"].setValue(True)
                read["first"].setValue(1)
                read["last"].setValue(3)
                read["frame_mode"].setValue("expression")
                read["frame"].setValue("frame - (-2) + 1")
            for frame in (-2, -1, 0):
                nuke.frame(frame)
                for channel in ("red", "green", "blue"):
                    close(reads[0].sample(channel, 3, 5), reads[1].sample(channel, 3, 5))
                    close(reads[2].sample(channel, 3, 5), reads[1].sample(channel, 3, 5))
                if self.native:
                    for x, y in ((0, 0), (15, 0), (0, 11), (15, 11), (3, 5)):
                        close(reads[2].sample("alpha", x, y), reads[3].sample("red", x, y))
                        for channel in ("red", "green", "blue"):
                            close(reads[2].sample(channel, x, y), reads[1].sample(channel, x, y))
                else:
                    close(reads[2].sample("alpha", 3, 5), -0.125 + (frame + 2) / 4)
                close(reads[0].sample("red", 3, 5), (0.25 + (frame - 3)*0.01 + 3/64)*0.75)
            for frame in (-3, 1):
                nuke.frame(frame)
                close(self.group.sample("alpha", 3, 5), 0)
        finally:
            for temporary in reversed(owned):
                nuke.delete(temporary)
        render.validate_node(self.group)
        self.report["passed"].extend(["actual GUI QProcess probe/capture/preflight/worker/validate/activation",
            "capture matches evaluated graded and retimed RGB at negative timeline frames",
            ("actual native backend matte equals rendered output alpha at five corner/interior positions on all three frames" if self.native else
             "original RGB preserved and three deterministic matte frames aligned"),
            ("original RGB preserved at frame corners and zero alpha outside processed range" if self.native else
             "raw negative matte value and zero alpha outside processed range"),
            "proxy/frame restored and node rename retains controller ownership", "strict Nuke cache validation"])
        self.report["attempts"].append({"phase": self.phase, "state": data["state"], "manifest": str(self.current_manifest)})
        if self.native:
            self.report["probe"] = data["probe"]
            self.report["worker_result"] = data["result"]
            self.report["generation"] = data["plan"]["spatial"]
            self.finish()
            return
        self.phase = "failure"
        self.launch(43)

    def poll(self):
        if self.finished:
            return
        try:
            if time.monotonic() - self.started > self.timeout_seconds:
                raise TimeoutError("GUI workflow exceeded its configured timeout")
            current = controller.SESSION.current
            if self.phase == "cancel" and current is not None and current.stage == "generating" and not self.cancel_sent:
                controller.cancel(self.group)
                self.cancel_sent = True
            if current is not None and current.stage == "generating" and self.worker_ready():
                if self.phase == "stale" and not self.action_done:
                    self.grade["multiply"].setValue(0.5)
                    self.action_done = True
                elif self.phase == "force":
                    if not self.action_done:
                        self.forced_pid = self.owned_pid()
                        controller.cancel(self.group)
                        self.force_started = time.monotonic()
                        self.action_done = True
                    elif not self.force_sent and time.monotonic() - self.force_started >= 30.2:
                        assert self.group["sb_force"].enabled()
                        controller.cancel(self.group, force=True)
                        self.force_sent = True
                elif self.phase == "delete" and not self.action_done:
                    pid, old_id, name = self.owned_pid(), self.group["sb_uuid"].value(), self.group.name()
                    old_active = self.group["sb_active_manifest"].value()
                    nuke.Undo.begin("Workflow delete owned worker")
                    nuke.delete(self.group)
                    nuke.Undo.end()
                    assert controller.SESSION.current is None and not controller.RUNS, (
                        "Delete failed to clear ownership", repr(controller.SESSION.current), list(controller.RUNS))
                    self.assert_reaped(pid)
                    self.record_attempt("cancelled")
                    nuke.Undo.undo()
                    self.group = nuke.toNode(name)
                    assert self.group is not None, "Undo did not restore the Group"
                    lifecycle.reconcile()
                    assert self.group["sb_uuid"].value() != old_id, "Undo restored retired running UUID"
                    assert self.group["sb_active_manifest"].value() == old_active, "Undo lost prior active cache"
                    self.group["sb_seed"].setValue(42)
                    render.validate_node(self.group)
                    self.report["passed"].append("node deletion reaps resistant owned child; undo restores cache with fresh UUID and no controller")
                    self.phase = "close"
                    self.launch(45)
                    return
                elif self.phase == "close" and not self.action_done:
                    pid = self.owned_pid()
                    # scriptNew may exit/relaunch this GUI process rather than
                    # return to Python. The launcher verifies normal host exit
                    # and that this exact owned child was reaped afterwards.
                    self.finished = True
                    self.timer.stop()
                    self.report["status"] = "awaiting_host_exit"
                    self.report["closing_worker_pid"] = pid
                    self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
                    self.report_path.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
                    # Exercise the user-facing File > New lifecycle. Clear is
                    # a low-level graph reset and does not reliably deliver
                    # onScriptClose for an unnamed GUI script in this build.
                    nuke.root().setModified(False)
                    nuke.scriptNew()
                    # If this host build returns, explicitly exit through its
                    # normal shutdown path and let the same launcher verify.
                    nuke.scriptExit()
                    return
            if current is not None:
                return
            if self.phase == "success":
                self.success()
            elif self.phase == "failure":
                data = cache.read_manifest(self.current_manifest)
                assert data["state"] == "failed", data
                assert self.group["sb_active_manifest"].value() == self.active
                self.report["attempts"].append({"phase": self.phase, "state": data["state"], "manifest": str(self.current_manifest)})
                self.report["passed"].append("worker nonzero/error regeneration retains prior active result and releases session lock")
                self.phase = "cancel"
                self.launch(44)
            elif self.phase == "cancel":
                data = cache.read_manifest(self.current_manifest)
                assert self.cancel_sent and data["state"] == "cancelled", data
                assert Path(str(self.current_manifest.parent / "worker.json") + ".cancel").is_file()
                assert self.group["sb_active_manifest"].value() == self.active
                self.report["attempts"].append({"phase": self.phase, "state": data["state"], "manifest": str(self.current_manifest)})
                self.report["passed"].append("cooperative worker cancellation retains prior active cache and releases session lock")
                self.group["sb_seed"].setValue(42)
                render.validate_node(self.group)
                self.phase = "stale"
                self.launch(46)
            elif self.phase == "stale":
                data = self.record_attempt("complete")
                assert self.action_done and "input changed" in data["message"].lower()
                assert self.group["sb_active_manifest"].value() == self.active
                assert cache.validate_cache(self.current_manifest)["valid"]
                self.grade["multiply"].setValue(0.75)
                self.group["sb_seed"].setValue(42)
                render.validate_node(self.group)
                self.report["passed"].append("upstream change during worker leaves validated completed attempt inactive and retains previous cache")
                self.phase = "force"
                self.launch(45)
            elif self.phase == "force":
                self.record_attempt("cancelled")
                assert self.force_sent and time.monotonic() - self.force_started >= 30
                self.assert_reaped(self.forced_pid)
                assert self.group["sb_active_manifest"].value() == self.active
                self.group["sb_seed"].setValue(42)
                render.validate_node(self.group)
                self.report["passed"].append("30-second force-stop offer and owned QProcess terminate/kill of resistant worker retain previous active cache")
                self.phase = "delete"
                self.launch(45)
        except Exception:
            self.finish(traceback.format_exc())

    def finish(self, error=None):
        if self.finished:
            return
        self.finished = True
        self.timer.stop()
        if error:
            self.report["error"] = error
            self.report["failure_phase"] = self.phase
            self.report["session_current"] = repr(controller.SESSION.current)
            self.report["session_runs"] = list(controller.RUNS)
            if hasattr(self, "current_manifest") and self.current_manifest.is_file():
                self.report["last_manifest"] = json.loads(self.current_manifest.read_text())
        self.report["status"] = "failed" if error else "passed"
        self.report["elapsed_seconds"] = round(time.monotonic() - self.started, 3)
        controller.shutdown()
        process.ChildProcess = self.original_process
        preferences.load = self.original_preferences
        controller.Run.transition = self.original_transition
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        print("SHOTBOX_GUI_WORKFLOW_" + self.report["status"].upper() + " " + str(self.report_path), flush=True)
        if error:
            print(error, flush=True)
        nuke.scriptClear(ignoreUnsavedChanges=True)
        nuke.scriptExit()


def start():
    global _workflow
    _workflow = Workflow()
    try:
        _workflow.setup()
    except Exception:
        _workflow.finish(traceback.format_exc())


if __name__ == "__main__":
    start()
