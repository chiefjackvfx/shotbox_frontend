"""Publish final-only precomp folders and discard owned working images."""
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import uuid

from . import cache


def _remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _copy_exclusive(source, destination):
    try:
        os.link(source, destination)
    except OSError as exc:
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
        with destination.open("xb") as target:
            try:
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, target, 1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
            except Exception:
                destination.unlink(missing_ok=True)
                raise


def promote_alpha(attempt_path, manifest):
    """Describe validated worker alpha files directly; never decode or re-encode."""
    root = Path(attempt_path).resolve()
    capture = manifest["capture"]
    if manifest["settings"]["precision"] not in ("half", "float"):
        raise ValueError("Alpha output precision must be half or float.")
    outputs = manifest["outputs"]
    if len(outputs) != capture["count"] or not outputs:
        raise ValueError("Generated alpha frame count differs from the capture.")
    first = cache.resolve_owned(root, outputs[0]["path"])
    pattern = first.with_name(first.name.rsplit(".", 2)[0] + ".%04d.exr").relative_to(root).as_posix()
    records = []
    for index, record in enumerate(outputs, 1):
        path = cache.resolve_owned(root, record["path"])
        if path != cache.resolve_owned(root, pattern % index):
            raise ValueError("Generated alpha sequence must be contiguous from frame 1.")
        if path.stat().st_size != record["size"] or cache.sha256_file(path) != record["sha256"]:
            raise ValueError("Generated alpha changed before publication.")
        records.append({**deepcopy(record), "geometry": {"width": capture["width"], "height": capture["height"]},
                        "box": [0, 0, capture["width"], capture["height"]], "pixel_aspect": 1.0,
                        "channels": ["rgba.alpha"]})
    return {"kind": "alpha", "pattern": pattern, "first": capture["start"], "last": capture["end"],
            "files": records, "channels": ["rgba.alpha"], "precision": manifest["settings"]["precision"],
            "state": "complete"}


def commit(attempt_path, manifest, output_directory=None, output_pattern=None):
    """Commit verified final EXRs and a small receipt, without overwriting files."""
    root = Path(attempt_path).resolve()
    request = manifest.get("precomp_request", {})
    target = Path(output_directory or request["output_directory"]).resolve()
    pattern = output_pattern or request["pattern"]
    if target == root or root in target.parents or target in root.parents:
        raise ValueError("Precomp output and temporary job folders must be separate.")
    if Path(pattern).name != pattern or pattern.count("%04d") != 1 or not pattern.endswith(".exr"):
        raise ValueError("Final precomp pattern must be an EXR basename with one %04d token.")
    if not target.is_dir() or any(target.iterdir()):
        raise RuntimeError("The reserved precomp output folder must be empty.")
    candidate = deepcopy(manifest)
    descriptor = candidate["precomp"]
    created = []
    receipt = root / "integration.json"
    previous = receipt.read_bytes() if receipt.exists() else None
    try:
        for frame, record in zip(range(descriptor["first"], descriptor["last"] + 1), descriptor["files"]):
            source = cache.resolve_owned(root, record["path"])
            if source.stat().st_size != record["size"] or cache.sha256_file(source) != record["sha256"]:
                raise RuntimeError("Staged precomp changed before publication.")
            destination = cache.resolve_owned(target, pattern % frame)
            _copy_exclusive(source, destination)
            created.append((destination, destination.stat().st_ino))
            if destination.stat().st_size != record["size"] or cache.sha256_file(destination) != record["sha256"]:
                raise RuntimeError("Published precomp failed its final checksum.")
            record["path"] = destination.name
        descriptor["pattern"] = pattern
        candidate["storage"] = {"mode": "published", "output_directory": str(target)}
        candidate["state"] = "complete"
        candidate["updated_at"] = cache.utc_now()
        cache.atomic_write_json(root / "integration.json", candidate)
        cache.validate_cache(root / "integration.json")
    except Exception:
        for destination, inode in reversed(created):
            if destination.is_file() and destination.stat().st_ino == inode:
                destination.unlink()
        if previous is None:
            receipt.unlink(missing_ok=True)
        else:
            cache.atomic_write_json(receipt, json.loads(previous))
        raise
    manifest.clear()
    manifest.update(candidate)
    return manifest


