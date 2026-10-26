"""Actual GUI/GPU acceptance of project precomp publication and automatic Read.

Use launch_gui_test.py --precomp --source ... --cache test-runs/nuke-precomp.
Writes only beneath the explicitly selected test directory, never the plate path.
"""
import hashlib
import os
from pathlib import Path
import re
import sys
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHOTBOX_GUI_NATIVE"] = "1"

from workflow_gui import APPLICATION, Workflow, close  # noqa: E402
import nuke  # noqa: E402
from shotbox_nuke import cache, controller, lifecycle, node, preferences, process, render  # noqa: E402

_workflow = None


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


class PrecompWorkflow(Workflow):
    def setup(self):
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and nuke.GUI
        assert not nuke.env.get("nc") and not nuke.env.get("indie")
        lifecycle.register()
        workflow = self

        class RecordedProcess(self.original_process):
            def __init__(self, python, application, attempt, arguments, terminal, progress, done):
                super().__init__(python, application, attempt, arguments, terminal, progress, done)
                entry = {"terminal": terminal, "arguments": list(arguments), "attempt": str(attempt)}
                workflow.report["processes"].append(entry)
                self.process.started.connect(lambda: entry.update(pid=int(self.process.processId())))

        process.ChildProcess = RecordedProcess

        def transition(run, stage):
            workflow.report["stage_events"].append({"attempt": run.owner.attempt_id, "stage": stage})
            return workflow.original_transition(run, stage)

        controller.Run.transition = transition
        source_directory = Path(os.environ["SHOTBOX_GUI_SOURCE"]).resolve()
        files = sorted(source_directory.glob("*.exr"))
        matches = [re.fullmatch(r"(.*?)(\d+)\.exr", path.name) for path in files]
        assert files and all(matches)
        prefix, padding = matches[0][1], len(matches[0][2])
        frames = [int(match[2]) for match in matches]
        assert frames == list(range(frames[0], frames[-1] + 1))
        assert all(match[1] == prefix and len(match[2]) == padding for match in matches)
        self.start_frame = frames[0]
        self.count = min(len(frames), int(os.environ.get("SHOTBOX_PRECOMP_FRAMES", "3")))
        self.end_frame = self.start_frame + self.count - 1
        self.script = self.directory / "shot/nuke/bam030.nk"
        self.script.parent.mkdir(parents=True, exist_ok=True)
        nuke.root()["first_frame"].setValue(self.start_frame)
        nuke.root()["last_frame"].setValue(self.end_frame)
        nuke.root()["fps"].setValue(24)
        nuke.root()["proxy"].setValue(False)
        self.source = nuke.nodes.Read(name="ACEScg_Source", file=str(source_directory / (prefix + "%0" + str(padding) + "d.exr")))
        self.source["raw"].setValue(True)
        self.source["first"].setValue(frames[0])
        self.source["last"].setValue(frames[-1])
        self.source["localizationPolicy"].setValue("off")
        width, height = self.source.width(), self.source.height()
        nuke.root()["format"].setValue(nuke.addFormat(f"{width} {height} 1 ShotBoxPrecomp").name())
        nuke.Layer("shotboxAux", ["shotboxAux.value"])
        auxiliary = nuke.nodes.Constant(name="Auxiliary42")
        auxiliary["color"].setValue([42, 42, 42, 1])
        auxiliary["format"].setValue(nuke.root()["format"].value())
        source_with_aux = nuke.nodes.Copy(name="PreserveAuxiliary", inputs=[self.source, auxiliary])
        source_with_aux["from0"].setValue("rgba.red")
        source_with_aux["to0"].setValue("shotboxAux.value")
        for key in ("bbox", "metainput", "rangeinput"):
            source_with_aux[key].setValue("B")
        self.grade = nuke.nodes.Grade(name="UpstreamGrade", inputs=[source_with_aux])
        prefs = self.original_preferences()
        prefs.update(schema_version=1, application_root=str(APPLICATION),
                     interpreter=str(preferences.resolve_interpreter(APPLICATION, prefs.get("interpreter") or None)),
                     cache_root="")
        preferences.load = lambda: dict(prefs)
        nuke.scriptSaveAs(str(self.script), overwrite=True)
        self.group = node.create()
        self.group.setInput(0, self.grade)
        self.group.setName("ShotBoxMatte_bam030")
        self.group["sb_precomp_name"].setValue("bam030_alpha")
        self.group["sb_color"].setValue("acescg")
        self.group["sb_height"].setValue(int(os.environ.get("SHOTBOX_GUI_HEIGHT", "384")))
        self.group["sb_precision"].setValue("float")
        lifecycle.reconcile()
        nuke.nodes.Viewer(name="PrecompReview", inputs=[self.group])
        assert self.group["sb_storage"].value() == "Project precomp"
        assert self.group["sb_state"].value() == "Bypass"
        nuke.frame(self.start_frame)
        for channel in ("red", "green", "blue", "alpha", "shotboxAux.value"):
            close(self.group.sample(channel, width//2, height//2), self.grade.sample(channel, width//2, height//2))
        nuke.scriptSave()
        self.report.update(fixture="actual ACEScg user footage with auxiliary channel; real native GPU and actual Nuke GUI",
                           source_directory=str(source_directory), source_pattern=self.source["file"].value(),
                           source_color="acescg", range=[self.start_frame, self.end_frame], source_dimensions=[width, height],
                           review_script=str(self.script), published_versions=[], quality="Technical workflow acceptance; no production matte quality claim")
        self.report["passed"].append("new node passes live original input including alpha and auxiliary channel before Generate")
        self.phase = "first"
        self.launch(42)
        assert self.group["sb_state"].value() == "Rendering"
        self.timer.start()

    def success(self):
        data = cache.read_manifest(self.current_manifest, require_complete=True)
        assert self.group["sb_active_manifest"].value() == str(self.current_manifest)
        assert self.group["sb_state"].value() == "Precomped"
        assert self.group["sb_use_precomp"].value()
        render.validate_node(self.group)
        precomp = data["precomp"]
        first = cache.resolve_owned(self.current_manifest.parent, precomp["files"][0]["path"])
        assert first.parent.parent == (self.script.parent / "../renders/precomp").resolve()
        assert re.fullmatch(r"bam030_alpha_v\d{2,}", first.parent.name), first
        assert first.name == f"{first.parent.name}.{self.start_frame:04d}.exr"
        assert len(precomp["files"]) == self.count
        assert (precomp["first"], precomp["last"]) == (self.start_frame, self.end_frame)
        assert "shotboxAux.value" in precomp["channels"]
        # Native Node.sample is not a reliable GUI time-context oracle in this
        # host build. Render real Write outputs, then inspect every pixel with
        # verify_precomp_pixels.py in an independent OpenImageIO process.
        rendered = self.render_reference("precomp", self.start_frame, self.end_frame)
        self.grade["multiply"].setValue(0.5)
        render.validate_node(self.group)
        frozen = self.render_reference("frozen", self.start_frame, self.start_frame)
        self.group["sb_use_precomp"].setValue(False)
        assert self.group["sb_state"].value() == "Bypass"
        bypass = self.render_reference("bypass", self.start_frame, self.start_frame)
        self.grade["multiply"].setValue(1)
        self.group["sb_use_precomp"].setValue(True)
        assert self.group["sb_state"].value() == "Precomped"
        render.validate_node(self.group)
        hashes = {str(cache.resolve_owned(self.current_manifest.parent, record["path"])): record["sha256"] for record in precomp["files"]}
        self.report["published_versions"].append({"manifest": str(self.current_manifest), "precomp": precomp, "files_sha256": hashes,
                                                    "rendered_pattern": rendered, "frozen_pattern": frozen, "bypass_pattern": bypass})
        self.report["passed"].append(f"{self.phase}: native GPU complete automatically switches to full RGBA/auxiliary precomp Read in project renders/precomp version folder")
        self.report["passed"].append(f"{self.phase}: actual GUI Write renders full selected range, frozen precomp after upstream edit, and live bypass for independent every-pixel inspection")
        if self.phase == "first":
            self.previous_files = hashes
            self.previous_directory = first.parent
            self.phase = "regenerate"
            self.launch(42)
            assert self.group["sb_state"].value() == "Rendering"
            return
        assert first.parent != self.previous_directory
        for path, checksum in self.previous_files.items():
            assert digest(path) == checksum, "Regeneration overwrote a prior precomp file"
        self.report["passed"].append("regeneration allocates a distinct next version and preserves all prior EXR byte hashes")
        self.report["probe"] = data["probe"]
        self.report["worker_result"] = data["result"]
        self.report["generation"] = data["plan"]["spatial"]
        nuke.scriptSave()
        self.finish()

    def render_reference(self, label, first, last):
        from shotbox_nuke.capture import configure_write, preserve_host
        output = self.directory / "acceptance" / self.phase / (label + ".%04d.exr")
        output.parent.mkdir(parents=True, exist_ok=True)
        with preserve_host():
            write = nuke.nodes.Write(name="PrecompAcceptanceWrite", inputs=[self.group])
            try:
                write["file"].setValue(str(output))
                write["file_type"].setValue("exr")
                configure_write(write, channels="all")
                nuke.execute(write, first, last, 1, views=["main"], continueOnError=False)
            finally:
                nuke.delete(write)
        assert all(Path(str(output) % frame).is_file() for frame in range(first, last + 1))
        return str(output)

    def poll(self):
        if self.finished:
            return
        try:
            import time
            if time.monotonic() - self.started > self.timeout_seconds:
                raise TimeoutError("GUI precomp workflow exceeded configured timeout")
            if controller.SESSION.current is None:
                self.success()
        except Exception:
            self.finish(traceback.format_exc())

    def finish(self, error=None):
        if error and hasattr(self, "group"):
            self.report["graph_diagnostic"] = {"frame": nuke.frame(), "proxy": nuke.root()["proxy"].value(), "nodes": []}
            for name in ("SBPrecomp", "SBResultMode", "SBOutputChoice", "SBHasResult", "SBPrecompSwitch", "SBOutput"):
                candidate = self.group.node(name)
                if candidate is not None:
                    self.report["graph_diagnostic"]["nodes"].append({"name": name,
                        "knobs": {key: candidate[key].value() for key in ("which", "file", "frame", "first", "last") if candidate.knob(key)},
                        "red_bottom_left": candidate.sample("red", 0, 0), "error": candidate.error()})
            failure = self.script.with_name("failure.nk")
            nuke.scriptSaveAs(str(failure), overwrite=True)
            self.report["failure_script"] = str(failure)
        super().finish(error)


def start():
    global _workflow
    _workflow = PrecompWorkflow()
    try:
        _workflow.setup()
    except Exception:
        _workflow.finish(traceback.format_exc())


if __name__ == "__main__":
    start()
