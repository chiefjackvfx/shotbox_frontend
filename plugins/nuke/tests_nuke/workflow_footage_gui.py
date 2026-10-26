"""Real native GUI generation on explicitly ACEScg user-supplied EXRs.

Launch with the same temporary menu.py QTimer pattern as workflow_gui.py.
Requires SHOTBOX_GUI_SOURCE, SHOTBOX_WORKFLOW_CACHE, SHOTBOX_WORKFLOW_REPORT;
SHOTBOX_GUI_HEIGHT defaults to 384. This never writes to the source directory.
"""
import os
from pathlib import Path
import re
import sys
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["SHOTBOX_GUI_NATIVE"] = "1"
os.environ.setdefault("SHOTBOX_WORKFLOW_TIMEOUT", "1800")

from workflow_gui import APPLICATION, Workflow, close  # noqa: E402
import nuke  # noqa: E402
from shotbox_nuke import cache, lifecycle, node, preferences, render  # noqa: E402

_workflow = None


class FootageWorkflow(Workflow):
    def setup(self):
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and nuke.GUI
        assert not nuke.env.get("nc") and not nuke.env.get("indie")
        lifecycle.register()
        source_directory = Path(os.environ["SHOTBOX_GUI_SOURCE"]).resolve()
        files = sorted(source_directory.glob("*.exr"))
        assert files, "Source EXR sequence was not found"
        matches = [re.fullmatch(r"(.*?)(\d+)\.exr", path.name) for path in files]
        assert all(matches), "Source names must end in an integer frame number"
        prefix = matches[0][1]
        padding = len(matches[0][2])
        frames = [int(match[2]) for match in matches]
        assert frames == list(range(frames[0], frames[-1] + 1))
        assert all(match[1] == prefix and len(match[2]) == padding for match in matches)
        self.start_frame, self.end_frame = frames[0], frames[-1]
        self.count = len(frames)
        assert 1 <= self.count <= 250
        # Prefix includes its own separator, so add no extra dot before %.
        source_pattern = source_directory / (prefix + "%0" + str(padding) + "d.exr")
        nuke.root()["first_frame"].setValue(self.start_frame)
        nuke.root()["last_frame"].setValue(self.end_frame)
        nuke.root()["fps"].setValue(24)
        self.source = nuke.nodes.Read(name="ACEScg_Source", file=str(source_pattern))
        self.source["raw"].setValue(True)
        self.source["first"].setValue(self.start_frame)
        self.source["last"].setValue(self.end_frame)
        self.source["on_error"].setValue("error")
        self.source["localizationPolicy"].setValue("off")
        width, height = self.source.width(), self.source.height()
        nuke.root()["format"].setValue(nuke.addFormat(f"{width} {height} 1 ShotBoxFootage").name())
        self.directory.mkdir(parents=True, exist_ok=True)
        prefs = self.original_preferences()
        prefs.update(schema_version=1, application_root=str(APPLICATION),
                     interpreter=str(preferences.resolve_interpreter(APPLICATION, prefs.get("interpreter") or None)),
                     cache_root=str(self.directory))
        preferences.load = lambda: dict(prefs)
        self.group = node.create()
        self.group.setInput(0, self.source)
        self.group.setName("ShotBoxMatte_bam030")
        self.group["sb_storage"].setValue("Custom cache")
        self.group["sb_color"].setValue("acescg")
        self.group["sb_height"].setValue(int(os.environ.get("SHOTBOX_GUI_HEIGHT", "384")))
        self.group["sb_precision"].setValue("half")
        self.group["sb_cache_root"].setValue(str(self.directory))
        lifecycle.reconcile()
        self.report.update(fixture="actual user ACEScg footage; unmodified native probe, worker and validation QProcess",
                           source_directory=str(source_directory), source_pattern=str(source_pattern),
                           source_color="acescg", source_read_raw=True, range=[self.start_frame, self.end_frame],
                           source_dimensions=[width, height], fps=24,
                           quality="Pending visual review; frame/pixel checks do not establish matte quality")
        self.phase = "success"
        nuke.frame(self.start_frame)
        nuke.root()["proxy"].setValue(True)
        self.launch(42)
        self.timer.start()

    def success(self):
        data = cache.read_manifest(self.current_manifest, require_complete=True)
        assert self.group["sb_active_manifest"].value() == str(self.current_manifest)
        assert self.source["raw"].value() and data["settings"]["source_color"] == "acescg"
        assert nuke.root()["proxy"].value() is True and nuke.frame() == self.start_frame
        nuke.root()["proxy"].setValue(False)
        first_output = cache.resolve_owned(self.current_manifest.parent, data["outputs"][0]["path"])
        patterns = [self.current_manifest.parent / "capture_v001/capture.%04d.exr",
                    first_output.with_name(first_output.name.rsplit(".", 2)[0] + ".%04d.exr")]
        reads = []
        width, height = data["capture"]["width"], data["capture"]["height"]
        positions = [(0, 0), (width-1, 0), (0, height-1), (width-1, height-1),
                     (width//2, height//2), (width//3, height//3), (2*width//3, 2*height//3)]
        try:
            for pattern in patterns:
                read = nuke.nodes.Read(file=str(pattern))
                reads.append(read)
                read["raw"].setValue(True)
                read["first"].setValue(1)
                read["last"].setValue(self.count)
                read["frame_mode"].setValue("expression")
                read["frame"].setValue(f"frame - ({self.start_frame}) + 1")
            for frame in range(self.start_frame, self.end_frame + 1):
                nuke.frame(frame)
                for x, y in positions:
                    for channel in ("red", "green", "blue"):
                        value = self.source.sample(channel, x, y)
                        close(reads[0].sample(channel, x, y), value)
                        close(self.group.sample(channel, x, y), value)
                    close(self.group.sample("alpha", x, y), reads[1].sample("red", x, y))
                assert reads[1].width() == width and reads[1].height() == height
            for frame in (self.start_frame - 1, self.end_frame + 1):
                nuke.frame(frame)
                close(self.group.sample("alpha", width//2, height//2), 0)
        finally:
            for read in reads:
                nuke.delete(read)
        render.validate_node(self.group)
        self.report["passed"].extend(["actual Nuke 17.1v2 GUI native GPU probe/capture/preflight/worker/validate/activation",
            "ACEScg source explicitly selected and Read raw enabled",
            "all selected frames captured at original full resolution",
            "seven positions per frame preserve original raw RGB and match captured RGB",
            "output alpha matches actual raw backend matte at seven positions on every timeline frame",
            "full-resolution matte geometry and positive cache/timeline mapping validated",
            "alpha zero outside selected range", "proxy and frame restored after capture",
            "strict Nuke image-decoding and full cache checksum validation"])
        self.report["attempts"].append({"phase": self.phase, "state": data["state"], "manifest": str(self.current_manifest)})
        self.report["probe"], self.report["worker_result"] = data["probe"], data["result"]
        self.report["generation"] = data["plan"]["spatial"]
        nuke.frame(self.start_frame)
        viewer = nuke.nodes.Viewer(inputs=[self.group])
        viewer.setName("ShotBoxReview")
        script = self.directory / "bam030_review.nk"
        nuke.scriptSaveAs(str(script), overwrite=True)
        self.report["review_script"] = str(script)
        self.finish()


def start():
    global _workflow
    _workflow = FootageWorkflow()
    try:
        _workflow.setup()
    except Exception:
        _workflow.finish(traceback.format_exc())


if __name__ == "__main__":
    start()
