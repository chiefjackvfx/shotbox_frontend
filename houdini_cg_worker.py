"""UI-independent Houdini worker. Run with hython, never ShotBox's interpreter.

Camera organization and animation inspection adapted from the user's 123.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys
import tokenize
import traceback


START_FRAME = 1001


def progress(message):
    print("SHOTBOX_PROGRESS " + message, flush=True)


def collect_nodes(roots):
    nodes = {}
    for root in roots:
        nodes[root.path()] = root
        for node in root.allSubChildren():
            nodes[node.path()] = node
    return list(nodes.values())


def snapshot_keys(roots):
    """Capture explicit frame numbers before any FPS change."""
    channels = []
    for node in collect_nodes(roots):
        for parm in node.parms():
            keys = [(key, float(key.frame())) for key in parm.keyframes()]
            if keys:
                if not all(math.isfinite(frame) for _, frame in keys):
                    raise ValueError(f"Invalid keyframe on {parm.path()}")
                channels.append((parm, keys))
    return channels


def restore_keys(channels, offset=0):
    for parm, keys in channels:
        shifted = []
        for key, frame in keys:
            key.setFrame(frame + offset)
            shifted.append(key)
        parm.deleteAllKeyframes()
        parm.setKeyframes(shifted)


def animation_summary(channels):
    # Default camera expressions are often stored as a single key at frame 1.
    # They do not define a finite animation range and must not extend a baked
    # camera's bounds. A lone keyed pose needs an explicit duration as well.
    frames = [frame for _, keys in channels if len(keys) > 1 for _, frame in keys]
    if not frames:
        return {"first_frame": None, "last_frame": None, "duration": None}
    # 3DE exports time values as decimals; remove floating-point frame noise.
    first, last = round(min(frames), 6), round(max(frames), 6)
    return {"first_frame": first, "last_frame": last,
            "duration": int(math.ceil(last - first)) + 1}


def organize_import(hou, source, roots):
    obj = hou.node("/obj")
    cameras = [node for node in collect_nodes(roots) if node.type().name() == "cam"]
    if not roots or not cameras:
        raise ValueError(f"The export did not create a camera under /obj:\n{source}")
    base = re.sub(r"[^A-Za-z0-9_]", "_", source.stem).strip("_") or "Camera"
    if base[0].isdigit():
        base = "cam_" + base
    if not base.lower().endswith("_camera"):
        base += "_Camera"
    if len(roots) == 1:
        container = roots[0]
        container.setName(base, unique_name=True)
    else:
        # Let Houdini preserve hierarchy references as objects are moved.
        name, index = base, 2
        while obj.node(name) is not None:
            name, index = f"{base}_{index}", index + 1
        container = obj.collapseIntoSubnet(tuple(roots), subnet_name=name)
        container.layoutChildren()
    return container, [node for node in collect_nodes([container]) if node.type().name() == "cam"]


def import_camera(hou, source, fps):
    source = Path(source).resolve()
    if source.suffix.lower() != ".py":
        raise ValueError("Choose a 3DE Houdini Python export (.py).")
    # Compile before changing the scene and honor Python source encodings.
    with tokenize.open(str(source)) as stream:
        code = compile(stream.read(), str(source), "exec")
    obj = hou.node("/obj")
    before = set(obj.children())
    previous = snapshot_keys(list(before))
    namespace = {"hou": hou, "__name__": "__main__", "__file__": str(source)}
    # Only explicitly selected exports are run, once per preparation.
    exec(code, namespace, namespace)
    roots = [node for node in obj.children() if node not in before]
    imported = snapshot_keys(roots)
    # An export can change FPS. Restore both old and new frame labels explicitly.
    hou.setFps(fps)
    restore_keys(previous)
    restore_keys(imported)
    container, cameras = organize_import(hou, source, roots)
    channels = snapshot_keys([container])
    summary = animation_summary(channels)
    if summary["first_frame"] is not None:
        restore_keys(channels, START_FRAME - summary["first_frame"])
    metadata = dict(summary, source=str(source), container=container.path(),
                    cameras=[camera.path() for camera in cameras])
    for node in [container, *cameras]:
        node.setUserData("3de_camera_source", str(source))
        node.setUserData("shotbox_camera_animation", json.dumps(metadata))
        node.setComment(f"3DEqualizer Camera Import\nSource file: {source}\n"
                        f"Source frames: {summary['first_frame']} - {summary['last_frame']}\n"
                        f"Duration: {summary['duration']} frames; scene start: {START_FRAME}")
    return metadata


def require_commercial(hou):
    if hou.licenseCategory() != hou.licenseCategoryType.Commercial:
        raise RuntimeError("Creating .hip assets requires a commercial Houdini licence.")


def run_request(hou, request):
    require_commercial(hou)
    fps = float(request["fps"])
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("FPS must be positive.")
    if request["mode"] == "prepare":
        hou.setFps(fps)
        summaries = []
        for index, source in enumerate(request["sources"]):
            progress(f"Importing camera {index + 1}/{len(request['sources'])}: {Path(source).name}")
            summaries.append(import_camera(hou, source, fps))
        hou.node("/obj").layoutChildren()
    elif request["mode"] == "save":
        summaries = []
        if request.get("prepared"):
            progress("Loading prepared cameras")
            hou.hipFile.load(request["prepared"], suppress_save_prompt=True, ignore_load_warnings=False)
        channels = snapshot_keys(hou.node("/obj").children())
        hou.setFps(fps)
        restore_keys(channels)
        duration = request["duration"]
        if not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0:
            raise ValueError("Duration must be a positive frame count.")
        end = START_FRAME + duration - 1
        hou.playbar.setFrameRange(START_FRAME, end)
        hou.playbar.setPlaybackRange(START_FRAME, end)
        hou.setFrame(START_FRAME)
        hou.putenv("JOB", request["destination"])
    else:
        raise ValueError("Unknown Houdini worker operation.")
    require_commercial(hou)
    output = Path(request["output"])
    if output.suffix != ".hip" or output.exists():
        raise ValueError("Expected a new .hip output path.")
    progress("Saving Houdini scene")
    hou.hipFile.save(str(output), save_to_recent_files=False)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Houdini did not create the expected .hip file.")
    return {"output": str(output), "cameras": summaries}


def main():
    request_path, result_path = map(Path, sys.argv[1:3])
    try:
        import hou
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result = run_request(hou, request)
        result_path.write_text(json.dumps(result), encoding="utf-8")
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
