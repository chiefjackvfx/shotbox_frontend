"""Static portable-bundle validation on any OS; it does not execute Windows code."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys


def source_metadata(root: Path) -> dict:
    project = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("shotbox_build_info", project / "shotbox_matte/build_info.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_metadata(root)


def pe_info(path: Path) -> dict:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError(f"Not a Windows executable: {path}")
        stream.seek(0x3C)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise ValueError(f"Missing PE header: {path}")
        machine = struct.unpack("<H", stream.read(2))[0]
        stream.seek(offset + 24)
        magic = struct.unpack("<H", stream.read(2))[0]
        stream.seek(offset + 24 + 68)
        subsystem = struct.unpack("<H", stream.read(2))[0]
    return {"machine": machine, "optional_header": magic, "subsystem": subsystem}


def validate(root: Path, *, full_hashes: bool = False) -> dict:
    root = root.resolve()
    errors = []
    executables = {}
    required = ["ShotBoxMatte.exe", "windows_runtime/python.exe", "windows_runtime/python312.dll",
                "windows_bootstrap.py", "shotbox_matte/__main__.py", "models/manifest.json",
                "windows_runtime/python312._pth", "portable-package.json"]
    for name in required:
        if not (root / name).is_file():
            errors.append(f"Missing {name}")
    for name in ("ShotBoxMatte.exe", "windows_runtime/python.exe", "windows_runtime/python312.dll"):
        if (root / name).is_file():
            try:
                info = pe_info(root / name)
                executables[name] = info
                if info["machine"] != 0x8664 or info["optional_header"] != 0x20B:
                    errors.append(f"{name} is not Windows x64 PE32+")
                if name == "ShotBoxMatte.exe" and info["subsystem"] != 2:
                    errors.append("The launcher is not a Windows GUI executable")
            except (OSError, ValueError, struct.error) as exc:
                errors.append(str(exc))
    packages = root / "windows_runtime" / "Lib" / "site-packages"
    for name in ("torch/__init__.py", "PyQt6/Qt6/plugins/platforms/qwindows.dll", "ltx_core/__init__.py",
                 "ltx_pipelines/ic_lora.py", "numpy/__init__.py", "av/__init__.py"):
        if not (packages / name).is_file():
            errors.append(f"Missing Windows runtime component: {name}")
    for metadata in packages.glob("*.dist-info/WHEEL"):
        for tag in metadata.read_text(encoding="utf-8").splitlines():
            if tag.startswith("Tag:") and any(platform in tag for platform in ("linux", "macosx")):
                errors.append(f"Non-Windows wheel in runtime: {metadata.parent.name}: {tag}")
    extensions = list(packages.rglob("*.pyd"))
    for extension in extensions:
        try:
            info = pe_info(extension)
            if info["machine"] != 0x8664 or info["optional_header"] != 0x20B:
                errors.append(f"Non-x64 Windows extension: {extension.relative_to(packages)}")
        except (OSError, ValueError, struct.error) as exc:
            errors.append(str(exc))
    for extension in packages.rglob("*.so"):
        errors.append(f"Linux extension in Windows runtime: {extension.relative_to(packages)}")
    if (root / "models" / "manifest.json").is_file():
        manifest = json.loads((root / "models" / "manifest.json").read_text())
        if len(manifest["models"]) != 6:
            errors.append("The distribution must contain all six model components, including Alpha Gen LoRA")
        for role, record in manifest["models"].items():
            path = root / "models" / record["file"]
            if not path.is_file() or path.is_symlink() or path.stat().st_size != record["bytes"]:
                errors.append(f"Missing, linked or incorrectly sized model: {role}")
            elif full_hashes:
                print(f"Checking {role} SHA-256...", flush=True)
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != record["sha256"]:
                    errors.append(f"Model checksum mismatch: {role}")
    payload = source_metadata(root)
    try:
        package = json.loads((root / "portable-package.json").read_text(encoding="utf-8"))
        for key, value in payload.items():
            if package.get(key) != value:
                errors.append(f"App source differs from portable-package.json: {key}")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"Cannot read portable-package.json: {exc}")
    report = {"bundle": str(root), "ok": not errors, "executables": executables,
              **payload,
              "windows_native_extensions": len(extensions),
              "full_model_hashes": full_hashes, "windows_execution_tested": False, "errors": errors}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--full-model-hashes", action="store_true")
    args = parser.parse_args()
    report = validate(args.folder, full_hashes=args.full_model_hashes)
    print(json.dumps(report, indent=2))
    (args.folder / "bundle-validation.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
