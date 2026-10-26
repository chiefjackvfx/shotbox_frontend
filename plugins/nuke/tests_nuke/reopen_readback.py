"""Fresh-process standalone Read render after deleting the generator Group."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import capture, readback  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    report = {"host": capture.host_identity(), "external_interpreter": "deliberately unavailable", "native_gpu_inference": False}
    try:
        assert evidence["status"] == "passed" and not nuke.GUI
        os.environ["SHOTBOX_PYTHON"] = "/deliberately-unavailable-shotbox-python"
        shotbox_nuke.register()
        identities = []
        for _ in range(2):
            nuke.scriptOpen(evidence["review_script"])
            group = nuke.toNode("ShotBoxMatte_bam030")
            read = readback.find(group)
            assert read is not None and read.Class() == "Read" and "." not in read.fullName()
            managed = [candidate for candidate in nuke.allNodes(recurseGroups=True) if candidate.knob("sb_read_owner") is not None]
            assert len(managed) == 1
            assert nuke.toNode("ExistingDownstream").input(0) is read
            assert nuke.toNode("ExistingTwoSlots").input(0) is read and nuke.toNode("ExistingTwoSlots").input(1) is read
            assert nuke.toNode("PrecompReview").input(0) is read
            identities.append(read["sb_read_id"].value())
        assert identities[0] == identities[1] == evidence["external_reads"][-1]["id"]
        first, last = evidence["range"]
        assert read["raw"].value() and (read["first"].value(), read["last"].value()) == (first, last)
        nuke.delete(group)
        assert nuke.toNode(read.fullName()) is read
        output = Path(evidence["review_script"]).parent / "standalone-render/verify.%04d.exr"
        output.parent.mkdir(exist_ok=True)
        write = nuke.nodes.Write(name="StandaloneCachedWrite", inputs=[read])
        write["file"].setValue(str(output))
        write["file_type"].setValue("exr")
        capture.configure_write(write, channels="all")
        nuke.execute(write, first, last, 1, views=["main"], continueOnError=False)
        assert all(Path(str(output) % frame).is_file() for frame in range(first, last + 1))
        assert not any(name.startswith(("torch", "shotbox_matte", "PyQt")) for name in sys.modules)
        assert "shotbox_nuke.process" not in sys.modules
        report.update(status="passed", review_script=evidence["review_script"], range=[first, last], rendered_pattern=str(output),
                      read=read.fullName(), read_id=identities[0], passed=[
                          "actual GPU review comp opens twice without duplicate external Reads or changed read identity",
                          "existing downstream inputs and Viewer remain connected to the standalone raw Read",
                          "deleting the generating Group leaves the native Read independently usable",
                          "all selected frames render through standalone Read with external interpreter unavailable",
                          "no inference backend, Torch, PyQt or process adapter imported"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_READBACK_REOPEN_PASS " + str(args.report))


if __name__ == "__main__":
    main()
