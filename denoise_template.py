"""Media-based denoise requests and template preparation (also imported by Nuke)."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
from fractions import Fraction

from nuke_headless_tasks import preview_input_exists, resolve_preview_sequence_input, resolve_preview_input_colourspace, map_input_colorspace

TEMPLATE = Path(__file__).with_name("template_denoise_v01.nk")


def collect_tasks(job, files_io):
    tasks, seen_shots, seen_outputs = [], set(), set()
    skipped = 0
    for timeline in job.get("timelines", []) or []:
        for shot in timeline.get("shots", []) or []:
            base = str(shot.get("base_path") or "").strip()
            key = ("id", shot["id"]) if shot.get("id") is not None else ("path", base)
            if key in seen_shots:
                continue
            seen_shots.add(key)
            sources = shot.get("original_clips") or [shot.get("original_clip")]
            if isinstance(sources, str):
                sources = [sources]
            sources = list(dict.fromkeys(str(p).strip() for p in sources if p and str(p).strip().lower() not in {"none", "offline", "—"}))
            if not base or not sources:
                skipped += 1
                continue
            try:
                local_base = str(Path(files_io.convert_path(base)).resolve())
            except (OSError, ValueError):
                skipped += len(sources)
                continue
            seen_sources = set()
            for index, source in enumerate(sources, 1):
                try:
                    local = str(Path(files_io.convert_path(source)).resolve())
                    canonical = os.path.normcase(resolve_preview_sequence_input(local)[0])
                    exists = preview_input_exists(local)
                except (OSError, ValueError):
                    skipped += 1
                    continue
                if canonical in seen_sources:
                    continue
                seen_sources.add(canonical)
                if not exists:
                    skipped += 1
                    continue
                plate = f"plate_{index:02d}" if len(sources) > 1 else None
                destination = (os.path.normcase(local_base), plate)
                if destination in seen_outputs:
                    continue
                seen_outputs.add(destination)
                media = {"source": local, "shot_dir": local_base, "shot_name": shot.get("title") or Path(local_base).name,
                         "plate": plate, "colourspace": shot.get("colourspace"), "fps": shot.get("fps") or 25,
                         "mode": "overwrite"}
                tasks.append({"shot": dict(shot), "media": media})
    return tasks, skipped


def output_directory(media):
    if media.get("mode", "overwrite") not in {"overwrite", "new"}:
        raise ValueError("Unknown denoise output mode.")
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(media["shot_name"])).strip(" .") or "shot"
    if media.get("plate"):
        if not re.fullmatch(r"plate_\d+", media["plate"]):
            raise ValueError("Invalid denoise plate identifier.")
        name += "_" + media["plate"]
    stem = name + "_DN"
    parent = Path(media["shot_dir"]).resolve() / "renders/precomp"
    parent.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(re.escape(stem) + r"_v(\d+)$")
    versions = {int(match[1]): p for p in parent.iterdir() if p.is_dir() and (match := pattern.fullmatch(p.name))}
    version = max(versions, default=0)
    if media.get("mode", "overwrite") == "overwrite" and version:
        return versions[version]
    version += 1
    while True:
        target = parent / f"{stem}_v{version:03d}"
        try:
            target.mkdir()
            return target
        except FileExistsError:
            version += 1


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remove_stale_frames(output_pattern, first, last):
    """After a successful render, remove only this version's out-of-range EXRs."""
    output = Path(output_pattern)
    pattern = re.compile(re.escape(output.name).replace("\\#\\#\\#\\#", r"(\d+)") + "$")
    for path in output.parent.iterdir():
        match = pattern.fullmatch(path.name)
        if match and path.is_file() and not first <= int(match[1]) <= last:
            path.unlink()


