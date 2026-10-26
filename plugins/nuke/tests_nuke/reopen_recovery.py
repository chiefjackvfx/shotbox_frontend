"""Fresh-process reopening and farm render of a moved/relinked alpha result."""
import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, readback, render  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, default=Path("docs/nuke-recovery-gui.json"))
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-recovery-reopen.json"))
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    report = {"host": capture.host_identity(), "native_gpu_inference": False, "passed": []}
    try:
        assert not nuke.GUI and evidence["status"] == "passed"
        shotbox_nuke.register()
        for _ in range(2):
            nuke.scriptOpen(evidence["script"])
            group = nuke.toNode("ShotBoxRecovery")
            assert group["sb_active_manifest"].value() == evidence["relinked_receipt"]
            pending = cache.read_manifest(group["sb_pending_manifest"].value())
            assert pending["state"] == "failed"
            shotbox_nuke.reload_result(group)
            read = readback.find(group)
            assert read is not None and read["sb_read_id"].value() == evidence["output_read_id"]
            assert read["file"].value() == evidence["moved_sequence"]
            assert set(read.channels()) == {"rgba.alpha"}
            assert cache.validate_cache(group["sb_active_manifest"].value(), checksums=True)["valid"]
            assert len([candidate for candidate in nuke.allNodes() if candidate.knob("sb_read_owner")]) == 1
        directory = Path(evidence["script"]).parent / "relinked-render"
        directory.mkdir(exist_ok=True)

        def execute(label, upstream):
            pattern = directory / (label + ".%04d.exr")
            write = nuke.nodes.Write(inputs=[upstream])
            try:
                write["file_type"].setValue("exr")
                write["file"].setValue(str(pattern))
                capture.configure_write(write, channels="all")
                render.farm_preflight(write=write, start=1001, end=1003)
                nuke.execute(write, 1001, 1003, 1, views=["main"], continueOnError=False)
            finally:
                nuke.delete(write)
            return str(pattern)

        group_pattern = execute("group", group)
        nuke.delete(group)
        read_pattern = execute("read", read)
        assert not any(name.startswith(("shotbox_matte", "torch", "PyQt")) for name in sys.modules)
        report.update(status="passed", moved_sequence=evidence["moved_sequence"], group_rendered_pattern=group_pattern,
            read_rendered_pattern=read_pattern, passed=["saved moved/relinked result opens twice and Reload selects the valid active receipt despite a newer failed pending attempt",
                "owned raw alpha Read retains its identity and sequence without duplicate root Reads",
                "explicit farm preflight and all selected frames render through the Group and standalone Read after generator deletion without inference imports"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_RECOVERY_REOPEN_PASS " + str(args.report))


if __name__ == "__main__":
    main()
