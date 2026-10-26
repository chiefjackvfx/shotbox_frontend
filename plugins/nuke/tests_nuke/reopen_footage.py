"""Fresh cached farm render of the review comp; no ShotBox Python/GPU needed."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import cache, capture, freshness, render  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    report = {"host": capture.host_identity(), "review_script": evidence["review_script"],
              "external_interpreter": "deliberately unavailable", "native_gpu_inference": False}
    os.environ["SHOTBOX_PYTHON"] = "/deliberately-unavailable-shotbox-python"
    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2" and not nuke.GUI
        shotbox_nuke.register()
        nuke.scriptOpen(evidence["review_script"])
        group = nuke.toNode("ShotBoxMatte_bam030")
        assert group is not None
        validated = render.validate_node(group)
        saved = validated["manifest"]["fingerprint"]
        current = freshness.fingerprint(group)
        assert freshness.compare(saved, current) == "no detected change"
        first = validated["manifest"]["capture"]["start"]
        output = Path(evidence["review_script"]).parent / "cached-render/verify.%04d.exr"
        output.parent.mkdir(exist_ok=True)
        write = nuke.nodes.Write(name="Bam030CachedFarmWrite", inputs=[group])
        write["file"].setValue(str(output))
        write["file_type"].setValue("exr")
        write["channels"].setValue("rgba")
        write["datatype"].setValue("32 bit float")
        write["raw"].setValue(True)
        render.farm_preflight(write=write, start=first, end=first)
        nuke.execute(write, first, first, 1, views=["main"], continueOnError=False)
        assert Path(str(output) % first).is_file()
        assert not any(name.startswith(("torch", "shotbox_matte", "PyQt")) for name in sys.modules)
        assert "shotbox_nuke.process" not in sys.modules
        assert cache.validate_cache(group["sb_active_manifest"].value())["valid"]
        report.update(status="passed", fingerprint=current["digest"], rendered_frame=first,
                      rendered_file=str(output) % first,
                      passed=["saved review comp reopens with final package and matching input freshness",
                              "all cached files/hash identities and Nuke decoding validate",
                              "explicit farm preflight and cached frame render with external interpreter unavailable",
                              "no ShotBox backend, torch, PyQt or process adapter imported"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("SHOTBOX_REAL_FOOTAGE_REOPEN_PASS " + str(args.report))


if __name__ == "__main__":
    main()
