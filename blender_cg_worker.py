"""Prepare 3DE exports and save CG scenes inside background Blender."""
import json
import math
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

import bpy


START_FRAME = 1001


def empty_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.use_relative_paths = False
    bpy.context.preferences.filepaths.save_version = 0


def set_fps(scene, fps):
    scene.render.fps = round(fps)
    scene.render.fps_base = scene.render.fps / fps


def action_curves(action):
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    yield from bag.fcurves
    else:
        yield from action.fcurves


def align_animation(cameras, scene):
    owners = set()
    for camera in cameras:
        owners.add(camera.data)
        owner = camera
        while owner and owner not in owners:
            owners.add(owner)
            owner = owner.parent
    actions = {owner.animation_data.action for owner in owners
               if owner.animation_data and owner.animation_data.action}
    bounds = [action.frame_range[:] for action in actions]
    first = min((start for start, end in bounds), default=scene.frame_start)
    last = max((end for start, end in bounds), default=first)
    offset = START_FRAME - first
    if offset:
        for action in bpy.data.actions:
            for curve in action_curves(action):
                for key in curve.keyframe_points:
                    key.handle_left.x += offset
                    key.handle_right.x += offset
                    key.co.x += offset
                curve.update()
        for data in {camera.data for camera in cameras}:
            for background in data.background_images:
                background.image_user.frame_start += round(offset)
    return math.ceil(last - first) + 1 if last > first else None


def import_source(source, work_dir, fps):
    empty_scene()
    set_fps(bpy.context.scene, fps)
    # 3DE can request temporary OBJ files beside the original export.
    # Keep its scratch geometry with this worker, including after cancellation.
    named_temp = tempfile.NamedTemporaryFile

    def export_temp(*args, **kwargs):
        kwargs["dir"] = work_dir
        return named_temp(*args, **kwargs)

    with patch.object(tempfile, "NamedTemporaryFile", export_temp):
        runpy.run_path(str(source), run_name="__main__")
    scene = bpy.context.scene
    cameras = [obj for obj in scene.objects if obj.type == "CAMERA"]
    if not cameras:
        scene = next((item for item in bpy.data.scenes
                      if any(obj.type == "CAMERA" for obj in item.objects)), scene)
        cameras = [obj for obj in scene.objects if obj.type == "CAMERA"]
    if not cameras:
        raise ValueError("Export created no camera.")
    bpy.context.window.scene = scene
    scene.camera = scene.camera or cameras[0]
    duration = align_animation(cameras, scene)
    # Relative footage paths in standalone exports refer to the export folder.
    for image in bpy.data.images:
        if image.filepath.startswith("//"):
            image.filepath = bpy.path.abspath(image.filepath, start=str(source.parent))
    group = bpy.data.collections.new(source.stem)
    for obj in list(scene.collection.objects):
        group.objects.link(obj)
        scene.collection.objects.unlink(obj)
    for child in list(scene.collection.children):
        group.children.link(child)
        scene.collection.children.unlink(child)
    scene.collection.children.link(group)
    return group, duration


def save(path):
    bpy.ops.wm.save_as_mainfile(filepath=str(path), relative_remap=False, check_existing=False)


def prepare(request, output):
    summaries = []
    libraries = []
    first_scene = output.with_name(output.stem + "-first.blend")
    for index, value in enumerate(request["sources"]):
        source = Path(value)
        print(f"SHOTBOX_PROGRESS Importing {source.name}", flush=True)
        try:
            group, duration = import_source(source, output.parent, request["fps"])
            summaries.append(dict(source=value, duration=duration))
            if index == 0:
                save(first_scene)
            else:
                library = output.with_name(f"{output.stem}-{index}.blend")
                bpy.data.libraries.write(str(library), {group}, path_remap="ABSOLUTE")
                libraries.append((library, group.name))
        except Exception as error:
            raise RuntimeError(f"Could not import {source}: {error}") from error
    bpy.ops.wm.open_mainfile(filepath=str(first_scene))
    for library, collection_name in libraries:
        with bpy.data.libraries.load(str(library), link=False, relative=False) as (_, data):
            data.collections = [collection_name]
        bpy.context.scene.collection.children.link(data.collections[0])
    return summaries


def main():
    request_path, result_path = map(Path, sys.argv[sys.argv.index("--") + 1:])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    output = Path(request["output"])
    if output.suffix != ".blend" or output.exists():
        raise ValueError("Expected a new .blend output path.")
    cameras = []
    if request["mode"] == "prepare":
        cameras = prepare(request, output)
    elif request.get("prepared"):
        bpy.ops.wm.open_mainfile(filepath=request["prepared"])
    else:
        empty_scene()
    scene = bpy.context.scene
    set_fps(scene, request["fps"])
    scene.frame_start = START_FRAME
    if request["mode"] == "save":
        end = START_FRAME + request["duration"] - 1
        scene.frame_end = end
        if scene.frame_end != end:
            raise ValueError(f"Frame {end} exceeds Blender's timeline limit.")
    scene.frame_set(START_FRAME)
    if bpy.data.filepath:
        bpy.ops.file.make_paths_absolute()
    save(output)
    result_path.write_text(json.dumps(dict(output=str(output), cameras=cameras)), encoding="utf-8")


if __name__ == "__main__":
    main()
