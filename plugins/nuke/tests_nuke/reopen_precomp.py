"""Fresh headless reopen/render of precomp without external Python or live input."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, render  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    assert evidence["status"] == "passed"
    os.environ["SHOTBOX_PYTHON"] = "/deliberately-unavailable-shotbox-python"
    report = {"host": capture.host_identity(), "review_script": evidence["review_script"],
              "external_interpreter": "deliberately unavailable", "native_gpu_inference": False}
    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and not nuke.GUI
        shotbox_nuke.register()
        nuke.scriptOpen(evidence["review_script"])
        group = nuke.toNode("ShotBoxMatte_bam030")
        assert group is not None and group["sb_state"].value() == "Precomped"
        render.validate_node(group)
        assert cache.validate_cache(group["sb_active_manifest"].value())["valid"]
        assert group.node("SBPrecomp")["file"].value() == group["sb_precomp_path"].value()
        live_script = Path(evidence["review_script"]).with_name("bam030_saved_live.nk")
        group["sb_use_precomp"].setValue(False)
        assert group["sb_state"].value() == "Bypass"
        nuke.scriptSaveAs(str(live_script), overwrite=True)
        nuke.scriptOpen(str(live_script))
        group = nuke.toNode("ShotBoxMatte_bam030")
        assert not group["sb_use_precomp"].value(), "Reopen must preserve the artist's live bypass choice"
        assert group["sb_state"].value() == "Bypass"
        group["sb_use_precomp"].setValue(True)
        assert group["sb_state"].value() == "Precomped"
        # Finished precomp needs no live upstream graph, exactly as a native Read.
        group.setInput(0, None)
        render.validate_node(group)
        first, last = evidence["range"]
        output = Path(evidence["review_script"]).parent / "cached-render/verify.%04d.exr"
        output.parent.mkdir(exist_ok=True)
        write = nuke.nodes.Write(name="PrecompCachedFarmWrite", inputs=[group])
        write["file"].setValue(str(output))
        write["file_type"].setValue("exr")
        write["channels"].setValue("all")
        write["datatype"].setValue("32 bit float")
        write["raw"].setValue(True)
        render.farm_preflight(write=write, start=first, end=last)
        nuke.execute(write, first, last, 1, views=["main"], continueOnError=False)
        assert all(Path(str(output) % frame).is_file() for frame in range(first, last + 1))
        assert not any(name.startswith(("torch", "shotbox_matte", "PyQt")) for name in sys.modules)
        assert "shotbox_nuke.process" not in sys.modules
        report.update(status="passed", range=[first, last], rendered_pattern=str(output),
                      passed=["saved precomp automatically reopens active in exact Nuke 17.1v2 headless process",
                              "saving and reopening an artist-selected live bypass preserves the false toggle and Bypass state",
                              "strict cache checksums and Nuke image decoding pass",
                              "disconnected live input does not block validated precomp render",
                              "all selected timeline frames render using published RGBA+auxiliary Read with external interpreter unavailable",
                              "no ShotBox backend, Torch, PyQt or process adapter imported"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SHOTBOX_PRECOMP_REOPEN_PASS " + str(args.report))


if __name__ == "__main__":
    main()
