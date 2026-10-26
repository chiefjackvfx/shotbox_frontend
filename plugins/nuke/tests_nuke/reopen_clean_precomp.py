"""Fresh cached render/reload after every intermediate image has been removed."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, readback, render  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    report = {"host": capture.host_identity(), "native_gpu_inference": False, "external_interpreter": "deliberately unavailable"}
    try:
        assert evidence["status"] == "passed" and not nuke.GUI
        os.environ["SHOTBOX_PYTHON"] = "/deliberately-unavailable-shotbox-python"
        shotbox_nuke.register()
        first, last = evidence["range"]
        latest = evidence["completed_versions"][-1]
        receipt = Path(latest["receipt"])
        assert set(receipt.parent.iterdir()) == {receipt}
        for _ in range(2):
            nuke.scriptOpen(evidence["review_script"])
            group = nuke.toNode("ShotBoxMatte_bam030")
            assert Path(group["sb_active_manifest"].value()) == receipt
            assert cache.validate_cache(receipt, checksums=True)["valid"]
            shotbox_nuke.reload_result(group)
            read = readback.find(group)
            assert read is not None and read["sb_read_id"].value() == evidence["external_read"]["id"]
            assert len([candidate for candidate in nuke.allNodes(recurseGroups=True) if candidate.knob("sb_read_owner") is not None]) == 1
            assert nuke.toNode("ExistingDownstream").input(0) is read
            read["reload"].execute()
        directory = Path(evidence["review_script"]).parent / "post-cleanup-render"
        directory.mkdir(exist_ok=True)

        def execute(label, upstream):
            output = directory / (label + ".%04d.exr")
            write = nuke.nodes.Write(inputs=[upstream])
            try:
                write["file_type"].setValue("exr")
                write["file"].setValue(str(output))
                capture.configure_write(write, channels="all")
                render.farm_preflight(write=write, start=first, end=last)
                nuke.execute(write, first, last, 1, views=["main"], continueOnError=False)
            finally:
                nuke.delete(write)
            assert all(Path(str(output) % frame).is_file() for frame in range(first, last + 1))
            return str(output)

        group_render = execute("group", group)
        group["sb_output"].setValue("Matte only")
        matte_render = execute("matte-only", group)
        group["sb_output"].setValue("Original RGB + generated alpha")
        nuke.delete(group)
        read_render = execute("standalone-read", read)
        assert set(receipt.parent.iterdir()) == {receipt}
        assert not any(name.startswith(("torch", "shotbox_matte", "PyQt")) for name in sys.modules)
        assert "shotbox_nuke.process" not in sys.modules
        report.update(status="passed", receipt=str(receipt), range=[first, last],
            group_rendered_pattern=group_render, matte_rendered_pattern=matte_render, rendered_pattern=read_render,
            passed=["saved scene opens twice and explicit Reload validates compact receipt with no intermediate files",
                    "native Read identity and downstream connection persist without duplicate Reads",
                    "explicit farm preflight and all-frame Group/matte-only renders work after cleanup",
                    "standalone Read renders after deleting generator Group with external interpreter unavailable",
                    "no inference backend, Torch, PyQt or process adapter imported; receipt-only job folder remains unchanged"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_CLEAN_PRECOMP_REOPEN_PASS " + str(args.report))


if __name__ == "__main__":
    main()
