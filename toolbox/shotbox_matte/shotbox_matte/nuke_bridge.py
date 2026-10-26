"""External Nuke integration helpers; never import this module inside Nuke.

Only ``probe`` imports torch (to query capabilities). Planning and validation
decode one EXR at a time and never import the native inference adapter.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
from dataclasses import fields
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from typing import Callable

from .build_info import build_metadata
from .core import JobOptions, plan_job
from .export import frame_filename
from .imaging import SOURCE_COLORS, _oiio, read_alpha_exr, read_exr, read_image_window, spatial_plan
from .models import DEFAULT_MODEL_ROOT, MODEL_FILES, ModelConfig, validate_models
from .temporal import temporal_chunks

PROTOCOL_VERSION = 1
Progress = Callable[[str, int, int, str], None]


def _read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Non-finite JSON: {value}")))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _integer(value, name: str, minimum: int | None = None) -> int:
    if type(value) is not int or (minimum is not None and value < minimum):
        raise ValueError(f"{name} must be an integer" + (f" >= {minimum}." if minimum is not None else "."))
    return value


def _owned(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or PurePosixPath(relative).is_absolute() or ":" in relative
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ValueError(f"Expected an owned relative path: {relative!r}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"Path escapes attempt: {relative}")
    return path


def _relative(root: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"Path outside owned attempt: {path}") from exc
    _owned(root, relative)
    return relative


def _precision(path: Path, expected: str | None = None, *, channels: str = "rgb") -> str:
    if channels not in ("rgb", "alpha"):
        raise ValueError("Output channel mode must be rgb or alpha.")
    oiio = _oiio()
    reader = oiio.ImageInput.open(str(path))
    if reader is None:
        raise ValueError(f"Cannot open EXR: {path}")
    try:
        spec = reader.spec()
        expected_names = ["A"] if channels == "alpha" else ["R", "G", "B"]
        if list(spec.channelnames) != expected_names or spec.nchannels != len(expected_names):
            raise ValueError(f"Expected exactly flat {'/'.join(expected_names)} channels: {path}")
        formats = list(spec.channelformats) or [spec.format] * len(expected_names)
        names = {str(value) for value in formats}
        if len(names) != 1 or names.pop() not in ("half", "float"):
            raise ValueError(f"Expected uniform half/float EXR channels: {path}")
        precision = str(formats[0])
        if expected and precision != expected:
            raise ValueError(f"EXR precision mismatch in {path}: expected {expected}, found {precision}")
        if reader.seek_subimage(1, 0):
            raise ValueError(f"Multipart EXRs are unsupported: {path}")
        return precision
    finally:
        reader.close()


def _record(path: Path, root: Path, *, expected_geometry=None, precision=None, grayscale=False,
            channels="rgb") -> dict:
    import numpy as np

    relative = _relative(root, path)  # Reject escaping symlinks before opening anything.
    geometry = read_image_window(path).to_dict()
    if expected_geometry is not None and geometry != expected_geometry:
        raise ValueError(f"EXR geometry/window/PAR changed: {path}")
    if geometry["pixel_aspect_ratio"] != 1.0:
        raise ValueError(f"Non-square pixels are unsupported: {path}")
    actual_precision = _precision(path, precision, channels=channels)
    pixels = read_alpha_exr(path) if channels == "alpha" else read_exr(path)
    if grayscale and channels == "rgb" and (not np.array_equal(pixels[..., 0], pixels[..., 1])
                      or not np.array_equal(pixels[..., 0], pixels[..., 2])):
        raise ValueError(f"Matte is not RGB grayscale: {path}")
    return {"path": relative, "size": path.stat().st_size, "sha256": _sha256(path),
            "geometry": geometry, "precision": actual_precision}


def load_job(path: Path) -> JobOptions:
    """Enforce the narrower Nuke capture contract without changing CLI defaults."""
    data = _read_json(path)
    allowed = {item.name for item in fields(JobOptions)}
    required = allowed - {"output_channels"}
    if required - set(data) or set(data) - allowed:
        raise ValueError(f"Flat worker JobOptions fields mismatch; missing={sorted(required - set(data))}, "
                         f"unknown={sorted(set(data) - allowed)}")
    for name in ("frame_skip", "frame_cap", "generation_width", "generation_height", "seed",
                 "first_frame", "padding", "source_start", "source_end"):
        _integer(data[name], name, 0 if name in ("frame_skip", "frame_cap", "seed") else 1)
    if data["source_start"] != 1 or data["source_end"] < 1:
        raise ValueError("Captured source range must be 1..N with at least one frame.")
    for name, expected in (("frame_skip", 0), ("frame_cap", 0), ("first_frame", 1),
                           ("padding", 4), ("suffix", "_matte"), ("existing", "version-up"), ("sequence", None)):
        if data[name] != expected:
            raise ValueError(f"Nuke worker {name} must be {expected!r}.")
    if data["source_color"] not in SOURCE_COLORS:
        raise ValueError("Choose an explicit supported source colour before generation.")
    if (type(data["fps"]) not in (int, float) or not math.isfinite(data["fps"]) or data["fps"] <= 0):
        raise ValueError("FPS must be finite and positive.")
    if type(data["low_vram"]) is not bool or data["offload"] not in ("none", "cpu", "disk"):
        raise ValueError("Invalid low_vram/offload options.")
    if data["temporal_mode"] not in ("segmented", "full-range"):
        raise ValueError("Invalid temporal_mode.")
    if data["precision"] not in ("half", "float"):
        raise ValueError("Invalid precision.")
    if data.get("output_channels", "rgb") not in ("rgb", "alpha"):
        raise ValueError("Invalid output_channels; expected rgb or alpha.")
    models = data["models"]
    if not isinstance(models, dict) or set(models) != set(MODEL_FILES):
        raise ValueError("Worker requires all six explicit model paths.")
    for name, value in {"input_path": data["input_path"], **models}.items():
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError(f"{name} must be an absolute path.")
    return JobOptions(**data)


def preflight(job: Path | str, progress: Progress | None = None) -> dict:
    job = Path(job).resolve()
    options = load_job(job)
    root = job.parent
    source = Path(options.input_path).resolve()
    _relative(root, source)
    if not source.is_dir():
        raise ValueError("Captured input must be an owned sequence directory.")
    expected = [source / f"capture.{index:04d}.exr" for index in range(1, options.source_end + 1)]
    if {path.name for path in source.iterdir()} != {path.name for path in expected}:
        raise ValueError("Capture files must be exactly capture.0001.exr through capture.NNNN.exr; gaps/extra files unsupported.")
    for path in expected:
        _relative(root, path)
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Capture must be a regular owned file: {path}")
    plan = plan_job(options)
    if tuple(path.resolve() for path in plan.selected_files) != tuple(expected):
        raise ValueError("Selected capture frame map must be exactly 1..N.")
    spatial = plan.spatial
    if spatial.width > 1920 or spatial.height > 1088 or min(spatial.width, spatial.height) < 32:
        raise ValueError("Padded spatial dimensions exceed native generation bounds.")
    records = []
    for index, path in enumerate(expected, 1):
        record = _record(path, root, expected_geometry=records[0]["geometry"] if records else None, precision="float")
        geometry = record["geometry"]
        if (geometry["x"], geometry["y"], geometry["width"], geometry["height"]) != (
                geometry["full_x"], geometry["full_y"], geometry["full_width"], geometry["full_height"]):
            raise ValueError("Capture data and display windows must match the fixed format rectangle.")
        records.append(record)
        if progress:
            progress("preflight", index, len(expected), path.name)
    return {"plan": plan.to_dict(), "capture": {"count": len(records), "geometry": records[0]["geometry"],
            "width": spatial.source_width, "height": spatial.source_height, "files": records,
            "worker_sha256": _sha256(job)}}


def _pinned_revision() -> str:
    # Read the shared literal without importing the inference module.
    tree = ast.parse(Path(__file__).with_name("native.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "LTX_REVISION"
                                                for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Backend does not declare a pinned LTX revision.")


def _dependencies() -> tuple[dict, list[str]]:
    versions, errors = {}, []
    for package, module in (("numpy", "numpy"), ("Pillow", "PIL"), ("OpenImageIO", "OpenImageIO"),
                            ("av", "av"), ("torch", "torch"), ("ltx-core", "ltx_core"),
                            ("ltx-pipelines", "ltx_pipelines")):
        try:
            version = importlib.metadata.version(package)
            present = importlib.util.find_spec(module) is not None
            versions[package] = {"version": version, "available": present}
            if not present:
                errors.append(f"Dependency {package} cannot be imported.")
        except (ImportError, ValueError, importlib.metadata.PackageNotFoundError):
            versions[package] = {"version": None, "available": False}
            errors.append(f"Missing required dependency: {package}.")
    return versions, errors


def _cuda_report() -> dict:
    try:
        import torch
        available = torch.cuda.is_available()
        bf16 = bool(available and torch.cuda.is_bf16_supported())
        devices = []
        if available:
            for index in range(torch.cuda.device_count()):
                properties = torch.cuda.get_device_properties(index)
                devices.append({"index": index, "name": properties.name, "total_memory": properties.total_memory})
        return {"available": bool(available), "bf16": bf16, "torch_cuda": torch.version.cuda, "devices": devices}
    except Exception as exc:
        return {"available": False, "bf16": False, "error": str(exc)}


def probe(request: Path | str) -> dict:
    from .cli import build_parser, load_models

    request = Path(request).resolve()
    data = _read_json(request)
    allowed = {"schema_version", "model_root", "model_config", "models", "cache_root", "capture"}
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1 or set(data) - allowed:
        raise ValueError("Unsupported runtime request schema/version or fields.")
    for name in ("model_root", "model_config", "cache_root"):
        if name in data and data[name] is not None and (not isinstance(data[name], str) or not data[name]):
            raise ValueError(f"{name} must be a path string.")
    overrides = data.get("models", {})
    if not isinstance(overrides, dict) or set(overrides) - set(MODEL_FILES):
        raise ValueError("Invalid explicit model overrides.")
    if any(not isinstance(value, str) or not value for value in overrides.values()):
        raise ValueError("Model overrides must be nonempty path strings.")
    args = build_parser().parse_args([])
    args.model_root = Path(data.get("model_root") or DEFAULT_MODEL_ROOT)
    args.models_config = Path(data["model_config"]) if data.get("model_config") else None
    for name, value in overrides.items():
        setattr(args, name, Path(value))
    models = ModelConfig.from_dict(load_models(args))
    models = ModelConfig.from_dict({name: str(Path(value).resolve()) if value else None
                                    for name, value in models.to_dict().items()})
    model_errors = validate_models(models)
    identities = {}
    for name, value in models.to_dict().items():
        if value and Path(value).is_file():
            stat = Path(value).stat()
            identities[name] = {"path": value, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                                "identity_method": "path-size-mtime_ns; header validation; no full checksum"}
    versions, errors = _dependencies()
    if not (3, 12) <= sys.version_info[:2] < (3, 14):
        errors.append("Shotbox's Python requires version 3.12 or 3.13. Run the Shotbox installer or updater.")
    errors.extend(model_errors)
    cuda = _cuda_report()
    if not cuda["available"] or not cuda["bf16"]:
        errors.append("Native generation requires an available CUDA GPU with bf16 support.")
    disk = {}
    if data.get("cache_root"):
        cache_root = Path(data["cache_root"]).expanduser().resolve()
        if not cache_root.is_dir():
            errors.append(f"Cache root must be an existing directory: {cache_root}")
        else:
            try:
                with tempfile.NamedTemporaryFile(prefix=".shotbox-probe-", dir=cache_root) as handle:
                    handle.write(b"ShotBox writable-path probe\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                usage = shutil.disk_usage(cache_root)
                disk = {"root": str(cache_root), "free": usage.free, "total": usage.total, "writable": True}
                if "capture" in data:
                    capture = data["capture"]
                    if not isinstance(capture, dict) or set(capture) - {"width", "height", "count", "precision", "precomp_channels"}:
                        raise ValueError("Invalid capture storage estimate request.")
                    width, height, count = (_integer(capture.get(key), f"capture.{key}", 1)
                                            for key in ("width", "height", "count"))
                    if capture.get("precision", "half") not in ("half", "float"):
                        raise ValueError("Invalid capture count/precision for disk estimate.")
                    # Inputs + output + staged output; overhead and no compression savings.
                    estimate = count * (width * height * (12 + 2 * (6 if capture.get("precision", "half") == "half" else 12))
                                        + 3 * 1024 * 1024)
                    if "precomp_channels" in capture:
                        channels = _integer(capture["precomp_channels"], "capture.precomp_channels", 4)
                        estimate += count * width * height * channels * 12  # source + publication + staging, float32
                    disk["estimated_required_bytes"] = estimate
                    if usage.free < estimate:
                        errors.append(f"Insufficient cache disk space: need approximately {estimate} bytes; {usage.free} free.")
            except OSError as exc:
                errors.append(f"Cache root is not writable: {exc}")
    return {"ready": not errors, "errors": errors,
            "python": {"executable": sys.executable, "version": sys.version, "platform": sys.platform},
            "dependencies": versions, "models": models.to_dict(), "model_identities": identities,
            "model_validation": {"valid": not model_errors, "errors": model_errors}, "cuda": cuda,
            "backend": build_metadata(), "ltx_revision": _pinned_revision(), "disk": disk}


def validate(attempt: Path | str, progress: Progress | None = None) -> dict:
    """Validate original or moved complete attempt without consulting old paths."""
    attempt = Path(attempt).resolve()
    root = attempt.parent
    data = _read_json(attempt)
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Unsupported integration schema version.")
    required = {"integration_version", "node_id", "attempt_id", "state", "created_at", "worker_path",
                "worker_sha256", "capture", "fingerprint", "settings", "host", "probe", "result",
                "worker_exit", "outputs", "validation", "original_attempt_root"}
    if required - set(data):
        raise ValueError(f"Integration manifest missing fields: {sorted(required - set(data))}")
    if data["integration_version"] != "0.1.0" or data["state"] not in ("validating", "complete"):
        raise ValueError("Only a compatible validating/complete attempt can be validated.")
    for name in ("node_id", "attempt_id", "created_at"):
        if not isinstance(data[name], str) or not data[name]:
            raise ValueError(f"Invalid integration {name}.")
    for name in ("fingerprint", "settings", "host", "probe", "validation"):
        if not isinstance(data[name], dict):
            raise ValueError(f"Integration {name} must be an object.")
    if type(data["worker_exit"]) is not int or data["worker_exit"] != 0:
        raise ValueError("Worker did not exit normally with code zero.")
    result = data["result"]
    if not isinstance(result, dict) or result.get("status") != "complete":
        raise ValueError("Worker result must have status complete.")
    if not isinstance(data["original_attempt_root"], str) or not data["original_attempt_root"]:
        raise ValueError("Original attempt root must be an absolute path string.")
    original = Path(data["original_attempt_root"])
    if not original.is_absolute():
        raise ValueError("Original attempt root must be absolute.")

    def remap(value):
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ValueError("Backend provenance paths must be absolute.")
        try:
            relative = Path(value).relative_to(original).as_posix()
        except ValueError as exc:
            raise ValueError(f"Backend path outside original attempt: {value}") from exc
        return _owned(root, relative)

    worker = _owned(root, data["worker_path"])
    if _sha256(worker) != data["worker_sha256"]:
        raise ValueError("Worker JSON digest mismatch.")
    options = load_job(worker)
    capture = data["capture"]
    if not isinstance(capture, dict):
        raise ValueError("Capture record must be an object.")
    start = _integer(capture.get("start"), "capture.start")
    end = _integer(capture.get("end"), "capture.end")
    count = _integer(capture.get("count"), "capture.count", 1)
    if (count != end - start + 1 or count != options.source_end
            or capture.get("fps") != options.fps or capture.get("pixel_aspect") != 1.0
            or not isinstance(capture.get("view"), str) or not capture["view"]):
        raise ValueError("Capture range/FPS/PAR/frame map mismatch.")
    mapping = {"start": start, "end": end, "count": count, "first": 1, "last": count,
               "expression": f"frame - ({start}) + 1"}
    if "frame_map" in data and data["frame_map"] != mapping:
        raise ValueError("Integration frame map mismatch.")
    if type(result.get("frame_count")) is not int or result["frame_count"] != count:
        raise ValueError("Worker result frame count mismatch.")
    source = remap(options.input_path)
    source_names = [f"capture.{index:04d}.exr" for index in range(1, count + 1)]
    if {path.name for path in source.iterdir()} != set(source_names):
        raise ValueError("Capture snapshot filenames/count mismatch.")
    records = capture.get("files")
    if not isinstance(records, list) or len(records) != count:
        raise ValueError("Capture snapshot records/count mismatch.")
    geometry = capture.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("Capture geometry is required.")
    if (capture.get("width"), capture.get("height")) != (geometry.get("width"), geometry.get("height")):
        raise ValueError("Capture dimensions mismatch geometry.")
    if data["settings"].get("source_color") != options.source_color:
        raise ValueError("Capture colour does not match worker settings.")
    if ("output_channels" in data["settings"]
            and data["settings"]["output_channels"] != options.output_channels):
        raise ValueError("Integration output channel mode does not match worker settings.")
    if (geometry.get("x"), geometry.get("y"), geometry.get("width"), geometry.get("height")) != (
            geometry.get("full_x"), geometry.get("full_y"), geometry.get("full_width"), geometry.get("full_height")):
        raise ValueError("Capture fixed data/display windows mismatch.")
    for index, (name, recorded) in enumerate(zip(source_names, records), 1):
        path = source / name
        actual = _record(path, root, expected_geometry=geometry, precision="float")
        if not isinstance(recorded, dict) or any(recorded.get(key) != actual[key]
                                               for key in ("path", "size", "sha256", "geometry", "precision")):
            raise ValueError(f"Capture snapshot identity mismatch: {path}")
        if progress:
            progress("validate-capture", index, count, name)
    if remap(result.get("first_source")) != source / source_names[0]:
        raise ValueError("Worker first source does not match capture.")
    backend_path = remap(result.get("manifest"))
    for name in ("source_preview", "matte_preview"):
        preview = remap(result.get(name))
        if preview.parent != remap(result.get("job_dir")):
            raise ValueError(f"Worker {name} is outside its owned backend job directory.")
    if data.get("backend_manifest") and _owned(root, data["backend_manifest"]) != backend_path:
        raise ValueError("Backend manifest identity mismatch.")
    backend = _read_json(backend_path)
    expected_channels = ["A"] if options.output_channels == "alpha" else ["R", "G", "B"]
    if backend.get("output_channels", ["R", "G", "B"]) != expected_channels:
        raise ValueError("Backend output channel mode does not match immutable worker settings.")
    output = remap(result.get("output_dir"))
    if (backend.get("status") != "complete" or backend.get("frame_count") != count
            or remap(backend.get("output_dir")) != output
            or remap(backend.get("job_dir")) != remap(result.get("job_dir"))
            or backend_path.parent != remap(result.get("job_dir"))):
        raise ValueError("Backend manifest status/count/owned paths mismatch.")
    if backend.get("selected_count") != count or backend.get("source_numbers") != list(range(1, count + 1)):
        raise ValueError("Backend selected capture frame map mismatch.")
    if backend.get("first_frame") != 1 or backend.get("source_windows") != [geometry] * count:
        raise ValueError("Backend frame mapping/window record mismatch.")
    spatial = spatial_plan(geometry["width"], geometry["height"], options.generation_width, options.generation_height)
    chunks = temporal_chunks(count, options.fps, options.temporal_mode)
    if (backend.get("spatial") != spatial.to_dict()
            or backend.get("temporal_chunks") != [chunk.to_dict() for chunk in chunks]):
        raise ValueError("Backend spatial/temporal plan does not match worker settings.")
    for name, value in (("fps", options.fps), ("source_color", options.source_color),
                        ("frame_skip", 0), ("temporal_mode", options.temporal_mode)):
        if backend.get(name) != value:
            raise ValueError(f"Backend {name} does not match immutable worker settings.")
    selected = backend.get("selected_files")
    if not isinstance(selected, list) or [remap(path) for path in selected] != [source / name for name in source_names]:
        raise ValueError("Backend source snapshot paths mismatch.")
    names = [frame_filename(output, index, options.padding) for index in range(1, count + 1)]
    if backend.get("frames") != names or {path.name for path in output.iterdir()} != set(names):
        raise ValueError("Output filenames/count mismatch.")
    if remap(result.get("first_output")) != output / names[0]:
        raise ValueError("Worker first output mismatch.")
    saved = data["outputs"]
    if not isinstance(saved, list) or (saved and len(saved) != count) or (data["state"] == "complete" and not saved):
        raise ValueError("Output identity records/count mismatch.")
    if any(not isinstance(record, dict) for record in saved):
        raise ValueError("Output identity records must be objects.")
    outputs = []
    for index, name in enumerate(names, 1):
        actual = _record(output / name, root, expected_geometry=geometry, precision=options.precision,
                         grayscale=True, channels=options.output_channels)
        if saved and any(saved[index - 1].get(key) != actual[key]
                         for key in ("path", "size", "sha256", "geometry", "precision")):
            raise ValueError(f"Output checksum/identity mismatch: {name}")
        outputs.append(actual)
        if progress:
            progress("validate-output", index, count, name)
    return {"valid": True, "count": count, "outputs": outputs, "geometry": geometry,
            "output_channels": expected_channels,
            "output_dir": _relative(root, output), "backend_manifest": _relative(root, backend_path),
            "source_snapshot_verified": True, "freshness": "not assessed by backend validation"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    for operation, option in (("probe", "request"), ("preflight", "job"), ("validate", "attempt")):
        subparsers.add_parser(operation).add_argument(f"--{option}", required=True, type=Path)
    args = parser.parse_args(argv)
    stream = sys.stdout

    def emit(payload):
        print(json.dumps(payload, ensure_ascii=False, allow_nan=False), file=stream, flush=True)

    def progress(stage, current, total, message):
        emit({"type": "progress", "stage": stage, "current": current, "total": total, "message": message})

    try:
        # Third-party diagnostics must never contaminate the protocol stream.
        with contextlib.redirect_stdout(sys.stderr):
            if args.operation == "probe":
                report = probe(args.request)
                if not report["ready"]:
                    emit({"type": "error", "message": "; ".join(report["errors"]), "report": report})
                    return 1
                terminal = {"type": "probe", "protocol_version": 1, "report": report}
            elif args.operation == "preflight":
                terminal = {"type": "plan", "protocol_version": 1, **preflight(args.job, progress)}
            else:
                terminal = {"type": "validation", "protocol_version": 1, "report": validate(args.attempt, progress)}
        emit(terminal)
        return 0
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
