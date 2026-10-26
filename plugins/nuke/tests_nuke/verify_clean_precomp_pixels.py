"""Every-pixel source/alpha/render checks after successful bulk image cleanup."""
import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "toolbox" / "shotbox_matte"))
import numpy as np  # noqa: E402
from shotbox_matte.imaging import SpatialPlan, prepare_spatial, read_exr, restore_spatial  # noqa: E402
from verify_precomp_pixels import channels, read  # noqa: E402


def same_file_pixels(path, pixels, names):
    actual, actual_names = read(path)
    assert set(actual_names) == set(names)
    assert np.array_equal(channels(actual, actual_names, names), pixels), str(path)


def matte_pixels(path, alpha):
    pixels, names = read(path)
    for channel in ("R", "G", "B"):
        assert np.array_equal(channels(pixels, names, [channel]), alpha), (str(path), channel)
    assert np.all(channels(pixels, names, ["A"]) == 1), str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--reopen-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    reopened = json.loads(args.reopen_report.read_text())
    report = {"inspection": "independent OpenImageIO arrays; exact equality, every pixel/channel", "frames": []}
    try:
        assert evidence["status"] == reopened["status"] == "passed"
        source_path = Path(evidence["replay_source_manifest"])
        source = json.loads(source_path.read_text())
        for version_index, version in enumerate(evidence["completed_versions"]):
            receipt = json.loads(Path(version["receipt"]).read_text())
            directory = Path(receipt["storage"]["output_directory"])
            plan = SpatialPlan(**evidence["replay_provenance"][version_index]["spatial"])
            assert set(directory.iterdir()) == {directory / record["path"] for record in receipt["precomp"]["files"]}
            for index, frame in enumerate(range(evidence["range"][0], evidence["range"][1] + 1)):
                final_path = directory / receipt["precomp"]["files"][index]["path"]
                pixels, names = read(final_path)
                plate_path = evidence["source_pattern"] % frame
                plate, plate_names = read(plate_path)
                assert np.array_equal(channels(pixels, names, ["R", "G", "B"]), channels(plate, plate_names, ["R", "G", "B"])), (frame, "Original RGB changed")
                native_matte = source_path.parent / source["outputs"][index]["path"]
                expected_alpha = restore_spatial(prepare_spatial(read_exr(native_matte), plan), plan)[..., :1]
                alpha = channels(pixels, names, ["A"])
                assert np.array_equal(alpha, expected_alpha), (frame, "Replayed matte alpha changed")
                assert np.all(channels(pixels, names, ["shotboxAux.value"]) == 42)
                same_file_pixels(version["rendered_pattern"] % frame, pixels, names)
                if index == 0:
                    same_file_pixels(version["group_rendered_pattern"] % frame, pixels, names)
                    matte_pixels(version["matte_rendered_pattern"] % frame, alpha)
                if version_index == len(evidence["completed_versions"]) - 1:
                    same_file_pixels(reopened["rendered_pattern"] % frame, pixels, names)
                    same_file_pixels(reopened["group_rendered_pattern"] % frame, pixels, names)
                    matte_pixels(reopened["matte_rendered_pattern"] % frame, alpha)
                report["frames"].append({"frame": frame, "precomp": str(final_path), "source": plate_path,
                    "expected_replay_source_matte": str(native_matte), "rgb_exact": True, "alpha_exact": True,
                    "auxiliary_exact": True, "dimensions": list(pixels.shape[:2][::-1]), "channels": names})
        report.update(status="passed", passed=[
            "every RGB pixel in both clean versions exactly matches original ACEScg source",
            "every alpha pixel exactly matches the explicitly replayed/resampled native-matte fixture",
            "auxiliary channel preserved at 42 everywhere",
            "actual GUI Read/Group renders exactly match final precomp after intermediate deletion",
            "fresh headless Read/Group renders and matte-only views match every pixel after cleanup"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_CLEAN_PRECOMP_PIXELS_PASS " + str(args.report))


if __name__ == "__main__":
    main()
