"""Native synthetic alpha-only capture, direct promotion and cached rendering."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, freshness, node, precomp_paths, readback, render, storage  # noqa: E402
from shotbox_nuke.protocol import FrameMap  # noqa: E402
from smoke import close  # noqa: E402
from clean_precomp_smoke import render_output  # noqa: E402


def synthetic_alpha(group, jobs):
    start, end = 1001, 1003
    group["sb_start"].setValue(start)
    group["sb_end"].setValue(end)
    geometry = capture.input_geometry(group, start, end)
    snapshot = {**geometry, "start": start, "end": end, "count": 3, "fps": 24, "view": "main", "mode": "alpha"}
    attempt = cache.allocate_attempt(jobs, group["sb_uuid"].value())
    before = freshness.fingerprint(group)
    original_execute = nuke.execute
    executions = []

    def record(write, *args, **kwargs):
        executions.append(write.name())
        assert write["channels"].value() == "rgb"
        return original_execute(write, *args, **kwargs)

    nuke.execute = record
    try:
        capture.capture(group, attempt, snapshot)
    finally:
        nuke.execute = original_execute
    assert len(executions) == 1 and executions[0].startswith("ShotBoxCaptureWrite")
    assert not (attempt / "source_v001").exists()
    output = attempt / "capture_matte_v001"
    output.mkdir()
    owned = []
    try:
        source = nuke.nodes.Constant()
        owned.append(source)
        source["format"].setValue("ShotBoxAlphaSmoke")
        matte = nuke.nodes.Expression(inputs=[source])
        owned.append(matte)
        matte["channel0"].setValue("alpha")
        matte["expr0"].setValue("frame == 1 ? -0.1 : 1.25")
        write = nuke.nodes.Write(inputs=[matte])
        owned.append(write)
        write["file_type"].setValue("exr")
        write["file"].setValue(str(output / "capture_matte_v001.%04d.exr"))
        capture.configure_write(write, channels="alpha")
        original_execute(write, 1, 3, 1, views=["main"], continueOnError=False)
    finally:
        for temporary in reversed(owned):
            nuke.delete(temporary)
    worker = attempt / "worker.json"
    cache.atomic_write_json(worker, {"host_test_fixture": "Synthetic alpha EXR, no inference", "output_channels": "alpha"})
    backend = attempt / "backend_jobs/host-fixture/_shotbox_job.json"
    names = ["capture_matte_v001.%04d.exr" % index for index in range(1, 4)]
    cache.atomic_write_json(backend, {"status": "complete", "frame_count": 3, "frames": names, "output_channels": "alpha"})
    dims = {key: geometry[key] for key in ("width", "height")}
    data = {"schema_version": 1, "integration_version": node.VERSION, "node_id": group["sb_uuid"].value(),
        "attempt_id": attempt.name, "state": "complete", "created_at": cache.utc_now(), "original_attempt_root": str(attempt),
        "worker_path": "worker.json", "worker_sha256": cache.sha256_file(worker),
        "capture": {**snapshot, "files": [cache.file_record(attempt / ("capture_v001/capture.%04d.exr" % index), attempt, geometry=dims) for index in range(1, 4)]},
        "frame_map": FrameMap(start, end).as_dict(), "fingerprint": before, "settings": {"source_color": "acescg", "precision": "float"},
        "host": capture.host_identity(), "worker_exit": 0,
        "outputs": [cache.file_record(output / name, attempt, geometry=dims) for name in names],
        "validation": {"valid": True, "fixture": "synthetic native alpha; no inference"},
        "backend_manifest": backend.relative_to(attempt).as_posix(),
        "result": {"status": "complete", "frame_count": 3, "output_dir": str(output), "first_source": str(attempt / "capture_v001/capture.0001.exr"),
            "first_output": str(output / names[0]), "manifest": str(backend), "job_dir": str(backend.parent),
            "source_preview": str(backend.parent / "source.png"), "matte_preview": str(backend.parent / "matte.png")}}
    path = attempt / "integration.json"
    cache.atomic_write_json(path, data)
    assert cache.validate_cache(path)["valid"]
    return path, data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-alpha-host.json"))
    parser.add_argument("--directory", type=Path, default=Path("test-runs/nuke-alpha-host"))
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="case-", dir=args.directory.resolve()))
    report = {"host": capture.host_identity(), "fixture": "native synthetic alpha EXRs; no inference", "passed": []}
    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and not nuke.GUI
        shotbox_nuke.register()
        nuke.root()["format"].setValue(nuke.addFormat("16 12 1 ShotBoxAlphaSmoke").name())
        nuke.root()["first_frame"].setValue(1001)
        nuke.root()["last_frame"].setValue(1003)
        script = base / "shot/nuke/alpha.nk"
        script.parent.mkdir(parents=True)
        nuke.scriptSaveAs(str(script), overwrite=True)
        source = nuke.nodes.Constant(name="OriginalPlate")
        source["color"].setValue([0.25, 0.5, 0.75, 0.9])
        group = node.create()
        group.setName("AlphaSynthetic")
        group.setInput(0, source)
        assert group["sb_color"].value() == "acescg" and group["sb_output"].value() == "Matte only"
        group["sb_precomp_name"].setValue("alpha_subject")
        downstream = nuke.nodes.NoOp(name="AlphaDownstream", inputs=[group])
        versions = []
        previous = {}
        for _ in range(2):
            receipt, data = synthetic_alpha(group, base / "jobs")
            backend_hashes = [record["sha256"] for record in data["outputs"]]
            original_execute = nuke.execute

            def forbidden(*args, **kwargs):
                raise AssertionError("Alpha publication attempted a Nuke bake")

            nuke.execute = forbidden
            try:
                data["precomp"] = storage.promote_alpha(receipt.parent, data)
                assert data["precomp"]["kind"] == "alpha"
                target = precomp_paths.allocate(script, "alpha_subject")
                storage.commit(receipt.parent, data, output_directory=target["directory"],
                    output_pattern=Path(str(target["pattern"]).replace("####", "%04d")).name)
                node.activate(group, receipt)
                read = readback.install(group, receipt)
                storage.cleanup(receipt.parent, data)
            finally:
                nuke.execute = original_execute
            assert set(receipt.parent.iterdir()) == {receipt}
            validated = cache.validate_cache(receipt, checksums=True)
            final_files = [Path(path) for path in validated["precomp_files"]]
            assert [cache.sha256_file(path) for path in final_files] == backend_hashes
            assert set(target["directory"].iterdir()) == set(final_files)
            assert downstream.input(0) is read and read["raw"].value()
            assert set(read.channels()) == {"rgba.alpha"}, read.channels()
            nuke.frame(1001)
            close(read.sample("alpha", 5, 5), -0.1)
            close(group.sample("red", 5, 5), -0.1)
            group["sb_output"].setValue("Original RGB + generated alpha")
            close(group.sample("red", 5, 5), -0.1)
            nuke.frame(1003)
            close(read.sample("alpha", 5, 5), 1.25)
            close(group.sample("red", 5, 5), 1.25)
            shotbox_nuke.reload_result(group)
            read["reload"].execute()
            render.validate_node(group)
            assert all(cache.sha256_file(path) == value for path, value in previous.items())
            hashes = {str(path): cache.sha256_file(path) for path in final_files}
            versions.append({"receipt": str(receipt), "output_directory": str(target["directory"]), "files_sha256": hashes, "backend_sha256": backend_hashes})
            previous.update(hashes)
        report["passed"].extend(["new nodes default to ACEScg and Matte only", "one RGB capture pass creates no full-source snapshot",
            "direct alpha promotion performs no Nuke execution and preserves every original backend EXR byte hash",
            "normal raw Read contains alpha only; negative and above-one float alpha remain unclamped; Group shows matte for both output choices",
            "two clean versions preserve previous EXRs, compact receipts validate and Reload works after cleanup"])
        directory = base / "renders"
        directory.mkdir()
        os.environ["SHOTBOX_PYTHON"] = "/deliberately-unavailable-shotbox-python"
        read_render = render_output(read, directory, "standalone")
        group_render = render_output(group, directory, "group")
        nuke.scriptSave()
        nuke.scriptOpen(str(script))
        group = nuke.toNode("AlphaSynthetic")
        read = readback.find(group)
        assert read is not None and set(read.channels()) == {"rgba.alpha"}
        shotbox_nuke.reload_result(group)
        render_output(group, directory, "reopened")
        assert not any(name.startswith(("torch", "shotbox_matte", "PyQt")) for name in sys.modules)
        report["passed"].append("native save/reopen, farm preflight and actual cached Read/Group renders work with interpreter unavailable and no inference imports")
        report.update(status="passed", versions=versions, script=str(script), read_render=read_render, group_render=group_render)
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_ALPHA_HOST_PASS " + str(args.report))


if __name__ == "__main__":
    main()
