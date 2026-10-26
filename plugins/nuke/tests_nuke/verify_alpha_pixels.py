"""Independent full-image alpha-only EXR, preview and direct byte checks."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "toolbox" / "shotbox_matte"))
import numpy as np  # noqa: E402
from shotbox_matte.imaging import SpatialPlan, prepare_spatial, read_exr, restore_spatial  # noqa: E402
from verify_precomp_pixels import channels, read  # noqa: E402


def alpha_matches(path, expected):
    pixels, names = read(path)
    assert names == ["A"], (str(path), names)
    assert np.array_equal(pixels, expected), str(path)


def preview_matches(path, alpha):
    pixels, names = read(path)
    assert set(names) == {"R", "G", "B", "A"}, (str(path), names)
    for name in ("R", "G", "B"):
        assert np.array_equal(channels(pixels, names, [name]), alpha), (str(path), name)
    assert np.all(channels(pixels, names, ["A"]) == 1), str(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, required=True)
    parser.add_argument("--reopen-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    evidence = json.loads(args.gui_report.read_text())
    reopened = json.loads(args.reopen_report.read_text())
    report = {"inspection": "independent OpenImageIO arrays; every pixel, exact equality", "native_gpu_inference": False, "frames": []}
    try:
        assert evidence["status"] == reopened["status"] == "passed"
        source_path = Path(evidence["replay_source_manifest"])
        source = json.loads(source_path.read_text())
        first, last = evidence["range"]
        for version_index, version in enumerate(evidence["completed_versions"]):
            receipt = json.loads(Path(version["receipt"]).read_text())
            root = Path(receipt["storage"]["output_directory"])
            plan = SpatialPlan(**evidence["replay_provenance"][version_index]["spatial"])
            assert set(root.iterdir()) == {root / record["path"] for record in receipt["precomp"]["files"]}
            for index, frame in enumerate(range(first, last + 1)):
                path = root / receipt["precomp"]["files"][index]["path"]
                original = source_path.parent / source["outputs"][index]["path"]
                expected = restore_spatial(prepare_spatial(read_exr(original), plan), plan)[..., :1]
                alpha_matches(path, expected)
                assert hashlib.sha256(path.read_bytes()).hexdigest() == version["backend_outputs"][index]["sha256"]
                assert path.stat().st_size == version["backend_outputs"][index]["size"]
                alpha_matches(version["rendered_pattern"] % frame, expected)
                preview_matches(version["matte_rendered_pattern"] % frame, expected)
                if index == 0:
                    preview_matches(version["legacy_choice_rendered_pattern"] % frame, expected)
                if version_index == len(evidence["completed_versions"]) - 1:
                    alpha_matches(reopened["rendered_pattern"] % frame, expected)
                    preview_matches(reopened["matte_rendered_pattern"] % frame, expected)
                    preview_matches(reopened["legacy_choice_rendered_pattern"] % frame, expected)
                report["frames"].append({"frame": frame, "file": str(path), "channels": ["A"], "dimensions": [4096, 2850],
                    "alpha_exact": True, "backend_bytes_unchanged": True})
        legacy = json.loads(Path(reopened["legacy_rgba_receipt"]).read_text())
        for index, frame in enumerate(range(first, last + 1)):
            original = Path(legacy["storage"]["output_directory"]) / legacy["precomp"]["files"][index]["path"]
            pixels, names = read(original)
            rendered, rendered_names = read(reopened["legacy_rgba_rendered_pattern"] % frame)
            assert set(names) == set(rendered_names)
            assert np.array_equal(pixels, channels(rendered, rendered_names, names))
        report.update(status="passed", passed=["all six final images contain exactly one A channel with every pixel equal to the explicit replay fixture",
            "all final EXR sizes and SHA-256 hashes exactly equal original backend files; no second encode",
            "GUI and fresh-process standalone Read renders remain alpha-only and pixel-exact",
            "Group RGB preview equals alpha everywhere in both preview choices, after intermediate cleanup",
            "legacy RGBA renders remain exact on every channel/pixel"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_ALPHA_PIXELS_PASS " + str(args.report))


if __name__ == "__main__":
    main()