def create_template_request(directory, media, executable):
    source = str(Path(media["source"]).resolve())
    if not preview_input_exists(source):
        raise RuntimeError(f"Source media is missing: {source}")
    template = Path(media.get("template") or TEMPLATE).resolve()
    if not template.is_file():
        raise RuntimeError(f"Denoise template is missing: {template}")
    directory = Path(directory)
    working = directory / "template.nk"
    shutil.copy2(template, working)
    output = output_directory(media)
    request = {"workflow": "template", "script": str(working), "sha256": digest(working),
               "template": str(template), "source": source, "colourspace": media.get("colourspace"),
               "fps": media.get("fps") or 25,
               "output_pattern": (output / (output.name + "_####.exr")).as_posix(),
               # Windows manual profiling succeeds in the full NukeX GUI;
               # use its normal visible startup. Keep Linux's
               # qualified Qt-terminal workflow unchanged.
               "nuke": str(executable), "write_node": "Write1",
               "qt_terminal": sys.platform != "win32", "topdown": True,
               "profile_method": "keyboard" if sys.platform == "win32" else "visual"}
    path = directory / "request.json"
    if sys.platform == "win32":
        mode = os.environ.get("SHOTBOX_DENOISE_WINDOWS_DISPLAY", "hidden").strip().lower()
        if mode not in {"hidden", "visible"}:
            raise RuntimeError("SHOTBOX_DENOISE_WINDOWS_DISPLAY must be hidden or visible.")
        request["windows_display"] = mode
    path.write_text(json.dumps(request, indent=2), encoding="utf-8")
    return path


def resolve_source(source):
    source, first, last = resolve_preview_sequence_input(source)
    token = re.search(r"#+|%0?\d*d", Path(source).name)
    if token is None:
        return source, first, last
    name = Path(source).name
    width = len(token[0]) if token[0].startswith("#") else int(re.sub(r"\D", "", token[0]) or 0)
    digits = rf"(\d{{{width}}})" if width else r"(\d+)"
    matcher = re.compile(re.escape(name[:token.start()]) + digits + re.escape(name[token.end():]) + "$", re.IGNORECASE)
    frames = sorted(int(m[1]) for p in Path(source).parent.iterdir() if p.is_file() and (m := matcher.fullmatch(p.name)))
    if not frames:
        raise RuntimeError(f"No frames found for {source}")
    if len(set(frames)) != frames[-1] - frames[0] + 1:
        raise RuntimeError(f"Source sequence contains missing frames: {source}")
    return source, frames[0], frames[-1]


def output_size(width, height):
    if width <= 0 or height <= 0:
        raise RuntimeError("Source media has invalid dimensions.")
    return (4096, max(1, int(height * 4096 / width + .5))) if width > 4096 else (width, height)


def native_sequence(nuke, source):
    """Let Nuke group numbered images, without guessing a codec whitelist."""
    path = Path(source)
    if not path.is_file() or not re.search(r"\d+", path.stem):
        return source
    for entry in nuke.getFileNameList(str(path.parent)):
        match = re.fullmatch(r"(.+(?:#+|%0?\d*d).+) (-?\d+)-(-?\d+)", entry)
        if not match:
            continue
        pattern = match[1]
        token = re.search(r"#+|%0?\d*d", pattern)
        expression = re.escape(pattern[:token.start()]) + r"\d+" + re.escape(pattern[token.end():])
        if re.fullmatch(expression, path.name):
            # Nuke lists unpadded sequences using a single '#'.
            if token[0] == "#":
                pattern = pattern[:token.start()] + "%d" + pattern[token.end():]
            return str(path.parent / pattern)
    return source


