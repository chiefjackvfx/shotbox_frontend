"""Opt-in mixed MOV/EXR preview batch timing; writes only cache test outputs."""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime
import json
from itertools import zip_longest
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_cache
from nuke_headless_tasks import PreviewGenerator, parse_preview_progress


@app_cache.temporary_cache_task
def compare_previews(reference, candidate):
    import av
    import numpy as np

    reference, candidate = Path(reference), Path(candidate)
    before = json.loads((reference / "benchmark.json").read_text())
    after = json.loads((candidate / "benchmark.json").read_text())
    assert before["success"] and after["success"] and len(before["tasks"]) == len(after["tasks"])

    def metadata(container):
        stream = container.streams.video[0]
        codec = stream.codec_context
        return {"codec": codec.name, "profile": codec.profile, "format": codec.format.name,
                "width": codec.width, "height": codec.height, "fps": str(stream.average_rate),
                "time_base": str(stream.time_base), "start_time": stream.start_time,
                "duration": stream.duration, "audio_streams": len(container.streams.audio),
                **{key: str(getattr(codec, key, None)) for key in ("color_range", "colorspace", "color_primaries", "color_trc")}}

    comparisons = []
    for left, right in zip(before["tasks"], after["tasks"]):
        assert left["task"] == right["task"], "Preview requests differ"
        with av.open(left["output"]) as first, av.open(right["output"]) as second:
            info = metadata(first)
            assert info == metadata(second), (info, metadata(second))
            count, max_difference, squared_error, samples = 0, 0, 0.0, 0
            keyframes = []
            for a, b in zip_longest(first.decode(video=0), second.decode(video=0)):
                assert a is not None and b is not None, "Preview frame counts differ"
                assert a.pts * a.time_base == b.pts * b.time_base, "Preview frame timing differs"
                assert a.key_frame == b.key_frame, "Preview GOP layouts differ"
                if a.key_frame:
                    keyframes.append(count)
                x, y = a.to_ndarray(format="rgb24"), b.to_ndarray(format="rgb24")
                assert x.shape == y.shape
                if not np.array_equal(x, y):
                    difference = x.astype(np.float32) - y.astype(np.float32)
                    max_difference = max(max_difference, int(np.max(np.abs(difference))))
                    squared_error += float(np.sum(difference * difference, dtype=np.float64))
                samples += x.size
                count += 1
            mse = squared_error / max(samples, 1)
            comparisons.append({"file": Path(left["output"]).name, "metadata": info,
                                "frames": count, "keyframes": keyframes,
                                "identical_decoded_pixels": not max_difference, "max_difference": max_difference,
                                "psnr_db": round(10 * math.log10(255 ** 2 / mse), 3) if mse else None})
    report = {"reference": str(reference), "candidate": str(candidate), "previews": comparisons,
              "total_frames": sum(item["frames"] for item in comparisons)}
    (candidate / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


@app_cache.temporary_cache_task
def benchmark(executable, movie, sequence, *, batch=False):
    directory = Path(tempfile.mkdtemp(prefix="preview-benchmark-", dir=app_cache.cache_directory("temp")))
    print("PREVIEW_BENCHMARK " + str(directory), flush=True)
    generator = PreviewGenerator(executable)
    tasks = [
        {"input_path": str(movie), "colourspace": "ARRI LogC3 (EI800)", "fps": 25, "version": 1, "plate_name": "plate_01"},
        {"input_path": str(sequence), "colourspace": "ACEScg", "fps": 24, "version": 3, "plate_name": None},
        {"input_path": str(movie), "colourspace": "ARRI LogC3 (EI800)", "fps": 30, "version": 1, "plate_name": "plate_02"},
    ]
    reports = []
    with app_cache.temporary_cache_lease():
        previous = {name: os.environ.get(name) for name in ("NUKE_TEMP_DIR", "NUKE_DISK_CACHE", "TMPDIR", "XDG_CACHE_HOME")}
        for name in previous:
            target = directory / name.lower()
            target.mkdir()
            os.environ[name] = str(target)
        started = time.monotonic()
        try:
            with generator.preview_batch() if batch else nullcontext() as worker:
                for index, task in enumerate(tasks):
                    frames = []
                    task_started = time.monotonic()
                    with (directory / f"task-{index}.log").open("w", encoding="utf-8") as log:
                        def output(line):
                            log.write(line + "\n")
                            log.flush()
                            progress = parse_preview_progress(line)
                            if progress:
                                frames.append(progress)
                        result = generator.generate_preview(
                            **task, shot_dir=str(directory / "shot outputs"), shot_name="sho010",
                            project="Preview benchmark", artist="ShotBox", quality="medium", on_output=output,
                        )
                    report = {"task": task, "success": result.success, "error": result.error,
                              "output": str(result.output_path), "elapsed_seconds": round(time.monotonic() - task_started, 3),
                              "progress": frames[-1] if frames else None}
                    reports.append(report)
                    print(json.dumps(report), flush=True)
                    if not result.success:
                        break
            elapsed = time.monotonic() - started
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    report = {"measured_at": datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds"),
              "batch_worker": batch, "nuke_launches": worker.launches if batch else len(reports),
              "elapsed_seconds": round(elapsed, 3), "tasks": reports,
              "success": len(reports) == len(tasks) and all(task["success"] for task in reports)}
    (directory / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", required=True)
    parser.add_argument("--movie", type=Path, required=True)
    parser.add_argument("--sequence", type=Path, required=True)
    parser.add_argument("--batch", action="store_true")
    args = parser.parse_args()
    result = benchmark(args.nuke, args.movie, args.sequence, batch=args.batch)
    sys.exit(0 if result["success"] else 1)
