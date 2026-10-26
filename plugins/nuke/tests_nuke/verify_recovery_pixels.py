"""Independent alpha/preview checks after moved-EXR Relink and saved reopen."""
import argparse
import hashlib
import json
from pathlib import Path
import traceback

import numpy as np
from verify_precomp_pixels import channels, read


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gui-report", type=Path, default=Path("docs/nuke-recovery-gui.json"))
    parser.add_argument("--reopen-report", type=Path, default=Path("docs/nuke-recovery-reopen.json"))
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-recovery-pixels.json"))
    args = parser.parse_args()
    gui = json.loads(args.gui_report.read_text())
    reopened = json.loads(args.reopen_report.read_text())
    report = {"inspection": "independent OpenImageIO, every pixel/channel", "fixture": "native synthetic alpha, no inference", "frames": []}
    try:
        assert gui["status"] == reopened["status"] == "passed"
        original = json.loads(Path(gui["original_receipt"]).read_text())
        for index, frame in enumerate(range(1001, 1004)):
            path = Path(gui["moved_sequence"] % frame)
            alpha, names = read(path)
            assert names == ["A"] and alpha.shape == (12, 16, 1)
            expected = np.float32(-0.1 if frame == 1001 else 1.25)
            assert np.all(alpha == expected)
            assert hashlib.sha256(path.read_bytes()).hexdigest() == original["precomp"]["files"][index]["sha256"]
            read_pixels, read_names = read(reopened["read_rendered_pattern"] % frame)
            assert read_names == ["A"] and np.array_equal(read_pixels, alpha)
            preview, preview_names = read(reopened["group_rendered_pattern"] % frame)
            assert set(preview_names) == {"R", "G", "B", "A"}
            for channel in ("R", "G", "B"):
                assert np.array_equal(channels(preview, preview_names, [channel]), alpha)
            assert np.all(channels(preview, preview_names, ["A"]) == 1)
            report["frames"].append({"frame": frame, "moved_file": str(path), "channels": names,
                "dimensions": [16, 12], "original_sha256_preserved": True, "alpha_exact": True,
                "reopened_read_exact": True, "reopened_group_preview_exact": True})
        report.update(status="passed", passed=["moved and renamed alpha EXRs retain every original byte hash and unclamped synthetic alpha value",
            "actual fresh-process Read renders remain A-only and match every pixel",
            "actual fresh-process Group RGB previews equal alpha everywhere with opaque preview alpha"])
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_RECOVERY_PIXELS_PASS " + str(args.report))


if __name__ == "__main__":
    main()
