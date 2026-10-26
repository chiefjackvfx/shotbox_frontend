"""Lightweight build identity derived from the application files actually shipped."""
from __future__ import annotations

import hashlib
from pathlib import Path

APP_VERSION = "0.1.0"


def source_manifest(root: Path | None = None) -> dict:
    """Fingerprint app sources by portable relative names and their exact bytes."""
    root = Path(root) if root is not None else Path(__file__).resolve().parent.parent
    paths = sorted((root / "shotbox_matte").glob("*.py"))
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths)}
    digest = hashlib.sha256()
    for name, checksum in files.items():
        digest.update(name.encode("utf-8") + b"\0" + bytes.fromhex(checksum))
    return {"source_sha256": digest.hexdigest(), "source_files": files}


def build_metadata(root: Path | None = None) -> dict:
    """Return the version and full payload identity for packaging and diagnostics."""
    manifest = source_manifest(root)
    return {"app_version": APP_VERSION, "build_id": manifest["source_sha256"][:12], **manifest}


BUILD_ID = build_metadata()["build_id"]


def build_label() -> str:
    return f"{APP_VERSION} ({BUILD_ID})"