def prepare(nuke, request):
    """Configure a loaded template; never open or modify an artist's comp."""
    nodes = {}
    for name, cls in (("Read1", "Read"), ("Reformat1", "Reformat"), ("Write1", "Write")):
        node = nuke.toNode(name)
        if node is None or node.Class() != cls:
            raise RuntimeError(f"Denoise template requires {cls} named {name}.")
        nodes[name] = node
    noises = [n for n in nuke.allNodes() if n.Class() == "OFXcom.absoft.NeatVideo6_v6"]
    if len(noises) != 1:
        raise RuntimeError("Denoise template requires exactly one Neat Video v6 node.")
    read, reformat, write = (nodes[name] for name in ("Read1", "Reformat1", "Write1"))
    noise = noises[0]
    if reformat.input(0) is not read or noise.input(0) is not reformat or write.input(0) is not noise:
        raise RuntimeError("Denoise template must connect Read1 → Reformat1 → Neat Video → Write1.")
    source, first, last = resolve_source(native_sequence(nuke, request["source"]))
    file_text = source.replace("\\", "/")
    if first is not None:
        file_text += f" {first}-{last}"
    colourspace = resolve_preview_input_colourspace(input_path=source, requested_colourspace=request.get("colourspace"))
    input_colourspace = map_input_colorspace(colourspace)
    # A fresh Read prevents the template's 1–100 placeholder from surviving
    # when a still or movie does not explicitly provide a sequence range.
    probe = nuke.nodes.Read()
    try:
        probe["file"].fromUserText(file_text)
        # Movie metadata may select a legacy LUT such as 'rec709' that is
        # invalid in the template's OCIO config. Apply the shot IDT before
        # validation, dimensions or metadata evaluate this temporary reader.
        probe["colorspace"].setValue(input_colourspace)
        if first is None:
            first, last = int(probe["first"].value()), int(probe["last"].value())
        else:
            probe["first"].setValue(first)
            probe["last"].setValue(last)
        if last < first:
            raise RuntimeError("Source media has an invalid frame range.")
        nuke.root().setFrame(first)
        if probe.error():
            raise RuntimeError(f"Nuke cannot read source media: {source}. Check reader/codec availability.")
        width, height, aspect = probe.width(), probe.height(), probe.pixelAspect()
        metadata = probe.metadata()
        read["file"].fromUserText(file_text)
        read["colorspace"].setValue(input_colourspace)
        for knob, value in (("first", first), ("last", last), ("origfirst", first), ("origlast", last), ("origset", True)):
            read[knob].setValue(value)
    finally:
        nuke.delete(probe)
    offset = nuke.nodes.TimeOffset(name="ShotBoxFrameOffset", inputs=[read], time_offset=1001-first)
    reformat.setInput(0, offset)
    out_width, out_height = output_size(width, height)
    reformat["disable"].setValue(width <= 4096)
    if width > 4096:
        reformat["type"].setValue("to box")
        reformat["box_width"].setValue(out_width)
        reformat["box_height"].setValue(out_height)
        reformat["box_fixed"].setValue(True)
        reformat["resize"].setValue("width")
        if reformat.knob("box_pixel_aspect"):
            reformat["box_pixel_aspect"].setValue(aspect)
    root = nuke.root()
    root["format"].setValue(nuke.addFormat(f"{out_width} {out_height} {aspect} ShotBoxDenoise_{out_width}x{out_height}_{aspect}").name())
    root["first_frame"].setValue(1001)
    root["last_frame"].setValue(1001 + last - first)
    root["frame"].setValue(1001)
    root["proxy"].setValue(False)
    fps = request.get("fps") or 25
    for key in ("input/frame_rate", "input/frameRate", "input/fps", "frame_rate", "fps"):
        try:
            value = float(Fraction(str(metadata.get(key, 0))))
            if math.isfinite(value) and value > 0:
                fps = value
                break
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    root["fps"].setValue(float(fps))
    write["file"].setValue(request["output_pattern"])
    write["file_type"].setValue("exr")
    write["datatype"].setValue("16 bit half")
    write["compression"].setValue("Zip (1 scanline)")
    write["channels"].setValue("rgba")
    write["create_directories"].setValue(True)
    write["colorspace"].setValue("scene_linear")
    write["use_limit"].setValue(False)
    return {"source": source, "source_first": first, "source_last": last, "source_width": width,
            "source_height": height, "width": out_width, "height": out_height, "pixel_aspect": aspect,
            "resized": width > 4096, "fps": float(fps), "read_colorspace": read["colorspace"].value()}
