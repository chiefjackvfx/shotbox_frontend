"""Version-one disk/process contracts. Standard library only; usable in Nuke 3.11."""
from __future__ import annotations

import codecs
from dataclasses import dataclass
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any

SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1
INTEGRATION_VERSION = "0.1.0"
SOURCE_COLORS = ("linear-srgb", "acescg", "srgb")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ProtocolError(ValueError):
    """A malformed or incompatible integration contract."""


def generation_size(width: int, height: int, target_height: int = 544) -> tuple[int, int]:
    """Match backend spatial fitting, without importing its runtime in Nuke."""
    integer(width, "source width", 1)
    integer(height, "source height", 1)
    integer(target_height, "generation height", 32)
    if target_height > 1088:
        raise ProtocolError("Generation height must be 32–1088.")
    scale = min(1920 / width, target_height / height)
    return max(32, round(width * scale)), max(32, round(height * scale))


def integer(value: Any, name: str, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ProtocolError(f"{name} must be an integer" + (f" >= {minimum}" if minimum is not None else ""))
    return value


@dataclass(frozen=True)
class FrameMap:
    start: int
    end: int

    def __post_init__(self):
        integer(self.start, "start")
        integer(self.end, "end")
        if self.count < 1:
            raise ProtocolError("Choose an inclusive range containing at least one frame.")

    @property
    def count(self) -> int:
        return self.end - self.start + 1

    def cache_index(self, frame: int) -> int:
        integer(frame, "frame")
        if not self.start <= frame <= self.end:
            raise ProtocolError("Timeline frame is outside the captured range.")
        return frame - self.start + 1

    def nuke_frame(self, index: int) -> int:
        integer(index, "cache index", 1)
        if index > self.count:
            raise ProtocolError("Cache index is outside the captured range.")
        return index + self.start - 1

    @property
    def read_expression(self) -> str:
        return f"frame - ({self.start}) + 1"

    def as_dict(self) -> dict:
        return {"start": self.start, "end": self.end, "count": self.count,
                "first": 1, "last": self.count, "expression": self.read_expression}


def _object(value, name):
    if not isinstance(value, dict):
        raise ProtocolError(f"{name} must be an object.")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{name} must be a nonempty string.")
    return value


def validate_result(value: Any) -> dict:
    result = _object(value, "result")
    if result.get("status") != "complete":
        raise ProtocolError("Worker result is not complete.")
    integer(result.get("frame_count"), "result.frame_count", 1)
    for name in ("output_dir", "first_source", "first_output", "source_preview", "matte_preview", "manifest", "job_dir"):
        _text(result.get(name), f"result.{name}")
    return result


def validate_file_record(record: Any) -> dict:
    record = _object(record, "file record")
    _text(record.get("path"), "file.path")
    integer(record.get("size"), "file.size", 1)
    if not isinstance(record.get("sha256"), str) or not SHA256.fullmatch(record["sha256"]):
        raise ProtocolError("File record requires a SHA-256 digest.")
    return record


def _published_basename(value: Any, name: str) -> str:
    value = _text(value, name)
    if (value in (".", "..") or any(char in value for char in "/\\\x00\r\n[]${}:\"<>|?*")
            or PurePosixPath(value).name != value or PureWindowsPath(value).drive):
        raise ProtocolError(f"{name} must be a safe final EXR basename.")
    return value


def _published_storage(value: Any) -> dict:
    storage = _object(value, "storage")
    if storage.get("mode") != "published":
        raise ProtocolError("Unsupported storage mode; expected published.")
    directory = _text(storage.get("output_directory"), "storage.output_directory")
    posix, windows = PurePosixPath(directory), PureWindowsPath(directory)
    path = windows if windows.drive else posix
    if (not path.is_absolute() or len(path.parts) < 2 or ".." in path.parts
            or any(char in directory for char in "\x00\r\n[]$")):
        raise ProtocolError("Published output directory must be an absolute directory, not a filesystem root.")
    return storage


def validate_manifest(value: Any, *, require_complete: bool = False) -> dict:
    manifest = _object(value, "integration manifest")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != SCHEMA_VERSION:
        raise ProtocolError("Unsupported integration schema_version; expected 1.")
    for key in ("integration_version", "node_id", "attempt_id", "created_at", "state", "worker_path"):
        _text(manifest.get(key), key)
    if manifest["state"] not in ("idle", "probing", "capturing", "preflight", "generating", "validating",
                                  "complete", "failed", "cancelled", "interrupted"):
        raise ProtocolError("Unsupported attempt state.")
    published = "storage" in manifest
    if published:
        _published_storage(manifest["storage"])
        if "precomp" not in manifest:
            raise ProtocolError("Published storage requires a complete EXR publication.")
    if not isinstance(manifest.get("worker_sha256"), str) or not SHA256.fullmatch(manifest["worker_sha256"]):
        raise ProtocolError("Manifest requires the exact worker JSON SHA-256.")
    capture = _object(manifest.get("capture"), "capture")
    mapping = FrameMap(capture.get("start"), capture.get("end"))
    integer(capture.get("count"), "capture.count", 1)
    if capture.get("count") != mapping.count:
        raise ProtocolError("Capture count does not match timeline range.")
    for key in ("width", "height"):
        integer(capture.get(key), f"capture.{key}", 1)
    _text(capture.get("view"), "capture.view")
    fps = capture.get("fps")
    if type(fps) not in (int, float) or not math.isfinite(fps) or fps <= 0:
        raise ProtocolError("Capture FPS must be finite and positive.")
    if type(capture.get("pixel_aspect")) not in (int, float) or capture["pixel_aspect"] != 1.0:
        raise ProtocolError("Only square-pixel captures are supported.")
    if "frame_map" in manifest and manifest["frame_map"] != mapping.as_dict():
        raise ProtocolError("Recorded frame map does not match the capture.")
    for name in ("settings", "fingerprint", "host"):
        _object(manifest.get(name), name)
    if manifest["settings"].get("source_color") not in SOURCE_COLORS:
        raise ProtocolError("Select an explicit supported source colour encoding.")
    for name, records in (("capture.files", capture.get("files")), ("outputs", manifest.get("outputs", []))):
        if not isinstance(records, list):
            raise ProtocolError(f"{name} must be a list.")
        for record in records:
            validate_file_record(record)
        if len({record["path"] for record in records}) != len(records):
            raise ProtocolError(f"{name} contains duplicate paths.")
    if require_complete or published:
        if manifest.get("state") != "complete" or type(manifest.get("worker_exit")) is not int or manifest["worker_exit"] != 0:
            raise ProtocolError("Cache must have a complete attempt and successful worker exit.")
        result = validate_result(manifest.get("result"))
        if result["frame_count"] != mapping.count:
            raise ProtocolError("Worker result count differs from the frame map.")
        if len(capture["files"]) != mapping.count or len(manifest.get("outputs", [])) != mapping.count:
            raise ProtocolError("Cache file count differs from the frame map.")
        _text(manifest.get("backend_manifest"), "backend_manifest")
        validation = _object(manifest.get("validation"), "validation")
        if validation.get("valid") is not True:
            raise ProtocolError("Cache lacks a successful image validation report.")
    if "precomp" in manifest:
        precomp = _object(manifest["precomp"], "precomp")
        kind = precomp.get("kind", "rgba")
        if kind not in ("rgba", "alpha"):
            raise ProtocolError("Unknown precomp channel kind.")
        allowed_precision = ("half", "float") if kind == "alpha" else ("float",)
        if precomp.get("state") != "complete" or precomp.get("precision") not in allowed_precision:
            raise ProtocolError("Precomp must be a complete EXR publication with supported precision.")
        if kind == "alpha" and precomp.get("channels") != ["rgba.alpha"]:
            raise ProtocolError("Alpha publication channels must contain only alpha.")
        if kind == "alpha" and precomp["precision"] != manifest["settings"].get("precision"):
            raise ProtocolError("Alpha publication precision must match the worker output.")
        integer(precomp.get("first"), "precomp.first")
        integer(precomp.get("last"), "precomp.last")
        if precomp.get("first") != mapping.start or precomp.get("last") != mapping.end:
            raise ProtocolError("Precomp timeline range differs from the captured range.")
        pattern = _text(precomp.get("pattern"), "precomp.pattern")
        if pattern.count("%04d") != 1 or "%" in pattern.replace("%04d", "") or not pattern.endswith(".exr"):
            raise ProtocolError("Precomp requires an exact %04d EXR pattern.")
        records = precomp.get("files")
        if not isinstance(records, list) or len(records) != mapping.count:
            raise ProtocolError("Precomp frame count differs from the captured range.")
        for record in records:
            validate_file_record(record)
            geometry = _object(record.get("geometry"), "precomp.geometry")
            integer(geometry.get("width"), "precomp.width", 1)
            integer(geometry.get("height"), "precomp.height", 1)
            if record.get("geometry") != {"width": capture["width"], "height": capture["height"]}:
                raise ProtocolError("Precomp format differs from the captured format.")
            box = record.get("box")
            if not isinstance(box, list) or len(box) != 4 or any(type(value) is not int for value in box):
                raise ProtocolError("Precomp data window must contain four integer coordinates.")
            if (box[2] <= box[0] or box[3] <= box[1] or type(record.get("pixel_aspect")) not in (float, int)
                    or record["pixel_aspect"] != 1.0):
                raise ProtocolError("Precomp data window or pixel aspect is invalid.")
            channels = record.get("channels")
            if kind == "alpha":
                if channels != ["rgba.alpha"] or box != [0, 0, capture["width"], capture["height"]]:
                    raise ProtocolError("Alpha publication must contain only alpha over the captured raster.")
            elif (not isinstance(channels, list) or any(not isinstance(value, str) for value in channels)
                  or not {"rgba.red", "rgba.green", "rgba.blue", "rgba.alpha"}.issubset(channels)):
                raise ProtocolError("Precomp must contain original RGB and generated alpha.")
        if kind == "rgba":
            sources = capture.get("source_files")
            if not isinstance(sources, list) or len(sources) != mapping.count:
                raise ProtocolError("Precomp requires its immutable original-channel snapshot.")
            for record in sources:
                validate_file_record(record)
        if published:
            _published_basename(pattern, "precomp.pattern")
            for record in records:
                _published_basename(record["path"], "precomp.file.path")
            expected = [pattern % frame for frame in range(mapping.start, mapping.end + 1)]
            if [record["path"] for record in records] != expected:
                raise ProtocolError("Published precomp files do not match the timeline sequence.")
    return manifest


def _json_object(line: str) -> dict:
    def reject_constant(value):
        raise ProtocolError(f"Non-finite JSON number {value}.")
    def unique_object(items):
        output = {}
        for key, value in items:
            if key in output:
                raise ProtocolError(f"Duplicate JSON field {key!r}.")
            output[key] = value
        return output
    try:
        return _object(json.loads(line, parse_constant=reject_constant, object_pairs_hook=unique_object), "event")
    except (ValueError, TypeError) as exc:
        raise ProtocolError(f"Malformed stdout JSON event: {exc}") from exc


class EventParser:
    """Bounded incremental stdout parser; stderr must be logged separately.

    A parser is single-use for one owned process. No terminal report is accepted
    until finish checks the actual process exit status.
    """

    def __init__(self, expected_terminal: str = "result", *, max_line_bytes: int = 1024 * 1024):
        if expected_terminal not in ("result", "probe", "plan", "validation"):
            raise ValueError("Unknown terminal event type.")
        if max_line_bytes < 1:
            raise ValueError("Line bound must be positive.")
        self.expected_terminal = expected_terminal
        self.max_line_bytes = max_line_bytes
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self._buffer = ""
        self._terminal = None
        self._ended = False
        self._failure = None

    @property
    def terminal(self):
        return self._terminal

    def _event(self, line: str) -> dict | None:
        if not line.strip():
            return None
        event = _json_object(line)
        kind = event.get("type")
        if kind == "progress":
            _text(event.get("stage"), "progress.stage")
            if "current" in event:
                integer(event["current"], "progress.current", 0)
            if "total" in event:
                integer(event["total"], "progress.total", 0)
        elif kind in ("result", "probe", "plan", "validation", "error", "cancelled"):
            if self._terminal is not None:
                raise ProtocolError("Multiple terminal events from one process.")
            if kind in ("error", "cancelled"):
                _text(event.get("message"), f"{kind}.message")
            elif kind != self.expected_terminal:
                raise ProtocolError(f"Unexpected terminal event {kind!r}.")
            elif kind == "result":
                validate_result(event.get("result"))
            else:
                if type(event.get("protocol_version")) is not int or event["protocol_version"] != PROTOCOL_VERSION:
                    raise ProtocolError("Unsupported bridge protocol_version; expected 1.")
                if kind == "plan":
                    _object(event.get("plan"), "plan")
                    _object(event.get("capture"), "capture")
                else:
                    _object(event.get("report"), "report")
                    if kind == "validation" and event["report"].get("valid") is not True:
                        raise ProtocolError("Bridge did not validate the cache.")
            self._terminal = event
        else:
            raise ProtocolError(f"Unknown event type {kind!r}.")
        return event

    def feed(self, data: bytes) -> list[dict]:
        if self._ended or self._failure:
            raise ProtocolError(self._failure or "Process stream is already closed.")
        try:
            decoded = self._decoder.decode(data)
            events = []
            # Splitting avoids retaining an entire chunk with many valid lines.
            for fragment in decoded.splitlines(keepends=True):
                self._buffer += fragment
                if len(self._buffer.encode("utf-8")) > self.max_line_bytes:
                    raise ProtocolError("Process stdout event exceeds the configured byte limit.")
                if self._buffer.endswith("\n"):
                    event = self._event(self._buffer)
                    self._buffer = ""
                    if event is not None:
                        events.append(event)
            return events
        except (UnicodeError, ProtocolError) as exc:
            self._failure = str(exc)
            self._buffer = ""
            raise ProtocolError(self._failure) from exc

    def finish(self, exit_code: int, normal_exit: bool = True) -> dict:
        if self._ended or self._failure:
            raise ProtocolError(self._failure or "Process stream is already closed.")
        self._ended = True
        try:
            self._buffer += self._decoder.decode(b"", final=True)
            if self._buffer:
                self._event(self._buffer)
            self._buffer = ""
        except (UnicodeError, ProtocolError) as exc:
            raise ProtocolError(f"Invalid final stdout event: {exc}") from exc
        if not normal_exit or type(exit_code) is not int or exit_code != 0:
            detail = self._terminal.get("message", "") if self._terminal else ""
            raise ProtocolError(f"Process failed or was cancelled (exit {exit_code}). {detail}".strip())
        if self._terminal is None or self._terminal.get("type") != self.expected_terminal:
            raise ProtocolError("Process exited without a successful terminal report.")
        return self._terminal
