#!/usr/bin/env python
"""
Small template-based Nuke script editing utilities.

Flow:
1. The frontend builds a resolved request payload.
2. ``generate_from_template(...)`` launches ``Nuke -t create_nk.py make_nk``.
3. Inside Nuke, ``template_current.nk`` is opened, patched, and saved.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional
from html import escape

from nuke_headless_tasks import find_nuke_executable, map_input_colorspace
from app_cache import cache_directory, temporary_cache_task


READ_NODE_LABEL = "Fr. range: [value first] - [value last]\nRes: [value width] * [value height]"
RUNTIME_TEMPLATE_NAME = "template_current.nk"
EXTRA_PLATE_SPACING = 150
TARGET_ROOT_FORMAT_WIDTH = 4096


@dataclass
class GenerateNkRequest:
    template_path: str = ""
    script_path: str = ""
    shot_dir: str = ""
    shot_name: str = ""
    project_name: str = ""
    artist_name: str = ""
    fps: float = 25.0
    colourspace: str = ""
    duration: int = 1
    edit_inpoint: int = 0
    edit_outpoint: int = 0
    frame_first: int = 1001
    frame_last: int = 1001
    format_width: int = 1920
    format_height: int = 1080
    primary_plate_path: str = ""
    extra_plate_paths: list[str] = field(default_factory=list)
    # Optional timeline-only mapping. Legacy requests leave this empty.
    plate_timings: list[dict] = field(default_factory=list)
    cut_duration: Optional[int] = None
    head_handles: Optional[int] = None
    tail_handles: Optional[int] = None
    # Primary-first unique sources; read_index maps back to plate_timings/ReadN.
    # Source ranges may be omitted and probed inside Nuke before saving.
    denoise_plates: list[dict] = field(default_factory=list)
    dn_exr_file: str = ""
    comp_mov_file: str = ""
    comp_exr_file: str = ""
    preview_mp4_file: str = ""
    shot_metadata_lines: list[str] = field(default_factory=list)

    @property
    def cut_first(self):
        return self.frame_first + (self.head_handles or 0)

    @property
    def cut_last(self):
        return self.cut_first + max(self.cut_duration or self.duration, 1) - 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GenerateNkRequest":
        return cls(
            template_path=str(data.get("template_path", "")),
            script_path=str(data.get("script_path", "")),
            shot_dir=str(data.get("shot_dir", "")),
            shot_name=str(data.get("shot_name", "")),
            project_name=str(data.get("project_name", "")),
            artist_name=str(data.get("artist_name", "")),
            fps=float(data.get("fps", 25.0)),
            colourspace=str(data.get("colourspace", "")),
            duration=int(data.get("duration", 1)),
            edit_inpoint=int(data.get("edit_inpoint", 0)),
            edit_outpoint=int(data.get("edit_outpoint", 0)),
            frame_first=int(data.get("frame_first", 1001)),
            frame_last=int(data.get("frame_last", 1001)),
            format_width=int(data.get("format_width", 1920)),
            format_height=int(data.get("format_height", 1080)),
            primary_plate_path=str(data.get("primary_plate_path", "")),
            extra_plate_paths=[str(path) for path in data.get("extra_plate_paths", [])],
            plate_timings=list(data.get("plate_timings", [])),
            cut_duration=data.get("cut_duration"),
            head_handles=data.get("head_handles"),
            tail_handles=data.get("tail_handles"),
            denoise_plates=list(data.get("denoise_plates", [])),
            dn_exr_file=str(data.get("dn_exr_file", "")),
            comp_mov_file=str(data.get("comp_mov_file", "")),
            comp_exr_file=str(data.get("comp_exr_file", "")),
            preview_mp4_file=str(data.get("preview_mp4_file", "")),
            shot_metadata_lines=[str(line) for line in data.get("shot_metadata_lines", [])],
        )


@dataclass
class GenerateNkResult:
    success: bool
    script_path: str
    error: str = ""
    stdout_lines: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.success


def get_create_nk_script_path() -> Path:
    return Path(__file__).resolve()


def get_runtime_template_path() -> Path:
    return Path(__file__).resolve().parent / RUNTIME_TEMPLATE_NAME


def build_generate_from_template_command(
    nuke_exe: str,
    request_path: str,
    script_path: Optional[str] = None,
) -> list[str]:
    create_script = script_path or str(get_create_nk_script_path())
    return [
        nuke_exe,
        "-t",
        create_script,
        "make_nk",
        "--request",
        str(request_path),
    ]


def _normalize_path(path: str | Path) -> str:
    return str(path).replace("\\", "/")


def _coerce_request(request: GenerateNkRequest | dict) -> GenerateNkRequest:
    if isinstance(request, GenerateNkRequest):
        return request
    return GenerateNkRequest.from_dict(request)


def compute_target_root_format(source_width: int, source_height: int) -> tuple[int, int]:
    """Match denoise sizing: preserve native dimensions up to 4096 pixels wide."""
    from denoise_template import output_size
    source_width, source_height = int(source_width or 0), int(source_height or 0)
    if source_width <= 0 or source_height <= 0:
        return 1920, 1080
    return output_size(source_width, source_height)


def _resolve_template_path(request: GenerateNkRequest) -> Path:
    if request.template_path:
        return Path(request.template_path)
    return get_runtime_template_path()


def _build_process_error(returncode: int, output_lines: list[str]) -> str:
    for line in reversed(output_lines):
        if line.startswith("ERROR:"):
            detail = line.split("ERROR:", 1)[1].strip()
            if detail:
                return detail

    ignored_prefixes = {"NUKE_EXE:", "CREATE_NK_SCRIPT:", "CMD:", "REQUEST_JSON:"}
    for line in reversed(output_lines):
        if not line:
            continue
        if any(line.startswith(prefix) for prefix in ignored_prefixes):
            continue
        return f"Nuke exited with code {returncode}: {line}"

    return f"Nuke exited with code {returncode}"


@temporary_cache_task
def generate_from_template(
    request: GenerateNkRequest | dict,
    nuke_path: Optional[str] = None,
    on_output: Optional[Callable[[str], None]] = None,
) -> GenerateNkResult:
    request = _coerce_request(request)

    nuke_exe = find_nuke_executable([nuke_path]) if nuke_path else find_nuke_executable()
    if not nuke_exe:
        return GenerateNkResult(
            success=False,
            script_path=request.script_path,
            error="Nuke executable not found. Please set a valid Nuke path in Settings.",
        )

    headless_script = get_create_nk_script_path()
    if not headless_script.exists():
        return GenerateNkResult(
            success=False,
            script_path=request.script_path,
            error=f"Create Nuke script helper not found: {headless_script}",
        )

    template_path = _resolve_template_path(request)
    if not template_path.exists():
        return GenerateNkResult(
            success=False,
            script_path=request.script_path,
            error=f"Nuke template not found: {template_path}",
        )

    required_files = [request.primary_plate_path, *request.extra_plate_paths]
    missing_file = next((path for path in required_files if not path or not os.path.exists(path)), None)
    if missing_file:
        return GenerateNkResult(
            success=False,
            script_path=request.script_path,
            error=f"Plate file not found: {missing_file}",
        )

    for field_name in ("dn_exr_file", "comp_mov_file", "comp_exr_file", "preview_mp4_file"):
        if not getattr(request, field_name):
            return GenerateNkResult(
                success=False,
                script_path=request.script_path,
                error=f"Missing required output path: {field_name}",
            )

    Path(request.script_path).parent.mkdir(parents=True, exist_ok=True)

    temp_request_path = None
    output_lines: list[str] = []
    try:
        payload = request.to_dict()
        payload["template_path"] = str(template_path)

        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=cache_directory("temp"),
            suffix=".json",
            prefix="shotbox_create_nk_",
            delete=False,
            encoding="utf-8",
        ) as handle:
            json.dump(payload, handle, indent=2)
            temp_request_path = handle.name

        cmd = build_generate_from_template_command(
            nuke_exe,
            temp_request_path,
            str(headless_script),
        )
        cmd_display = " ".join(f'"{part}"' if " " in part else part for part in cmd)
        output_lines = [
            f"NUKE_EXE: {nuke_exe}",
            f"CREATE_NK_SCRIPT: {headless_script}",
            f"CMD: {cmd_display}",
        ]
        if on_output:
            for line in output_lines:
                on_output(line)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        while True:
            line = process.stdout.readline()
            if not line and process.poll() is not None:
                break
            if not line:
                continue
            cleaned = line.rstrip("\n")
            output_lines.append(cleaned)
            if on_output:
                on_output(cleaned)

        returncode = process.wait()
        if returncode == 0:
            output_lines.append(f"REQUEST_JSON: {temp_request_path}")
            return GenerateNkResult(
                success=True,
                script_path=request.script_path,
                stdout_lines=output_lines,
            )

        output_lines.append(f"REQUEST_JSON: {temp_request_path}")
        return GenerateNkResult(
            success=False,
            script_path=request.script_path,
            error=_build_process_error(returncode, output_lines),
            stdout_lines=output_lines,
        )
    except Exception as exc:
        if temp_request_path:
            output_lines.append(f"REQUEST_JSON: {temp_request_path}")
        return GenerateNkResult(
            success=False,
            script_path=request.script_path,
            error=str(exc),
            stdout_lines=output_lines,
        )
    finally:
        if temp_request_path:
            try:
                os.unlink(temp_request_path)
            except OSError:
                pass


class _TemplateEditor:
    REQUIRED_NODES = (
        "BackdropINFO",
        "Read1",
        "Retime1",
        "Reformat3",
        "WriteDN1",
        "ReadDN1",
        "WriteCompMov",
        "WriteCompEXR",
        "ReadCompMov",
        "RetimePreview1",
        "ReadCompEXR",
        "WriteCompMP4",
        "MS_slate_overlay",
    )
    REQUIRED_SLATE_KNOBS = (
        "projecttext",
        "artisttext",
        "startfr",
    )

    def __init__(self, nuke_module):
        self.nuke = nuke_module
        self.request: Optional[GenerateNkRequest] = None

    def edit(self, request: GenerateNkRequest) -> str:
        self.request = request
        self._open_template()
        self._validate_template()
        if request.denoise_plates:
            self._denoise_prototypes()
        self._update_root()
        self._update_info_backdrop()
        self._update_primary_plate()
        self._update_extra_plates()
        self._update_timeline_plate_timing()
        self._update_edit_cut_range()
        self._update_reformats()
        self._update_outputs()
        self._update_denoised_plates()
        self._update_slate_overlay()
        self._update_preview_text()

        script_path = _normalize_path(request.script_path)
        Path(request.script_path).parent.mkdir(parents=True, exist_ok=True)
        self.nuke.scriptSaveAs(script_path, overwrite=1)
        return request.script_path

    def _open_template(self):
        template_path = _resolve_template_path(self.request)
        self.nuke.scriptOpen(_normalize_path(template_path))

    def _validate_template(self):
        missing = [name for name in self.REQUIRED_NODES if self.nuke.toNode(name) is None]
        if missing:
            raise RuntimeError("Template is missing required nodes: " + ", ".join(missing))

        slate_group = self._required_node("MS_slate_overlay")
        missing_knobs = [name for name in self.REQUIRED_SLATE_KNOBS if name not in slate_group.knobs()]
        if missing_knobs:
            raise RuntimeError(
                "Template MS_slate_overlay is missing required knobs: " + ", ".join(missing_knobs)
            )

        slate_group.begin()
        try:
            if self.nuke.toNode("readtime") is None:
                raise RuntimeError("Template MS_slate_overlay is missing required node: readtime")
        finally:
            slate_group.end()

    def _required_node(self, name: str):
        node = self.nuke.toNode(name)
        if node is None:
            raise RuntimeError(f"Template node not found: {name}")
        return node

    def _denoise_prototypes(self):
        read = self._required_node("ReadDN1")
        anchors = [node for node in self.nuke.allNodes()
                   if "identifier" in node.knobs() and node["identifier"].value() == "anchor"
                   and node.input(0) is read]
        if len(anchors) != 1:
            raise RuntimeError("Template needs one Stamps anchor connected to ReadDN1.")
        anchor = anchors[0]
        stamps = [node for node in self.nuke.allNodes()
                  if "identifier" in node.knobs() and node["identifier"].value() == "wired"
                  and "anchor" in node.knobs() and node["anchor"].value() == anchor.name()
                  and node.input(0) is anchor]
        if not stamps:
            raise RuntimeError("Template denoise anchor needs a connected wired Stamps prototype.")
        for node in (anchor, stamps[0]):
            missing = {"title", "prev_title", "knobChanged"} - node.knobs().keys()
            if missing:
                raise RuntimeError(f"Template Stamps prototype {node.name()} is missing knobs: {', '.join(sorted(missing))}")
        return anchor, stamps[0]

    @temporary_cache_task
    def _clone_stamp(self, prototype, name, title, xpos, ypos, input_node, anchor=None):
        # Nuke serialization preserves user knobs, plugin buttons, and callbacks.
        self._clear_selection()
        callback = prototype["knobChanged"].value()
        path = None
        try:
            prototype["knobChanged"].setValue("")
            prototype["selected"].setValue(True)
            with tempfile.NamedTemporaryFile(dir=cache_directory("temp"), suffix=".nk", delete=False) as handle:
                path = handle.name
            self.nuke.nodeCopy(path)
            self._clear_selection()
            node = self.nuke.nodePaste(path)
            for key, value in (("name", name), ("title", title), ("prev_title", title),
                               ("xpos", xpos), ("ypos", ypos), ("selected", False),
                               ("showing", 0), ("toReconnect", False),
                               ("lockCallbacks", False), ("auto_reconnect_by_title", False)):
                self._set_optional_knob(node, key, value)
            self._set_optional_knob(node, "prev_name", node.name())
            if anchor is not None:
                self._set_optional_knob(node, "anchor", anchor.name())
            node.setInput(0, input_node)
            node["knobChanged"].setValue(callback)
            return node
        finally:
            prototype["knobChanged"].setValue(callback)
            self._clear_selection()
            if path:
                os.unlink(path)

    def _update_denoised_plates(self):
        if not self.request.denoise_plates:
            return
        anchor, stamp = self._denoise_prototypes()
        primary_read = self._required_node("ReadDN1")
        base_x = self._node_position(primary_read, "xpos", 0)
        read_y = self._node_position(primary_read, "ypos", 0)
        retime_y = read_y + 180
        anchor_y = retime_y + 180
        stamp_y = anchor_y + 180
        for offset, plate in enumerate(self.request.denoise_plates):
            index = int(plate["read_index"])
            xpos = base_x + offset * 250
            read = primary_read if offset == 0 else self._create_node(
                "Read", f"ReadDN{index}", xpos=xpos, ypos=read_y)
            first, last = plate.get("source_first"), plate.get("source_last")
            if first is None or last is None:
                from denoise_template import native_sequence, resolve_source
                source, first, last = resolve_source(native_sequence(self.nuke, plate["source"]))
                if first is None or last is None:
                    probe = self.nuke.nodes.Read()
                    try:
                        probe["file"].fromUserText(_normalize_path(source))
                        first, last = int(probe["first"].value()), int(probe["last"].value())
                    finally:
                        self.nuke.delete(probe)
            if last < first:
                raise ValueError(f"Invalid source frame range for {plate['source']}")
            dn_last = 1001 + last - first
            self._set_knob(read["file"], plate["file"])
            for key, value in (("file_type", "exr"), ("first", 1001), ("last", dn_last),
                               ("origfirst", 1001), ("origlast", dn_last), ("origset", True),
                               ("colorspace", "ACES - ACEScg")):
                self._set_optional_knob(read, key, value)
            source_label = escape(Path(plate["source"]).name).replace("[", "&#91;").replace("]", "&#93;")
            self._set_knob(read["label"], source_label + "\n" + READ_NODE_LABEL)
            output = self._create_node("Retime", f"RetimeDN{index}",
                                       xpos=xpos, ypos=retime_y, inputs=[read])
            for key, value in (
                ("input.first", 1001), ("input.first_lock", True),
                ("input.last", dn_last), ("input.last_lock", False),
                ("output.first", 1001), ("output.first_lock", True),
                ("output.last", dn_last), ("output.last_lock", False),
                ("speed", 1.0), ("reverse", False), ("time", ""),
            ):
                self._set_optional_knob(output, key, value)
            if offset == 0:
                anchor.setInput(0, output)
                self._set_knob(anchor["ypos"], anchor_y)
                self._set_knob(stamp["ypos"], stamp_y)
            else:
                title = "degrain_" + plate["plate"]
                new_anchor = self._clone_stamp(anchor, f"Anchor_DN{index}", title,
                                               xpos, anchor_y, output)
                self._clone_stamp(stamp, f"Stamp_DN{index}", title,
                                  xpos, stamp_y, new_anchor, anchor=new_anchor)
        for backdrop in self.nuke.allNodes("BackdropNode"):
            if "DENOISE" in str(backdrop["label"].value()).upper():
                right = base_x + (len(self.request.denoise_plates) - 1) * 250 + 180
                self._set_knob(backdrop["bdwidth"], max(float(backdrop["bdwidth"].value()), right - self._node_position(backdrop, "xpos", 0)))
                self._set_knob(backdrop["bdheight"], max(float(backdrop["bdheight"].value()), stamp_y + 100 - self._node_position(backdrop, "ypos", 0)))

    def _update_root(self):
        root = self.nuke.root()
        request = self.request

        self._set_knob(root["frame"], request.frame_first)
        self._set_knob(root["first_frame"], request.frame_first)
        self._set_knob(root["last_frame"], request.frame_last)
        self._set_knob(root["fps"], request.fps)

        format_name = f"{request.shot_name}_{request.format_width}x{request.format_height}"
        format_spec = (
            f"{request.format_width} {request.format_height} 0 0 "
            f"{request.format_width} {request.format_height} 1 {format_name}"
        )
        try:
            self.nuke.addFormat(format_spec)
        except Exception:
            pass
        self._set_knob(root["format"], format_name)

        if "project_directory" in root.knobs():
            root["project_directory"].fromScript("[python {nuke.script_directory()}]")

    def _update_reformats(self):
        reformat3 = self._required_node("Reformat3")
        if "type" in reformat3.knobs():
            self._set_optional_knob(reformat3, "type", "to box")
        self._set_optional_knob(reformat3, "box_width", TARGET_ROOT_FORMAT_WIDTH)
        self._set_optional_knob(reformat3, "box_height", self.request.format_height)
        self._set_optional_knob(reformat3, "box_fixed", False)
        self._set_optional_knob(reformat3, "resize", "width")
        reformat3["disable"].setExpression(f"input.width <= {TARGET_ROOT_FORMAT_WIDTH}")

    def _update_info_backdrop(self):
        node = self._required_node('BackdropINFO')
        text = self._build_info_text()
        self._set_knob(node['label'], text)
        if self.request.shot_metadata_lines:
            font = float(node['note_font_size'].value() or 20) if 'note_font_size' in node.knobs() else 20
            for key, required in (('bdheight', len(text.splitlines()) * font * 1.5 + 40),
                                  ('bdwidth', max(map(len, text.splitlines())) * font * 0.6 + 40)):
                if key in node.knobs():
                    self._set_knob(node[key], max(float(node[key].value() or 0), required))

    def _update_primary_plate(self):
        read1 = self._required_node("Read1")
        retime1 = self._required_node("Retime1")

        self._set_file_knob(read1["file"], self.request.primary_plate_path)
        self._set_optional_knob(read1, "last", max(self.request.duration, 1))
        self._set_optional_knob(read1, "origlast", max(self.request.duration, 1))
        self._set_optional_knob(read1, "origset", True)
        self._set_knob(read1["colorspace"], map_input_colorspace(self.request.colourspace))
        self._set_knob(read1["label"], READ_NODE_LABEL)

        self._set_optional_knob(retime1, "input.last", max(self.request.duration, 1))
        self._set_optional_knob(retime1, "output.first", self.request.frame_first)
        self._set_optional_knob(retime1, "output.first_lock", True)
        self._set_optional_knob(retime1, "output.last", self.request.frame_last)

    def _update_extra_plates(self):
        if not self.request.extra_plate_paths:
            return

        read1 = self._required_node("Read1")
        retime1 = self._required_node("Retime1")
        base_x = self._node_position(read1, "xpos", 0)
        read_y = self._node_position(read1, "ypos", 0)
        retime_y = self._node_position(retime1, "ypos", 0)
        self._clear_selection()

        for offset_index, plate_path in enumerate(self.request.extra_plate_paths, start=1):
            read_index = offset_index + 1
            xpos = base_x + (EXTRA_PLATE_SPACING * offset_index)

            read_node = self._create_node("Read", f"Read{read_index}", xpos=xpos, ypos=read_y)
            self._set_file_knob(read_node["file"], plate_path)
            self._set_optional_knob(read_node, "last", max(self.request.duration, 1))
            self._set_optional_knob(read_node, "origlast", max(self.request.duration, 1))
            self._set_optional_knob(read_node, "origset", True)
            self._set_knob(read_node["colorspace"], map_input_colorspace(self.request.colourspace))
            self._set_knob(read_node["label"], READ_NODE_LABEL)

            retime_node = self._create_node(
                "Retime",
                f"Retime{read_index}",
                xpos=xpos,
                ypos=retime_y,
                inputs=[read_node],
            )
            self._set_optional_knob(retime_node, "input.last", max(self.request.duration, 1))
            self._set_optional_knob(retime_node, "output.first", self.request.frame_first)
            self._set_optional_knob(retime_node, "output.first_lock", True)
            self._set_optional_knob(retime_node, "output.last", self.request.frame_last)
            self._set_optional_knob(retime_node, "time", "")

    def _update_timeline_plate_timing(self):
        """Keep media at 1001 and place the cut after the head handles."""
        if not self.request.plate_timings:
            return
        expected = 1 + len(self.request.extra_plate_paths)
        if len(self.request.plate_timings) != expected:
            raise ValueError('Every timeline plate must have its own source timing')
        for index, timing in enumerate(self.request.plate_timings, 1):
            read = self._required_node(f'Read{index}')
            retime = self._required_node(f'Retime{index}')
            frames = int(timing['frames'])
            if timing.get('single_shot'):
                source_first = int(timing.get('source_first', 1))
                source_last = source_first + frames - 1
                for key, value in (('first', source_first), ('origfirst', source_first),
                                   ('last', source_last), ('origlast', source_last), ('origset', True)):
                    self._set_knob(read[key], value)
                if timing.get('input_colourspace'):
                    self._set_knob(read['colorspace'], map_input_colorspace(timing['input_colourspace']))
                for key, value in (('input.first', source_first), ('input.first_lock', True),
                                   ('input.last', source_last), ('input.last_lock', False),
                                   ('output.first', 1001), ('output.first_lock', True),
                                   ('output.last', 1000 + frames), ('output.last_lock', False),
                                   ('speed', 1.0), ('reverse', False), ('time', '')):
                    self._set_optional_knob(retime, key, value)
                continue
            speed = float(timing['speed'])
            source_first = float(timing['cut_start']) + 1  # Movie Read nodes are one-based.
            working_source_first = source_first - (self.request.head_handles or 0) * speed
            working_source_last = source_first + (self.request.frame_last - self.request.cut_first) * speed
            if speed <= 0 or working_source_first < 1 - 0.000001 or working_source_last > frames + 0.000001:
                raise ValueError('Timeline plate mapping exceeds available media')
            for knob, value in (('first', 1), ('origfirst', 1), ('last', frames), ('origlast', frames), ('origset', True)):
                self._set_knob(read[knob], value)
            if timing.get('input_colourspace'):
                self._set_knob(read['colorspace'], map_input_colorspace(timing['input_colourspace']))
            if abs(speed - 1.0) < 1e-9:
                # Preserve the template's existing Retime node for a simple
                # frame-number offset. Do not replace it or add a speed node.
                anchor = math.floor(working_source_first + 0.000001)
                fraction = working_source_first - anchor
                if abs(fraction) > 0.000001:
                    self._set_knob(read['frame_mode'], 'expression')
                    self._set_knob(read['frame'], f'frame + {fraction:.15g}')
                for knob, value in (
                    ('input.first', anchor), ('input.first_lock', True),
                    ('input.last', frames), ('input.last_lock', False),
                    ('output.first', self.request.frame_first), ('output.first_lock', True),
                    ('output.last_lock', False), ('speed', 1.0), ('reverse', False),
                    ('before', 'continue'), ('after', 'continue'), ('filter', 'none'), ('shutter', 0.0),
                ):
                    self._set_knob(retime[knob], value)
                continue
            # Retime's input anchor rounds fractional frames. A linear TimeWarp
            # preserves the chosen XML/OTIO cut anchor and never fits endpoints.
            dependents = [(node, port) for node in retime.dependent(self.nuke.INPUTS)
                          for port in range(node.inputs()) if node.input(port) is retime]
            xpos = self._node_position(retime, 'xpos', 0)
            ypos = self._node_position(retime, 'ypos', 0)
            self.nuke.delete(retime)
            warp = self._create_node('TimeWarp', f'Retime{index}', xpos=xpos, ypos=ypos, inputs=[read])
            warp['lookup'].setExpression(f'{source_first:.15g} + (frame - {self.request.cut_first}) * {speed:.15g}')
            self._set_knob(warp['filter'], 'none')
            self._set_knob(warp['label'], f'Cut {self.request.cut_first}-{self.request.cut_last}; speed {speed:.9g}')
            for node, port in dependents:
                node.setInput(port, warp)

    def _update_edit_cut_range(self):
        if not self.request.plate_timings:
            return
        viewers = self.nuke.allNodes('Viewer')
        if not viewers:
            viewers = [self._create_node('Viewer', 'Viewer1', xpos=500, ypos=500,
                                        inputs=[self._required_node('Retime1')])]
        for viewer in viewers:
            self._set_knob(viewer['frame_range'], f'{self.request.cut_first}-{self.request.cut_last}')
            self._set_knob(viewer['frame_range_lock'], True)

    def _update_outputs(self):
        self._configure_write_node(
            self._required_node("WriteDN1"),
            self.request.dn_exr_file,
            file_type="exr",
            colorspace=None,
        )

        read_dn = self._required_node("ReadDN1")
        self._set_knob(read_dn["file"], self.request.dn_exr_file)
        self._set_optional_knob(read_dn, "first", self.request.frame_first)
        self._set_optional_knob(read_dn, "last", self.request.frame_last)
        self._set_optional_knob(read_dn, "origfirst", self.request.frame_first)
        self._set_optional_knob(read_dn, "origlast", self.request.frame_last)
        self._set_optional_knob(read_dn, "origset", True)
        self._set_knob(read_dn["colorspace"], "ACES - ACEScg")
        self._set_knob(read_dn["label"], READ_NODE_LABEL)

        self._configure_write_node(
            self._required_node("WriteCompMov"),
            self.request.comp_mov_file,
            file_type="mov",
            colorspace=map_input_colorspace(self.request.colourspace),
        )
        self._configure_write_node(
            self._required_node("WriteCompEXR"),
            self.request.comp_exr_file,
            file_type="exr",
            colorspace=map_input_colorspace(self.request.colourspace),
        )

        read_comp_mov = self._required_node("ReadCompMov")
        self._set_file_knob_if_static(read_comp_mov, self.request.comp_mov_file)
        self._set_knob(read_comp_mov["colorspace"], map_input_colorspace(self.request.colourspace))
        self._set_knob(read_comp_mov["label"], READ_NODE_LABEL)

        retime_preview = self._required_node("RetimePreview1")
        self._set_optional_knob(retime_preview, "input.last", max(self.request.duration, 1))
        self._set_optional_knob(retime_preview, "output.first", self.request.frame_first)
        self._set_optional_knob(retime_preview, "output.first_lock", True)
        self._set_optional_knob(retime_preview, "output.last", self.request.frame_last)

        read_comp_exr = self._required_node("ReadCompEXR")
        self._set_file_knob_if_static(read_comp_exr, self.request.comp_exr_file)
        self._set_knob(read_comp_exr["colorspace"], map_input_colorspace(self.request.colourspace))
        self._set_knob(read_comp_exr["label"], READ_NODE_LABEL)

        self._configure_write_node(
            self._required_node("WriteCompMP4"),
            self.request.preview_mp4_file,
            file_type="mov",
            colorspace="color_picking",
        )

    def _update_slate_overlay(self):
        slate_group = self._required_node("MS_slate_overlay")
        self._set_knob(slate_group["projecttext"], self.request.project_name)
        self._set_knob(slate_group["artisttext"], self.request.artist_name)
        self._set_optional_knob(slate_group, "startfr", self.request.frame_first)

        slate_group.begin()
        try:
            readtime = self._required_node("readtime")
            self._set_optional_knob(readtime, "tc1", self._duration_timecode())
        finally:
            slate_group.end()

    def _update_preview_text(self):
        text10 = self.nuke.toNode("Text10")
        if text10 is not None and "message" in text10.knobs():
            self._set_knob(text10["message"], str(max(self.request.duration - 1, 0)))

        text12 = self.nuke.toNode("Text12")
        if text12 is not None and "message" in text12.knobs():
            self._set_knob(text12["message"], f"{self.request.shot_name}\n{self.request.fps:g}fps")

    def _configure_write_node(self, node, relative_file: str, *, file_type: str, colorspace: Optional[str]):
        self._set_file_knob_if_static(node, relative_file)
        self._set_optional_knob(node, "file_type", file_type)
        self._set_optional_knob(node, "create_directories", True)
        self._set_optional_knob(node, "checkHashOnRead", False)
        if colorspace:
            self._set_optional_knob(node, "colorspace", colorspace)
        self._set_optional_knob(node, "ocioColorspace", "scene_linear")
        self._set_optional_knob(node, "display", "ACES")
        self._set_optional_knob(node, "view", "sRGB")

    def _build_info_text(self) -> str:
        lines = [f"primary: {self._relative_to_script(self.request.primary_plate_path)}"]
        for index, path in enumerate(self.request.extra_plate_paths, start=2):
            lines.append(f"track V{index}: {self._relative_to_script(path)}")
        timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        # Labels support HTML and Tcl substitutions. Metadata must remain literal.
        metadata_text = ''.join(escape(line).replace('[', '&#91;').replace(']', '&#93;') + '\n'
                                for line in self.request.shot_metadata_lines)
        return (
            f"script was auto generated by: {self.request.artist_name}\n"
            f"generated on: {timestamp}\n"
            f"shot: {self.request.shot_name}\n"
            f"edit: {self.request.edit_inpoint}-{self.request.edit_outpoint}\n"
            f"duration: {self.request.duration}\n"
            f"{metadata_text}plates:\n" + "\n".join(lines)
        )

    def _duration_timecode(self) -> str:
        fps = max(int(round(self.request.fps)), 1)
        total = max(self.request.duration - 1, 0)
        hours = total // (fps * 3600)
        minutes = (total % (fps * 3600)) // (fps * 60)
        seconds = (total % (fps * 60)) // fps
        frames = total % fps
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"

    def _relative_to_script(self, target_path: str) -> str:
        script_dir = Path(self.request.script_path).parent
        return _normalize_path(os.path.relpath(target_path, script_dir))

    def _set_file_knob(self, knob, absolute_path: str):
        if Path(absolute_path).suffix.lower() == '.exr':
            from nuke_headless_tasks import resolve_preview_sequence_input
            absolute_path = resolve_preview_sequence_input(absolute_path)[0]
        normalized_absolute = _normalize_path(absolute_path)
        from_user_text = getattr(knob, "fromUserText", None)
        if callable(from_user_text):
            from_user_text(normalized_absolute)
        self._set_knob(knob, self._relative_to_script(absolute_path))

    def _set_file_knob_if_static(self, node, relative_file: str):
        if self._node_uses_root_name_expression(node):
            return
        self._set_knob(node["file"], relative_file)

    def _node_uses_root_name_expression(self, node) -> bool:
        if node is None or "file" not in node.knobs():
            return False
        current_value = node["file"].value()
        return isinstance(current_value, str) and "root.name" in current_value

    def _create_node(self, class_name: str, name: str, *, xpos: int, ypos: int, inputs: Optional[Iterable] = None):
        factory = getattr(self.nuke.nodes, class_name, None)
        if callable(factory):
            node = factory()
        else:
            node = self.nuke.createNode(class_name, inpanel=False)
        self._set_knob(node["name"], name)
        self._set_knob(node["xpos"], xpos)
        self._set_knob(node["ypos"], ypos)
        if inputs:
            for index, input_node in enumerate(inputs):
                if input_node is not None:
                    node.setInput(index, input_node)
        return node

    def _clear_selection(self):
        all_nodes = getattr(self.nuke, "allNodes", None)
        if not callable(all_nodes):
            return
        try:
            nodes = list(all_nodes())
        except Exception:
            return
        for node in nodes:
            if "selected" in node.knobs():
                try:
                    self._set_knob(node["selected"], False)
                except Exception:
                    pass

    def _node_position(self, node, knob_name: str, default: int) -> int:
        if node is None or knob_name not in node.knobs():
            return default
        try:
            return int(node[knob_name].value())
        except Exception:
            return default

    def _set_optional_knob(self, node, knob_name: str, value):
        if knob_name in node.knobs():
            self._set_knob(node[knob_name], value)

    def _set_knob(self, knob, value):
        try:
            knob.setValue(value)
            return
        except TypeError as exc:
            if isinstance(value, str) and self._set_knob_enum_value(knob, value):
                return
            if isinstance(value, str) and self._set_knob_from_script(knob, value):
                return
            if isinstance(value, (list, tuple)) and self._set_knob_sequence(knob, value):
                return
            raise RuntimeError(self._format_knob_error(knob, value, exc)) from exc
        except Exception as exc:
            raise RuntimeError(self._format_knob_error(knob, value, exc)) from exc

    def _set_knob_enum_value(self, knob, value: str) -> bool:
        values_method = getattr(knob, "values", None)
        if not callable(values_method):
            return False
        try:
            values = list(values_method())
        except Exception:
            return False
        if value not in values:
            return False
        knob.setValue(values.index(value))
        return True

    def _set_knob_from_script(self, knob, value: str) -> bool:
        from_script = getattr(knob, "fromScript", None)
        if not callable(from_script):
            return False
        try:
            from_script(value)
            return True
        except Exception:
            return False

    def _set_knob_sequence(self, knob, value: list | tuple) -> bool:
        try:
            for index, item in enumerate(value):
                knob.setValue(item, index)
            return True
        except Exception:
            return False

    def _format_knob_error(self, knob, value, exc: Exception) -> str:
        knob_name = "<unknown>"
        node_name = "<unknown>"
        try:
            knob_name = knob.name()
        except Exception:
            pass
        try:
            node_name = knob.node().name()
        except Exception:
            pass
        return (
            f"Failed to set knob '{knob_name}' on node '{node_name}' "
            f"with value {value!r}: {exc}"
        )


class MakeNkTask:
    def __init__(self):
        self.nuke = None

    def setup_nuke(self):
        if self.nuke is not None:
            return self.nuke
        try:
            self.nuke = importlib.import_module("nuke")
            return self.nuke
        except ImportError as exc:
            raise RuntimeError("Must be run via: Nuke -t create_nk.py make_nk --request ...") from exc

    def run(self, request_path: str) -> bool:
        self.setup_nuke()
        with open(request_path, "r", encoding="utf-8") as handle:
            request = GenerateNkRequest.from_dict(json.load(handle))

        print(f"Loaded request: {request_path}")
        print(f"Editing template: {_resolve_template_path(request)}")
        print(f"Creating script: {request.script_path}")
        editor = _TemplateEditor(self.nuke)
        editor.edit(request)
        print(f"Created script: {request.script_path}")
        return True


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Template-based ShotBox Nuke script creation")
    subparsers = parser.add_subparsers(dest="command")

    make_nk = subparsers.add_parser("make_nk", help="Create a .nk script from a JSON request")
    make_nk.add_argument("--request", required=True, help="Path to GenerateNkRequest JSON file")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "make_nk":
        task = MakeNkTask()
        try:
            task.run(args.request)
            return 0
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            print(f"ERROR: {detail}")
            traceback.print_exc()
            return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
