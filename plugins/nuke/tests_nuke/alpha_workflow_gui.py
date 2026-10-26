"""Native alpha-only delivery acceptance with explicit CPU matte replay."""
import json
import os
from pathlib import Path
import sys
import traceback

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from clean_precomp_workflow_gui import CleanPrecompWorkflow  # noqa: E402
from workflow_gui import Workflow  # noqa: E402
import nuke  # noqa: E402
from shotbox_nuke import cache, capture, controller, precomp, preferences, process, readback, render  # noqa: E402

_workflow = None


class AlphaWorkflow(CleanPrecompWorkflow):
    def launch(self, seed):
        if not hasattr(self, "alpha_installed"):
            self.alpha_installed = True
            self.group["sb_precomp_name"].setValue("bam030_alpha_only")
            assert self.group["sb_color"].value() == "acescg"
            assert self.group["sb_output"].value() == "Matte only"
            self.downstream = nuke.nodes.NoOp(name="ExistingDownstream", inputs=[self.group])
            self.slots = nuke.nodes.Merge2(name="ExistingTwoSlots", inputs=[self.group, self.group])
            self.jobs_root = self.directory / "jobs"
            self.group["sb_cache_root"].setValue(str(self.jobs_root))
            prefs = preferences.load()
            prefs["cache_root"] = str(self.jobs_root)
            preferences.load = lambda: dict(prefs)
            self.original_execute = nuke.execute
            self.original_capture = capture.capture
            self.original_publish = precomp.publish
            self.acceptance_render = False
            self.report.update(native_gpu_inference=False, completed_versions=[], capture_calls=[],
                execute_calls=[], backend_outputs=[], replay_provenance=[],
                replay_source_manifest=os.environ["SHOTBOX_READBACK_REPLAY_MANIFEST"],
                fixture="actual Nuke RGB-only capture, CLI/export, direct alpha promotion and Read; explicit CPU replay, no new inference")
            workflow = self

            def guarded_execute(write, *args, **kwargs):
                if not workflow.acceptance_render:
                    active = controller.SESSION.current
                    assert active is not None and active.stage == "capturing", "Nuke execution attempted outside foreground capture"
                    assert write.name().startswith("ShotBoxCaptureWrite"), write.name()
                    workflow.report["execute_calls"].append({"phase": workflow.phase, "node": write.name(),
                        "stage": active.stage, "channels": write["channels"].value()})
                return workflow.original_execute(write, *args, **kwargs)

            def recorded_capture(group, attempt, snapshot):
                assert snapshot["mode"] == "alpha"
                result = workflow.original_capture(group, attempt, snapshot)
                root = Path(attempt)
                assert not (root / "source_v001").exists()
                records = [str(path.relative_to(root)) for path in root.rglob("*.exr")]
                assert len(records) == workflow.count
                assert all(path.startswith("capture_v001/") for path in records)
                workflow.report["capture_calls"].append({"phase": workflow.phase, "mode": snapshot["mode"], "files": records,
                    "source_snapshot_created": False})
                return result

            def forbidden_publish(*args, **kwargs):
                raise AssertionError("Legacy RGB+alpha Nuke bake was called")

            nuke.execute = guarded_execute
            capture.capture = recorded_capture
            precomp.publish = forbidden_publish
            original = process.ChildProcess

            class FixtureProcess(original):
                def __init__(self, python, application, attempt, arguments, terminal, progress, done):
                    if terminal == "result":
                        worker = Path(arguments[-1])
                        assert json.loads(worker.read_text())["output_channels"] == "alpha"
                        arguments = [str(HERE / "replay_worker.py"), str(worker), workflow.report["replay_source_manifest"]]
                    original_done = done

                    def recorded_done(event, error, code):
                        root = Path(attempt)
                        if terminal == "result" and (root / "replay_fixture.json").is_file():
                            workflow.report["replay_provenance"].append(json.loads((root / "replay_fixture.json").read_text()))
                        if terminal == "validation" and not error:
                            records = event["report"]["outputs"]
                            copied = []
                            for record in records:
                                path = cache.resolve_owned(root, record["path"])
                                checksum = cache.sha256_file(path)
                                assert checksum == record["sha256"]
                                copied.append({"path": str(path), "sha256": checksum, "size": path.stat().st_size})
                            workflow.report["backend_outputs"].append(copied)
                        original_done(event, error, code)

                    super().__init__(python, application, attempt, arguments, terminal, progress, recorded_done)

            process.ChildProcess = FixtureProcess
        self.group["sb_seed"].setValue(seed)
        controller.generate(self.group, prompt_name=False)
        self.current_manifest = Path(self.group["sb_pending_manifest"].value())
        self.current_run = controller.RUNS[self.group["sb_uuid"].value()]
        self.job_directory = Path(self.current_run.directory)

    def complete(self):
        data = cache.read_manifest(self.group["sb_active_manifest"].value(), require_complete=True)
        self.current_manifest = Path(self.group["sb_active_manifest"].value())
        assert data["precomp"]["kind"] == "alpha"
        assert data["precomp"]["channels"] == ["rgba.alpha"]
        assert data["capture"]["mode"] == "alpha" and not data["capture"].get("source_files")
        validated = cache.validate_cache(self.current_manifest, checksums=True)
        final_files = [Path(path) for path in validated["precomp_files"]]
        final_directory = Path(validated["output_dir"])
        assert set(final_directory.iterdir()) == set(final_files)
        assert set(self.job_directory.iterdir()) == {self.current_manifest}
        backend = self.report["backend_outputs"][-1]
        assert [cache.sha256_file(path) for path in final_files] == [record["sha256"] for record in backend]
        assert [path.stat().st_size for path in final_files] == [record["size"] for record in backend]
        assert len([item for item in self.report["execute_calls"] if item["phase"] == self.phase]) == 1
        assert len([item for item in self.report["capture_calls"] if item["phase"] == self.phase]) == 1
        read = readback.find(self.group)
        assert read is not None and read["raw"].value()
        assert self.downstream.input(0) is read and self.slots.input(0) is read and self.slots.input(1) is read
        assert nuke.toNode("PrecompReview").input(0) is read
        if hasattr(self, "read_identity"):
            assert read["sb_read_id"].value() == self.read_identity
        else:
            self.read_identity = read["sb_read_id"].value()
        render.validate_node(self.group)
        read["reload"].execute()
        self.acceptance_render = True
        try:
            rendered = self.write_reference("read", read, self.start_frame, self.end_frame)
            matte = self.write_reference("group-matte", self.group, self.start_frame, self.end_frame)
            self.group["sb_output"].setValue("Original RGB + generated alpha")
            legacy = self.write_reference("group-legacy-choice", self.group, self.start_frame, self.start_frame)
            self.group["sb_output"].setValue("Matte only")
        finally:
            self.acceptance_render = False
        hashes = {str(path): cache.sha256_file(path) for path in final_files}
        self.report["completed_versions"].append({"phase": self.phase, "receipt": str(self.current_manifest),
            "output_directory": str(final_directory), "files_sha256": hashes,
            "rendered_pattern": rendered, "matte_rendered_pattern": matte, "legacy_choice_rendered_pattern": legacy,
            "backend_outputs": backend, "kind": "alpha", "channels": ["A"], "remaining_job_files": ["integration.json"]})
        self.report["passed"].append(self.phase + ": one foreground RGB capture, no source snapshot or post-inference Nuke execution; alpha EXRs promoted byte-for-byte and normal Read installed")
        if self.phase == "first":
            self.original_hashes = hashes
            self.original_directory = final_directory
            self.phase = "regenerate"
            self.launch(42)
            return
        assert final_directory != self.original_directory
        assert all(cache.sha256_file(path) == checksum for path, checksum in self.original_hashes.items())
        self.report["external_read"] = {"name": read.fullName(), "id": self.read_identity, "file": read["file"].value()}
        self.report["passed"].append("regeneration keeps previous final-file hashes and reuses the normal raw alpha Read")
        nuke.scriptSave()
        self.finish()

    def poll(self):
        if self.finished:
            return
        try:
            import time
            assert time.monotonic() - self.started < self.timeout_seconds, "Alpha acceptance timed out"
            if controller.SESSION.current is None:
                self.complete()
        except Exception:
            self.finish(traceback.format_exc())

    def finish(self, error=None):
        if hasattr(self, "original_execute"):
            nuke.execute = self.original_execute
            capture.capture = self.original_capture
            precomp.publish = self.original_publish
        Workflow.finish(self, error)


def start():
    global _workflow
    _workflow = AlphaWorkflow()
    try:
        _workflow.setup()
    except Exception:
        _workflow.finish(traceback.format_exc())


if __name__ == "__main__":
    start()
