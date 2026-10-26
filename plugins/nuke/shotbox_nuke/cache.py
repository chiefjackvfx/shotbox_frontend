"""Owned immutable cache paths and integrity checks without inference imports."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import tempfile
import uuid

from .protocol import ProtocolError, validate_manifest


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_owned(attempt_root: str | Path, relative: str) -> Path:
    """Reject traversal, absolute/drive/UNC paths and symlink escapes."""
    if not isinstance(relative, str) or not relative or "\\" in relative or "\x00" in relative:
        raise ProtocolError("Owned path must be a nonempty relative POSIX path.")
    posix, windows = PurePosixPath(relative), PureWindowsPath(relative)
    if posix.is_absolute() or windows.drive or windows.root or ".." in posix.parts or relative == ".":
        raise ProtocolError(f"Owned path escapes the attempt: {relative!r}")
    root = Path(attempt_root).resolve()
    resolved = (root / relative).resolve()
    if resolved == root or root not in resolved.parents:
        raise ProtocolError(f"Owned path escapes the attempt: {relative!r}")
    return resolved


def file_record(path: str | Path, root: str | Path, **details) -> dict:
    path, root = Path(path).resolve(), Path(root).resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ProtocolError("Cannot record a file outside its owned attempt.") from exc
    resolve_owned(root, relative)
    if not path.is_file():
        raise ProtocolError(f"Cache file is missing: {path}")
    return {**details, "path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)}


def atomic_write_json(path: str | Path, value: dict) -> None:
    """Commit a flushed temporary sibling; an interrupted update keeps old JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="." + path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        # Windows does not permit opening a directory for fsync.
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def resolve_manifest_path(path: str | Path) -> Path:
    """Resolve a legacy reference through one bounded local migration alias.

    Existing manifests always win. Alias lookup is read-only, checks the exact
    original reference, and never follows a second alias.
    """
    path = Path(path).expanduser().resolve()
    if path.is_file():
        return path
    from . import preferences
    prefs = preferences.load()
    application = Path(prefs.get("application_root") or preferences.application_root()).expanduser()
    cache_root = Path(prefs["cache_root"]).expanduser() if prefs.get("cache_root") else application / ".shotbox-cache"
    original = str(path)
    alias = cache_root / "aliases" / (hashlib.sha256(original.encode("utf-8")).hexdigest() + ".json")
    if not alias.exists():
        return path
    try:
        if alias.stat().st_size > 64 * 1024:
            raise ProtocolError("Manifest migration alias is oversized.")
        value = json.loads(alias.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProtocolError(f"Cannot read manifest migration alias {alias}: {exc}") from exc
    if (not isinstance(value, dict) or type(value.get("schema_version")) is not int
            or value["schema_version"] != 1 or value.get("original") != original):
        raise ProtocolError("Manifest migration alias does not match the original reference.")
    target = value.get("manifest")
    if not isinstance(target, str) or not target or "\x00" in target or not Path(target).is_absolute():
        raise ProtocolError("Manifest migration alias requires an absolute receipt path.")
    resolved = Path(target).resolve()
    if not resolved.is_file():
        raise ProtocolError("Manifest migration alias receipt is missing; aliases cannot be chained.")
    return resolved


def read_manifest(path: str | Path, *, require_complete: bool = False) -> dict:
    path = resolve_manifest_path(path)
    try:
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ProtocolError("Integration manifest is oversized.")
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ProtocolError(f"Cannot read integration manifest {path}: {exc}") from exc
    return validate_manifest(value, require_complete=require_complete)


def allocate_attempt(cache_root: str | Path, node_id: str, attempt_id: str | None = None) -> Path:
    attempt_id = attempt_id or str(uuid.uuid4())
    for value in (node_id, attempt_id):
        try:
            uuid.UUID(value)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ProtocolError("Node and attempt identities must be UUIDs.") from exc
    root = Path(cache_root).expanduser().resolve()
    parent = root / node_id
    parent.mkdir(parents=True, exist_ok=True)
    if parent.resolve().parent != root:
        raise ProtocolError("Node cache directory escapes the chosen cache root.")
    attempt = parent / attempt_id
    attempt.mkdir(exist_ok=False)
    (attempt / "capture_v001").mkdir()
    (attempt / "backend_jobs").mkdir()
    return attempt


def _original_relative(manifest: dict, absolute: str) -> str:
    """Map original provenance to current root without touching original paths."""
    original = manifest.get("original_attempt_root")
    if not isinstance(original, str) or not original:
        raise ProtocolError("Missing original attempt root for provenance mapping.")
    path_type = PureWindowsPath if PureWindowsPath(original).drive else PurePosixPath
    try:
        return path_type(absolute).relative_to(path_type(original)).as_posix()
    except ValueError as exc:
        raise ProtocolError("Backend result points outside its original owned attempt.") from exc


def resolve_precomp_root(manifest: dict, attempt_root: str | Path) -> Path:
    """Find final EXRs for a legacy full attempt or a compact published receipt.

    ``manifest`` must have passed ``validate_manifest`` before use. The output
    root is a read-only reference; it never authorizes removing that directory.
    """
    storage = manifest.get("storage")
    if storage is None:
        return Path(attempt_root).resolve()
    directory = storage.get("output_directory")
    if (storage.get("mode") != "published" or not isinstance(directory, str) or not Path(directory).is_absolute()
            or (os.name != "nt" and PureWindowsPath(directory).drive)):
        raise ProtocolError("Published output directory is not an absolute path on this platform.")
    root = Path(directory).resolve()
    if root == Path(root.anchor):
        raise ProtocolError("Published output directory cannot be a filesystem root.")
    return root


def _validate_published(path: Path, manifest: dict, checksums: bool) -> dict:
    """Verify retained final images without consulting deleted intermediates."""
    root = resolve_precomp_root(manifest, path.parent)
    descriptor = manifest["precomp"]
    pattern = resolve_owned(root, descriptor["pattern"])
    files = [resolve_owned(root, record["path"]) for record in descriptor["files"]]
    expected = [Path(str(pattern) % frame) for frame in range(descriptor["first"], descriptor["last"] + 1)]
    if files != expected:
        raise ProtocolError("Published precomp files do not match the recorded timeline sequence.")
    prefix, suffix = descriptor["pattern"].split("%04d")
    frame_name = re.compile(re.escape(prefix) + r"-?\d+" + re.escape(suffix) + r"\Z")
    found = {item.name for item in root.glob("*.exr") if frame_name.fullmatch(item.name)}
    if not root.is_dir() or found != {item.name for item in expected}:
        raise ProtocolError("Published precomp folder has missing or unexpected EXR frames.")
    for record, owned in zip(descriptor["files"], files):
        if not owned.is_file() or owned.stat().st_size != record["size"]:
            raise ProtocolError("Missing or wrong-size published precomp: " + record["path"])
        if checksums and sha256_file(owned) != record["sha256"]:
            raise ProtocolError("Published precomp checksum mismatch: " + record["path"])
    return {"valid": True, "manifest": manifest, "manifest_path": str(path), "attempt_root": str(path.parent),
            "precomp_root": str(root), "output_dir": str(root), "matte_output_dir": "", "outputs": [],
            "precomp_files": [str(item) for item in files], "checksums_verified": checksums,
            "freshness": "unverifiable", "source_snapshot": "validated before publication; no longer retained"}


def validate_published_manifest(manifest: dict, receipt_path: str | Path, checksums: bool = True) -> dict:
    """Check a candidate published reference without writing a receipt first."""
    validate_manifest(manifest, require_complete=True)
    if manifest.get("storage", {}).get("mode") != "published":
        raise ProtocolError("A published EXR reference is required.")
    return _validate_published(Path(receipt_path).resolve(), manifest, checksums)


def validate_cache(manifest_path: str | Path, checksums: bool = True) -> dict:
    """Validate disk identity; host adapter must additionally decode images.

    This is usable on farms with no ShotBox runtime. Recorded image geometry is
    checked here; the external bridge originally decodes/validates every image,
    and Nuke's render adapter performs its own decoding before render.
    """
    path = resolve_manifest_path(manifest_path)
    root = path.parent
    manifest = read_manifest(path, require_complete=True)
    if manifest.get("storage", {}).get("mode") == "published":
        return _validate_published(path, manifest, checksums)
    capture, result = manifest["capture"], manifest["result"]
    worker = resolve_owned(root, manifest["worker_path"])
    if not worker.is_file() or sha256_file(worker) != manifest["worker_sha256"]:
        raise ProtocolError("Worker JSON is missing or its exact digest has changed.")
    backend = resolve_owned(root, manifest["backend_manifest"])
    if _original_relative(manifest, result["manifest"]) != manifest["backend_manifest"]:
        raise ProtocolError("Backend manifest does not match the recorded worker result.")
    try:
        backend_record = json.loads(backend.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProtocolError(f"Backend manifest is missing or unreadable: {exc}") from exc
    if not isinstance(backend_record, dict) or backend_record.get("status") != "complete":
        raise ProtocolError("Backend manifest does not have complete status.")
    for key in ("output_dir", "first_source", "first_output", "source_preview", "matte_preview", "manifest", "job_dir"):
        resolve_owned(root, _original_relative(manifest, result[key]))
    output_relative = _original_relative(manifest, result["output_dir"])
    output_dir = resolve_owned(root, output_relative)
    expected_paths = [resolve_owned(root, record["path"]) for record in manifest["outputs"]]
    if any(output.parent != output_dir for output in expected_paths):
        raise ProtocolError("Output files do not match the worker's recorded output directory.")
    output_names = [output.name for output in expected_paths]
    if backend_record.get("frame_count") != capture["count"] or backend_record.get("frames") != output_names:
        raise ProtocolError("Backend manifest frame count/names do not match recorded outputs.")
    if {item.name for item in output_dir.glob("*.exr")} != set(output_names):
        raise ProtocolError("Output folder has missing or unexpected EXR frames.")
    capture_paths = [resolve_owned(root, record["path"]) for record in capture["files"]]
    capture_dir = resolve_owned(root, "capture_v001")
    expected_captures = [capture_dir / f"capture.{index:04d}.exr" for index in range(1, capture["count"] + 1)]
    if capture_paths != expected_captures or set(capture_dir.glob("*.exr")) != set(expected_captures):
        raise ProtocolError("Capture files do not have the exact positive 1..N frame mapping.")
    if (resolve_owned(root, _original_relative(manifest, result["first_source"])) != capture_paths[0]
            or resolve_owned(root, _original_relative(manifest, result["first_output"])) != expected_paths[0]
            or resolve_owned(root, _original_relative(manifest, result["job_dir"])) != backend.parent):
        raise ProtocolError("Worker result first-frame/job paths do not match recorded files.")
    for index, output in enumerate(expected_paths, 1):
        if output.name != f"{output_dir.name}.{index:04d}.exr":
            raise ProtocolError("Output filenames do not have the exact positive 1..N frame mapping.")
    for record in [*capture["files"], *manifest["outputs"]]:
        owned = resolve_owned(root, record["path"])
        if not owned.is_file() or owned.stat().st_size != record["size"]:
            raise ProtocolError(f"Missing or wrong-size cache file: {record['path']}")
        if checksums and sha256_file(owned) != record["sha256"]:
            raise ProtocolError(f"Cache checksum mismatch: {record['path']}")
        geometry = record.get("geometry", record)
        if not isinstance(geometry, dict) or any(geometry.get(key) != capture[key] for key in ("width", "height")):
            raise ProtocolError(f"Cache geometry mismatch: {record['path']}")
        if "geometry" in capture and geometry != capture["geometry"]:
            raise ProtocolError(f"Cache data/display geometry mismatch: {record['path']}")
    descriptor = manifest.get("precomp")
    precomp_files = []
    if descriptor:
        pattern = resolve_owned(root, descriptor["pattern"])
        precomp_files = [resolve_owned(root, record["path"]) for record in descriptor["files"]]
        expected = [Path(str(pattern) % frame) for frame in range(capture["start"], capture["end"] + 1)]
        if precomp_files != expected:
            raise ProtocolError("Precomp files do not match the recorded timeline sequence.")
        sources = capture.get("source_files", []) if descriptor.get("kind") == "alpha" else capture["source_files"]
        source_paths = [resolve_owned(root, record["path"]) for record in sources]
        if descriptor.get("kind") != "alpha" and source_paths != [
                resolve_owned(root, "source_v001/source.%04d.exr" % frame)
                for frame in range(1, capture["count"] + 1)]:
            raise ProtocolError("Original-channel source snapshot mapping is invalid.")
        for record in [*sources, *descriptor["files"]]:
            owned = resolve_owned(root, record["path"])
            if not owned.is_file() or owned.stat().st_size != record["size"]:
                raise ProtocolError("Missing or wrong-size precomp/source file: " + record["path"])
            if checksums and sha256_file(owned) != record["sha256"]:
                raise ProtocolError("Precomp/source checksum mismatch: " + record["path"])
    return {"valid": True, "manifest": manifest, "manifest_path": str(path), "attempt_root": str(root),
            "precomp_root": str(root),
            "output_dir": str(precomp_files[0].parent if precomp_files else output_dir),
            "matte_output_dir": str(output_dir), "outputs": [str(item) for item in expected_paths],
            "precomp_files": [str(item) for item in precomp_files],
            "checksums_verified": checksums, "freshness": "unverifiable"}


def relink_cache(manifest_path: str | Path) -> dict:
    """Return a validated moved reference; never alter either cache."""
    return validate_cache(manifest_path, checksums=True)
