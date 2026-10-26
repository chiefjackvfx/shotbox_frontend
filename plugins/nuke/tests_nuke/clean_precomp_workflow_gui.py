"""Real Nuke/CLI EXR-only delivery with explicit CPU replay, never new GPU AI."""
import json
import os
from pathlib import Path
import sys
import time
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from precomp_workflow_gui import PrecompWorkflow  # noqa: E402
from workflow_gui import Workflow  # noqa: E402
import nuke  # noqa: E402
from shotbox_nuke import cache, controller, preferences, process, readback, render  # noqa: E402

_workflow = None


class CleanPrecompWorkflow(PrecompWorkflow):
    def launch(self, seed):
        if not hasattr(self, "downstream"):
            self.downstream = nuke.nodes.NoOp(name="ExistingDownstream", inputs=[self.group])
            self.slots = nuke.nodes.Merge2(name="ExistingTwoSlots", inputs=[self.group, self.group])
            self.group["sb_precomp_name"].setValue("bam030_clean")
            self.jobs_root = self.directory / "jobs"
            self.group["sb_cache_root"].setValue(str(self.jobs_root))
            prefs = preferences.load()
            prefs["cache_root"] = str(self.jobs_root)
            preferences.load = lambda: dict(prefs)
            original = process.ChildProcess
            workflow = self
            replay = os.environ["SHOTBOX_READBACK_REPLAY_MANIFEST"]

            class FixtureProcess(original):
                def __init__(self, python, application, attempt, arguments, terminal, progress, done):
                    if terminal == "result":
                        worker = Path(arguments[-1])
                        if int(json.loads(worker.read_text())["seed"]) in (43, 44):
                            arguments = [str(HERE / "controlled_worker.py"), "worker", str(worker)]
                        else:
                            arguments = [str(HERE / "replay_worker.py"), str(worker), replay]
                    original_done = done

                    def record_done(event, error, code):
                        provenance = Path(attempt) / "replay_fixture.json"
                        if terminal == "result" and provenance.is_file():
                            workflow.report.setdefault("replay_provenance", []).append(json.loads(provenance.read_text()))
                        original_done(event, error, code)

                    super().__init__(python, application, attempt, arguments, terminal, progress, record_done)

            process.ChildProcess = FixtureProcess
            self.report.update(native_gpu_inference=False,
                fixture="actual Nuke capture/probe/preflight/CLI/export/validation/publication/Read; explicit CPU replay and controlled failure/cancel engines",
                replay_source_manifest=replay, completed_versions=[], failure_attempts=[])
        self.group["sb_seed"].setValue(seed)
        self.cancel_sent = False
        controller.generate(self.group, prompt_name=False)
        self.current_manifest = Path(self.group["sb_pending_manifest"].value())
        self.current_run = controller.RUNS[self.group["sb_uuid"].value()]
        self.job_directory = Path(self.current_run.directory)
        assert self.jobs_root.resolve() in self.job_directory.resolve().parents, self.job_directory
        self.assert_output_folders_clean()

    def assert_output_folders_clean(self):
        root = (self.script.parent / "../renders/precomp").resolve()
        if root.exists():
            for directory in root.iterdir():
                assert directory.is_dir(), directory
                for path in directory.iterdir():
                    assert path.is_file() and path.suffix == ".exr", ("Non-final artifact in artist output", path)

    def write_reference(self, label, upstream, first, last):
        from shotbox_nuke.capture import configure_write, preserve_host
        output = self.directory / "acceptance" / self.phase / (label + ".%04d.exr")
        output.parent.mkdir(parents=True, exist_ok=True)
        with preserve_host():
            write = nuke.nodes.Write(name="CleanPrecompAcceptance", inputs=[upstream])
            try:
                write["file"].setValue(str(output))
                write["file_type"].setValue("exr")
                configure_write(write, channels="all")
                nuke.execute(write, first, last, 1, views=["main"], continueOnError=False)
            finally:
                nuke.delete(write)
        assert all(Path(str(output) % frame).is_file() for frame in range(first, last + 1))
        return str(output)

    def complete(self):
        self.current_manifest = Path(self.group["sb_active_manifest"].value())
        data = cache.read_manifest(self.current_manifest, require_complete=True)
        assert data["storage"]["mode"] == "published", data.get("storage")
        validated = cache.validate_cache(self.current_manifest, checksums=True)
        files = [Path(path) for path in validated["precomp_files"]]
        assert len(files) == self.count
        final_directory = Path(validated["output_dir"])
        assert final_directory.parent == (self.script.parent / "../renders/precomp").resolve()
        assert set(final_directory.iterdir()) == set(files)
        assert all(path.name.endswith(".%04d.exr" % frame) for path, frame in zip(files, range(self.start_frame, self.end_frame + 1)))
        assert final_directory not in self.current_manifest.parents
        assert self.current_manifest != final_directory / "integration.json"
        assert not list(self.job_directory.rglob("*.exr")), "Successful job retained intermediate EXRs"
        assert not list(self.job_directory.rglob("*.png")), "Successful job retained backend preview images"
        assert not (self.job_directory / "capture_v001").exists()
        assert not (self.job_directory / "source_v001").exists()
        assert not (self.job_directory / "backend_jobs").exists()
        self.assert_output_folders_clean()
        read = readback.find(self.group)
        assert read is not None and self.downstream.input(0) is read
        assert self.slots.input(0) is read and self.slots.input(1) is read
        assert nuke.toNode("PrecompReview").input(0) is read
        if hasattr(self, "read_identity"):
            assert read["sb_read_id"].value() == self.read_identity
        else:
            self.read_identity = read["sb_read_id"].value()
        render.validate_node(self.group)
        read["reload"].execute()
        rendered = self.write_reference("read", read, self.start_frame, self.end_frame)
        group_rendered = self.write_reference("group", self.group, self.start_frame, self.start_frame)
        self.group["sb_output"].setValue("Matte only")
        try:
            matte_rendered = self.write_reference("matte-only", self.group, self.start_frame, self.start_frame)
        finally:
            self.group["sb_output"].setValue("Original RGB + generated alpha")
        hashes = {str(path): cache.sha256_file(path) for path in files}
        self.report["completed_versions"].append({"phase": self.phase, "receipt": str(self.current_manifest),
            "job_directory": str(self.job_directory), "output_directory": str(final_directory), "files_sha256": hashes,
            "rendered_pattern": rendered, "group_rendered_pattern": group_rendered, "matte_rendered_pattern": matte_rendered,
            "remaining_job_files": [str(path.relative_to(self.job_directory)) for path in self.job_directory.rglob("*") if path.is_file()]})
        self.report["passed"].append(self.phase + ": final folder contains only final EXRs; bulk removed; receipt validates and Read/Group/matte-only render after cleanup")
        if self.phase == "first":
            self.original_receipt = str(self.current_manifest)
            self.original_hashes = hashes
            self.original_read_file = read["file"].value()
            self.original_output_directory = final_directory
            self.phase = "failure"
            self.launch(43)
            return
        assert final_directory != self.original_output_directory
        self.check_previous_unchanged()
        self.report["passed"].append("regeneration publishes a distinct clean version and preserves every previous final-file checksum")
        self.report["external_read"] = {"name": read.fullName(), "id": self.read_identity, "file": read["file"].value()}
        self.report["generation"] = data.get("plan", {}).get("spatial")
        nuke.scriptSave()
        self.finish()

    def check_previous_unchanged(self):
        assert all(cache.sha256_file(path) == digest for path, digest in self.original_hashes.items())

    def poll(self):
        if self.finished:
            return
        try:
            if time.monotonic() - self.started > self.timeout_seconds:
                raise TimeoutError("Clean precomp GUI acceptance timed out")
            current = controller.SESSION.current
            if self.phase == "cancel" and current is not None and current.stage == "generating" and not self.cancel_sent:
                controller.cancel(self.group)
                self.cancel_sent = True
            if current is not None:
                return
            if self.phase in ("first", "regenerate"):
                self.complete()
                return
            expected = "failed" if self.phase == "failure" else "cancelled"
            assert self.current_run.data["state"] == expected, self.current_run.data
            assert self.group["sb_active_manifest"].value() == self.original_receipt
            read = readback.find(self.group)
            assert read["file"].value() == self.original_read_file
            self.check_previous_unchanged()
            self.assert_output_folders_clean()
            self.report["failure_attempts"].append({"phase": self.phase, "state": expected, "job_directory": str(self.job_directory)})
            self.report["passed"].append(self.phase + ": no job artifacts enter output; previous active Read and final files remain unchanged")
            self.phase = "cancel" if self.phase == "failure" else "regenerate"
            self.launch(44 if self.phase == "cancel" else 42)
        except Exception:
            self.finish(traceback.format_exc())

    def finish(self, error=None):
        if error and hasattr(self, "current_run"):
            self.report["failed_run"] = self.current_run.data
            self.report["failed_job_directory"] = str(self.job_directory)
        Workflow.finish(self, error)


def start():
    global _workflow
    _workflow = CleanPrecompWorkflow()
    try:
        _workflow.setup()
    except Exception:
        _workflow.finish(traceback.format_exc())


if __name__ == "__main__":
    start()
