"""Time exactly four real source clips; keep all outputs in the app cache."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_cache
from nuke_denoise import create_request, run
import nuke_denoise


def run_clip(index, source, directory, executable, threads):
    job = directory / str(index)
    job.mkdir()
    metadata = json.loads((source.parent.parent / "shot_assets/timeline_import.json").read_text())
    plate = next(p for p in metadata["plates"] if Path(p.get("copied_path", "")).name == source.name)
    request = create_request(job, {"source": str(source), "shot_dir": str(job / "shot"),
                                  "shot_name": source.parent.parent.name,
                                  "colourspace": plate["input_colourspace"], "fps": metadata["fps"]}, executable)
    clip_started = time.monotonic()
    events = []

    def notify(event):
        entry = {"elapsed": time.monotonic() - clip_started, **event}
        events.append(entry)
        if event["stage"] != "debug" and (event["stage"] != "rendering" or event.get("completed", 0) % 25 == 0):
            print(index, json.dumps(entry), flush=True)

    command = nuke_denoise.build_nuke_command(executable)
    if threads is not None:
        command[-1] = str(threads)
    with mock.patch.object(nuke_denoise, "build_nuke_command", return_value=command):
        result = run(request, notify=notify)

    return {"seconds": time.monotonic() - clip_started, "result": result, "events": events}


@app_cache.temporary_cache_task
def benchmark(source_root, executable, threads=None, workers=1):
    sources = sorted(Path(source_root).glob("VFX/*/*/plates/*.mxf"))[:4]
    if len(sources) != 4:
        raise RuntimeError("Benchmark requires four source clips.")
    directory = Path(tempfile.mkdtemp(prefix="denoise-four-", dir=app_cache.cache_directory("temp")))
    print(directory, flush=True)
    report = {"sources": [str(p) for p in sources], "threads": threads, "workers": workers, "clips": []}
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        futures = [pool.submit(run_clip, i, source, directory, executable, threads)
                   for i, source in enumerate(sources, 1)]
        for future in futures:
            clip = future.result()
            report["clips"].append(clip)
            report["seconds"] = time.monotonic() - started
            (directory / "benchmark.json").write_text(json.dumps(report, indent=2))
            if clip["result"]["stage"] != "complete":
                raise RuntimeError(clip["result"])
    print(json.dumps({"seconds": report["seconds"], "report": str(directory / "benchmark.json")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--nuke", required=True)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    benchmark(args.source_root, args.nuke, args.threads, args.workers)
