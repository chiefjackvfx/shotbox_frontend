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
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from nuke_headless_tasks import find_nuke_executable, map_input_colorspace


READ_NODE_LABEL = "Fr. range: [value first] - [value last]\nRes: [value width] * [value height]"
RUNTIME_TEMPLATE_NAME = "template_current.nk"
EXTRA_PLATE_SPACING = 150


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
    dn_exr_file: str = ""
    comp_mov_file: str = ""
    comp_exr_file: str = ""
    preview_mp4_file: str = ""

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
            dn_exr_file=str(data.get("dn_exr_file", "")),
            comp_mov_file=str(data.get("comp_mov_file", "")),
            comp_exr_file=str(data.get("comp_exr_file", "")),
            preview_mp4_file=str(data.get("preview_mp4_file", "")),
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
        "WriteDN1",
        "ReadDN1",
        "WriteCompMov",
        "WriteCompEXR",
        "ReadCompMov",
        "RetimePreview1",
        "ReadCompEXR",
        "WriteCompMP4",
        "MS_slate_overlay",
        "Text11",
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
        self._update_root()
        self._update_info_backdrop()
        self._update_primary_plate()
        self._update_extra_plates()
        self._update_outputs()
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

    def _update_info_backdrop(self):
        self._set_knob(self._required_node("BackdropINFO")["label"], self._build_info_text())

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
        self._set_optional_knob(retime1, "time", "")

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
        self._set_knob(read_dn["colorspace"], map_input_colorspace(self.request.colourspace))
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
        self._set_knob(read_comp_mov["file"], self.request.comp_mov_file)
        self._set_knob(read_comp_mov["colorspace"], map_input_colorspace(self.request.colourspace))
        self._set_knob(read_comp_mov["label"], READ_NODE_LABEL)

        retime_preview = self._required_node("RetimePreview1")
        self._set_optional_knob(retime_preview, "input.last", max(self.request.duration, 1))
        self._set_optional_knob(retime_preview, "output.first", self.request.frame_first)
        self._set_optional_knob(retime_preview, "output.first_lock", True)
        self._set_optional_knob(retime_preview, "output.last", self.request.frame_last)
        self._set_optional_knob(retime_preview, "time", "")

        read_comp_exr = self._required_node("ReadCompEXR")
        self._set_knob(read_comp_exr["file"], self.request.comp_exr_file)
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
        text11 = self._required_node("Text11")
        self._set_knob(
            text11["message"],
            f"{self.request.frame_first}-{self.request.frame_last}\n{self._duration_timecode()}",
        )

        text10 = self.nuke.toNode("Text10")
        if text10 is not None and "message" in text10.knobs():
            self._set_knob(text10["message"], str(max(self.request.duration - 1, 0)))

        text12 = self.nuke.toNode("Text12")
        if text12 is not None and "message" in text12.knobs():
            self._set_knob(text12["message"], f"{self.request.shot_name}\n{self.request.fps:g}fps")

    def _configure_write_node(self, node, relative_file: str, *, file_type: str, colorspace: Optional[str]):
        self._set_knob(node["file"], relative_file)
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
        return (
            f"script was auto generated by: {self.request.artist_name}\n"
            f"generated on: {timestamp}\n"
            f"shot: {self.request.shot_name}\n"
            f"edit: {self.request.edit_inpoint}-{self.request.edit_outpoint}\n"
            f"duration: {self.request.duration}\n"
            f"plates:\n" + "\n".join(lines)
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
        normalized_absolute = _normalize_path(absolute_path)
        from_user_text = getattr(knob, "fromUserText", None)
        if callable(from_user_text):
            from_user_text(normalized_absolute)
        self._set_knob(knob, self._relative_to_script(absolute_path))

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