def cleanup(attempt_path, manifest):
    """Remove only an exclusive UUID job's contents, retaining its receipt."""
    for key in ("node_id", "attempt_id"):
        uuid.UUID(manifest[key])
    supplied = Path(attempt_path)
    root = supplied.resolve()
    if (supplied.is_symlink() or root.name != manifest.get("attempt_id")
            or root.parent.name != manifest.get("node_id")
            or str(root) != str(Path(manifest["original_attempt_root"]).resolve())):
        raise ValueError("Refusing cleanup outside the owned UUID job folder.")
    if manifest.get("state") not in ("complete", "failed", "cancelled", "interrupted"):
        raise ValueError("Only a finished job can discard working files.")
    if manifest.get("state") == "complete" and not manifest.get("storage"):
        raise ValueError("A completed result needs a validated published precomp before cleanup.")
    if manifest.get("storage"):
        cache.validate_cache(root / "integration.json")
    removed = []
    for entry in root.iterdir():
        if entry.name == "integration.json":
            continue
        _remove(entry)
        removed.append(entry.name)
    manifest["working_files_removed"] = True
    cache.atomic_write_json(root / "integration.json", manifest)
    request = manifest.get("precomp_request", {})
    if manifest.get("state") != "complete" and request.get("output_directory"):
        try:
            Path(request["output_directory"]).rmdir()
        except OSError:
            pass  # Preserve any existing EXR or artist-added file.
    return removed


def owner_is_running(manifest):
    """Conservatively protect jobs owned by another live host process."""
    pid = manifest.get("work_owner_pid")
    if type(pid) is not int or pid < 1 or os.name != "posix":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def migrate_legacy(manifest_path, cache_root):
    """Clean an explicitly selected terminal legacy folder, preserving all EXRs.

    A validated external receipt and alias are committed before any deletion.
    Only known ShotBox intermediate names are removed from the legacy folder.
    """
    original = Path(manifest_path).resolve()
    if not original.is_file():
        resolved = cache.resolve_manifest_path(original)
        if resolved != original:
            return {"receipt": str(resolved), "already_clean": True}
    data = cache.read_manifest(original)
    if data["state"] not in ("complete", "failed", "cancelled", "interrupted"):
        raise ValueError("Cannot clean a legacy attempt that may still be running.")
    root = original.parent
    if str(Path(data["original_attempt_root"]).resolve()) != str(root):
        raise ValueError("Legacy folder does not match the original owned attempt.")
    before = {p.name: cache.sha256_file(p) for p in root.glob("*.exr")}
    candidate = deepcopy(data)
    if data["state"] == "complete":
        if not data.get("precomp"):
            raise ValueError("Alpha-only legacy results still need their working files.")
        candidate["storage"] = {"mode": "published", "output_directory": str(root)}
    receipt = Path(cache_root).resolve() / "receipts" / data["node_id"] / data["attempt_id"] / "integration.json"
    if root == receipt.parent or root in receipt.parents:
        raise ValueError("Migration receipts must be outside the legacy output folder.")
    if receipt.exists() and cache.read_manifest(receipt).get("original_attempt_root") != data["original_attempt_root"]:
        raise ValueError("An unrelated receipt already occupies the migration destination.")
    cache.atomic_write_json(receipt, candidate)
    if data["state"] == "complete":
        cache.validate_cache(receipt)
    alias = Path(cache_root).resolve() / "aliases" / (hashlib.sha256(str(original).encode("utf-8")).hexdigest() + ".json")
    cache.atomic_write_json(alias, {"schema_version": 1, "original": str(original), "manifest": str(receipt)})
    known = {"capture_v001", "source_v001", "backend_jobs", "runtime.json", "events.jsonl",
             "stderr.log", "worker.json", "worker.json.cancel"}
    pattern = re.compile(r"(?:capture_matte_v\d{3,}|\.capture_matte_v\d{3,}\.shotbox-lock|"
                         r"\.shotbox-partial-capture_matte_v\d{3,}-[0-9a-f]{32})\Z")
    original.unlink()  # Readers now follow the verified alias to the receipt.
    removed = ["integration.json"]
    for entry in root.iterdir():
        if entry.name in known or pattern.fullmatch(entry.name):
            _remove(entry)
            removed.append(entry.name)
    after = {p.name: cache.sha256_file(p) for p in root.glob("*.exr")}
    if before != after:
        raise RuntimeError("Legacy EXR identities changed during cleanup.")
    candidate["working_files_removed"] = True
    cache.atomic_write_json(receipt, candidate)
    remaining = sorted(p.name for p in root.iterdir())
    if not remaining:
        root.rmdir()
    return {"folder": str(root), "receipt": str(receipt), "removed": removed,
            "remaining": remaining, "exrs_unchanged": before == after, "exr_sha256": after}
