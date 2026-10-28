"""Compare every decoded pixel and image window in two four-clip runs."""
import argparse
import json
from pathlib import Path

import numpy as np
import OpenEXR


def compare(baseline, candidate, measure=False):
    reports = [json.loads((p / "benchmark.json").read_text()) for p in (baseline, candidate)]
    assert reports[0]["sources"] == reports[1]["sources"]
    assert all(len(r["clips"]) == 4 for r in reports)
    count = 0
    samples = different = 0
    absolute_sum = maximum = 0.0
    for index in range(1, 5):
        paths = [sorted((p / str(index) / "shot").rglob("*.exr")) for p in (baseline, candidate)]
        assert [p.name for p in paths[0]] == [p.name for p in paths[1]]
        assert len(paths[0]) == reports[0]["clips"][index - 1]["result"]["total"]
        for left, right in zip(*paths):
            with OpenEXR.File(str(left), separate_channels=True) as a, OpenEXR.File(str(right), separate_channels=True) as b:
                for key in ("dataWindow", "displayWindow", "pixelAspectRatio"):
                    assert np.array_equal(a.header()[key], b.header()[key]), (left, key)
                assert a.channels().keys() == b.channels().keys()
                for name, channel in a.channels().items():
                    other = b.channels()[name].pixels
                    assert channel.pixels.dtype == other.dtype
                    equal = np.array_equal(channel.pixels, other, equal_nan=True)
                    if not measure:
                        assert equal, (left, name)
                    samples += other.size
                    if not equal:
                        delta = np.abs(channel.pixels.astype(np.float32) - other.astype(np.float32))
                        assert np.isfinite(delta).all(), (left, name, "nonfinite difference")
                        different += int(np.count_nonzero(delta))
                        absolute_sum += float(delta.sum(dtype=np.float64))
                        maximum = max(maximum, float(delta.max()))
            count += 1
        print(f"Clip {index}: {len(paths[0])} frames checked", flush=True)
    result = {"frames_checked": count, "pixel_identical": different == 0,
              "channel_samples": samples, "different_samples": different,
              "mean_absolute_difference": absolute_sum / samples, "maximum_absolute_difference": maximum,
              "baseline_seconds": reports[0]["seconds"], "candidate_seconds": reports[1]["seconds"],
              "reduction_percent": 100 * (1 - reports[1]["seconds"] / reports[0]["seconds"])}
    (candidate / "comparison.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--measure", action="store_true", help="Report measured differences instead of requiring exact equality.")
    args = parser.parse_args()
    compare(args.baseline, args.candidate, args.measure)
