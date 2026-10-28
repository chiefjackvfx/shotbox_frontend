"""Validate four farm clips and compare sample renders using the saved profiles."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_cache
from deadline_client import render_guard
import numpy as np
import OpenEXR


@app_cache.temporary_cache_task
def verify(report_path, executable):
    report = json.loads(report_path.read_text())
    assert report["clips"], "No clips in report"
    directory = Path(tempfile.mkdtemp(prefix="deadline-output-check-", dir=app_cache.cache_directory("temp")))
    cases = []
    total = 0
    for index, clip in enumerate(report["clips"], 1):
        request_path = Path(clip["request"])
        request = json.loads(request_path.read_text())
        media = json.loads((request_path.parent / "media.json").read_text())
        receipt = json.loads(Path(clip["result"]["receipt"]).read_text())
        first, last = receipt["first"], receipt["last"]
        frames = sorted(Path(request["output_pattern"]).parent.glob("*.exr"))
        assert len(frames) == last - first + 1, (index, len(frames))
        for frame in range(first, last + 1):
            path = Path(request["output_pattern"].replace("####", f"{frame:04d}"))
            assert path.is_file() and path.stat().st_size, path
            with OpenEXR.File(str(path), separate_channels=True) as image:
                header = image.header()
                low, high = header["dataWindow"]
                assert list(high - low + 1) == [media["width"], media["height"]]
                assert {c.name for c in header["channels"]} == {"R", "G", "B", "A"}
                assert header["compression"] == OpenEXR.ZIPS_COMPRESSION
                for channel in image.channels().values():
                    assert channel.pixels.dtype == np.float16
                    assert np.isfinite(channel.pixels).all()
            total += 1
        samples = {first, (first + last) // 2, last}
        chunk = receipt.get("chunk_size", request.get("deadline", {}).get("chunk_size", 10))
        for boundary in range(first + chunk, last + 1, chunk):
            samples.update((boundary - 1, boundary))
        samples = sorted(samples)
        case = {"guard": render_guard(receipt["nuke_version"], ["linux"]), "script": str(request_path.parent / "profiled.nk"), "frames": samples, "first": first, "last": last,
                "farm_pattern": request["output_pattern"], "local_pattern": str(directory / f"clip{index}_####.exr")}
        cases.append(case)
    fixture = directory / "cases.json"
    fixture.write_text(json.dumps(cases))
    script = directory / "render.py"
    script.write_text("import nuke, json\nfrom pathlib import Path\n"
                      + "cases=json.loads(Path(" + repr(str(fixture)) + ").read_text())\n"
                      + "for case in cases:\n"
                        " nuke.scriptClear(ignoreUnsavedChanges=True)\n"
                        " nuke.scriptOpen(case['script'])\n"
                        " write=nuke.toNode('Write1')\n"
                        " write['file'].setValue(case['local_pattern'])\n"
                        " write['beforeRender'].setValue(case['guard'])\n"
                        " nuke.execute(write,case['first'],case['last'],1)\n")
    with (directory / "nuke.log").open("w") as log:
        subprocess.run([executable, "--nukex", "-q", "-m", "16", "--topdown", "-t", str(script)], stdout=log,
                       stderr=subprocess.STDOUT, check=True, timeout=900)
    samples = different = 0
    absolute_sum = maximum = 0.0
    for case in cases:
        for frame in case["frames"]:
            paths = [case[key].replace("####", f"{frame:04d}") for key in ("farm_pattern", "local_pattern")]
            with OpenEXR.File(paths[0], separate_channels=True) as a, OpenEXR.File(paths[1], separate_channels=True) as b:
                for key in ("dataWindow", "displayWindow", "pixelAspectRatio", "compression"):
                    assert np.array_equal(a.header()[key], b.header()[key]), key
                assert a.channels().keys() == b.channels().keys()
                for name, channel in a.channels().items():
                    x, y = channel.pixels, b.channels()[name].pixels
                    assert x.dtype == y.dtype == np.float16
                    delta = np.abs(x.astype(np.float32) - y.astype(np.float32))
                    assert np.isfinite(delta).all()
                    samples += delta.size
                    different += int(np.count_nonzero(delta))
                    absolute_sum += float(delta.sum(dtype=np.float64))
                    maximum = max(maximum, float(delta.max()))
    result = {"all_frames_verified": total, "sample_frames_compared": sum(len(c["frames"]) for c in cases),
              "channel_samples": samples, "different_samples": different, "pixel_identical_samples": different == 0,
              "mean_absolute_difference": absolute_sum / samples, "maximum_absolute_difference": maximum}
    (directory / "verification.json").write_text(json.dumps(result, indent=2))
    print(directory, flush=True)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--nuke", required=True)
    args = parser.parse_args()
    verify(args.report, args.nuke)
