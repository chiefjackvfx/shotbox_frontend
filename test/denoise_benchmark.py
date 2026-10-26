"""Time the denoise workflow using an isolated copy of a selected script.

The copy reads the original source footage. Auto Profile, script saves, and
WriteDN1 frames are confined to .cache/temp alongside the timing report.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_cache
import nuke_denoise


def isolated_script(source, directory):
    source = Path(source).resolve()
    text = source.read_text(encoding="utf-8")

    def literal(path):
        value = str(path).replace("\\", "/")
        for character in ('[', ']', '$', '"'):
            value = value.replace(character, "\\" + character)
        return '"' + value + '"'

    writes = list(re.finditer(r"(?ms)^Write \{\n.*?^\}", text))
    targets = [match for match in writes if re.search(r"(?m)^ name WriteDN1$", match[0])]
    if len(targets) != 1:
        raise ValueError("Expected exactly one top-level WriteDN1 node")
    target = targets[0]
    output = directory / "renders" / "denoise.####.exr"
    block, count = re.subn(r"(?m)^ file .*?$", lambda _: " file " + literal(output), target[0])
    if count != 1:
        raise ValueError("WriteDN1 must have exactly one file knob")
    text = text[:target.start()] + block + text[target.end():]
    text, count = re.subn(r"(?m)^ project_directory .*?$",
                          lambda _: " project_directory " + literal(source.parent), text, count=1)
    if count != 1:
        raise ValueError("Expected an existing root project_directory")
    script = directory / source.name
    script.write_text(text, encoding="utf-8")
    return script


def stage_timings(events, elapsed):
    transitions = []
    for event in events:
        if event["stage"] != "debug" and (not transitions or event["stage"] != transitions[-1]["stage"]):
            transitions.append(event)
    stages = [{"stage": "display_setup", "seconds": transitions[0]["elapsed_seconds"]}] if transitions else []
    frames = [event for event in events if event["stage"] == "rendering" and event.get("completed", 0)]
    for index, event in enumerate(transitions):
        end = transitions[index + 1]["elapsed_seconds"] if index + 1 < len(transitions) else elapsed
        if event["stage"] == "rendering" and frames:
            rendered = frames[-1]["elapsed_seconds"]
            stages.append({"stage": "rendering", "seconds": round(rendered - event["elapsed_seconds"], 3)})
            stages.append({"stage": "shutdown", "seconds": round(end - rendered, 3)})
        else:
            stages.append({"stage": event["stage"], "seconds": round(max(0, end - event["elapsed_seconds"]), 3)})
    return stages


@app_cache.temporary_cache_task
def compare_renders(reference, candidate):
    """Compare every pixel and channel after timing, outside measured work."""
    import numpy as np
    import OpenEXR

    reference, candidate = Path(reference), Path(candidate)
    expected = tuple(path.name for path in sorted((reference / "renders").glob("*.exr")))
    actual = tuple(path.name for path in sorted((candidate / "renders").glob("*.exr")))
    if not expected or actual != expected:
        raise ValueError("Benchmark output frame sets differ or are empty")
    mismatched, max_difference, dimensions = [], 0.0, set()
    for name in expected:
        with OpenEXR.File(str(reference / "renders" / name)) as before, \
                OpenEXR.File(str(candidate / "renders" / name)) as after:
            left, right = before.channels(), after.channels()
            if left.keys() != right.keys():
                raise ValueError(f"Output channels differ: {name}")
            for channel in left:
                a, b = left[channel].pixels, right[channel].pixels
                if a.shape != b.shape or not np.isfinite(a).all() or not np.isfinite(b).all():
                    raise ValueError(f"Output dimensions differ or pixels are nonfinite: {name}")
                dimensions.add(tuple(a.shape[:2][::-1]))
                if not np.array_equal(a, b):
                    mismatched.append({"frame": name, "channel": channel})
                    difference = float(np.max(np.abs(a.astype(np.float32) - b.astype(np.float32))))
                    max_difference = max(max_difference, difference)
    report = {"reference": str(reference), "candidate": str(candidate), "frames_compared": len(expected),
              "dimensions": sorted(dimensions), "finite": True, "identical_pixels": not mismatched,
              "max_absolute_difference": max_difference, "mismatched_channels": mismatched}
    (candidate / "pixel-comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


@app_cache.temporary_cache_task
def benchmark(script, executable, threads=None, qt_terminal=None, topdown=None):
    directory = Path(tempfile.mkdtemp(prefix="denoise-benchmark-", dir=app_cache.cache_directory("temp")))
    source_digest = nuke_denoise.script_digest(script)
    copy = isolated_script(script, directory)
    request = nuke_denoise.create_request(directory, copy, executable)
    data = json.loads(request.read_text())
    if qt_terminal is not None:
        data["qt_terminal"] = qt_terminal
    if topdown is not None:
        data["topdown"] = topdown
    request.write_text(json.dumps(data), encoding="utf-8")
    original_command = nuke_denoise.build_nuke_command
    if threads is not None:
        def command(path):
            arguments = original_command(path)
            arguments[arguments.index("-m") + 1] = str(threads)
            return arguments
        nuke_denoise.build_nuke_command = command
    configured_command = nuke_denoise.build_launch_command(
        executable, qt_bootstrap=directory / "qt-bootstrap.py" if data.get("qt_terminal") else None,
        topdown=data.get("topdown", False),
    )
    started = time.monotonic()
    events = []
    print("DENOISE_BENCHMARK " + str(directory), flush=True)

    def notify(event):
        record = {**event, "elapsed_seconds": round(time.monotonic() - started, 3)}
        events.append(record)
        with (directory / "timed-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        if event["stage"] != "debug":
            print(json.dumps(record), flush=True)

    try:
        result = nuke_denoise.run(request, notify)
    finally:
        nuke_denoise.build_nuke_command = original_command
    elapsed = time.monotonic() - started
    stages = stage_timings(events, elapsed)
    frames = [event for event in events if event["stage"] == "rendering" and event.get("completed", 0)]
    rendering = next((stage["seconds"] for stage in stages if stage["stage"] == "rendering"), None)
    report = {
        "measured_at": datetime.now(ZoneInfo("Europe/London")).isoformat(timespec="seconds"),
        "script": str(Path(script).resolve()), "threads": int(configured_command[configured_command.index("-m") + 1]),
        "isolated_script": str(copy), "source_script_unchanged": nuke_denoise.script_digest(script) == source_digest,
        "command": configured_command, "elapsed_seconds": round(elapsed, 3),
        "qt_terminal": data.get("qt_terminal", False),
        "topdown": data.get("topdown", False),
        "stage_timings": stages, "rendered_frames": len(frames),
        "render_fps": round(len(frames) / rendering, 3) if rendering and frames else None,
        "result": result,
    }
    (directory / "benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--nuke", required=True)
    parser.add_argument("--threads", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--qt-terminal", action="store_true", default=None)
    mode.add_argument("--desktop", action="store_false", dest="qt_terminal")
    render = parser.add_mutually_exclusive_group()
    render.add_argument("--topdown", action="store_true", default=None)
    render.add_argument("--classic", action="store_false", dest="topdown")
    args = parser.parse_args()
    if args.threads is not None and args.threads < 1:
        parser.error("--threads must be positive")
    report = benchmark(args.script, args.nuke, args.threads, args.qt_terminal, args.topdown)
    sys.exit(0 if report["result"]["stage"] == "complete" else 1)
