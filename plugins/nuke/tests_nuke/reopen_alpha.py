"""Fresh native alpha-only Read, matte preview and legacy RGBA compatibility."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, node, readback, render  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    report = {"host": capture.host_identity(), "native_gpu_inference": False, "passed": []}
    try:
        assert evidence["status"] == "passed" and not nuke.GUI
        os.environ["SHOTBOX_PYTHON"] = "/deliberately-unavailable-shotbox-python"
        shotbox_nuke.register()
        first, last = evidence["range"]
        receipt = Path(evidence["completed_versions"][-1]["receipt"])
        assert set(receipt.parent.iterdir()) == {receipt}
        for _ in range(2):
            nuke.scriptOpen(evidence["review_script"])
            group = nuke.toNode("ShotBoxMatte_bam030")
            assert cache.validate_cache(group["sb_active_manifest"].value())["valid"]
            shotbox_nuke.reload_result(group)
            read = readback.find(group)
            assert read is not None and read["sb_read_id"].value() == evidence["external_read"]["id"]
            assert read["raw"].value() and set(read.channels()) == {"rgba.alpha"}
            assert len([item for item in nuke.allNodes(recurseGroups=True) if item.knob("sb_read_owner")]) == 1
            assert nuke.toNode("ExistingDownstream").input(0) is read
            read["reload"].execute()
        directory = Path(evidence["review_script"]).parent / "alpha-reopened-render"
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
            return str(output)

        group_pattern = execute("matte-preview", group)
        group["sb_output"].setValue("Original RGB + generated alpha")
        legacy_choice = execute("legacy-choice-preview", group)
        nuke.delete(group)
        read_pattern = execute("standalone-alpha", read)
        assert set(receipt.parent.iterdir()) == {receipt}
        assert not any(name.startswith(("torch", "shotbox_matte", "PyQt")) for name in sys.modules)
        assert "shotbox_nuke.process" not in sys.modules
        report["passed"].extend(["scene opens twice, Reload works and one raw alpha-only Read retains its identity and downstream connection",
            "cached Group previews render in both preview choices after cleanup with external interpreter unavailable",
            "standalone alpha Read renders after deleting the generator Group without loading inference/process modules"])
        # The previous RGBA publication is a retained historical test fixture.
        legacy_report = json.loads(Path("docs/nuke-clean-precomp-gui.json").read_text())
        legacy_receipt = legacy_report["completed_versions"][-1]["receipt"]
        nuke.scriptClear(ignoreUnsavedChanges=True)
        legacy_group = node.create()
        node.activate(legacy_group, legacy_receipt)
        legacy_group["sb_output"].setValue("Original RGB + generated alpha")
        assert {"rgba.red", "rgba.green", "rgba.blue", "rgba.alpha", "shotboxAux.value"}.issubset(legacy_group.channels())
        legacy_pattern = execute("legacy-rgba", legacy_group)
        report["passed"].append("a retained legacy RGBA precomp still activates and renders original RGB, generated alpha and auxiliary channels")
        report.update(status="passed", range=[first, last], receipt=str(receipt), rendered_pattern=read_pattern,
            matte_rendered_pattern=group_pattern, legacy_choice_rendered_pattern=legacy_choice,
            legacy_rgba_receipt=legacy_receipt, legacy_rgba_rendered_pattern=legacy_pattern)
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_ALPHA_REOPEN_PASS " + str(args.report))


if __name__ == "__main__":
    main()
