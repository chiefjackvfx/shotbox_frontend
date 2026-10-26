"""Cross-compile the real Windows GUI launcher with Zig on Linux or Windows.

Example:
    python -m pip install ziglang==0.17.0
    python packaging/windows/build_launcher.py

The produced EXE requires the accompanying windows_runtime and bootstrap script;
it does not embed Python, CUDA libraries, application code or model weights.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile


def find_zig(explicit: str | None) -> Path:
    if explicit:
        binary = Path(explicit).expanduser()
        if binary.is_file():
            return binary.resolve()
        resolved = shutil.which(explicit)
        if resolved:
            return Path(resolved)
        raise FileNotFoundError(f"Zig compiler not found: {explicit}")
    installed = shutil.which("zig")
    if installed:
        return Path(installed)
    package = importlib.util.find_spec("ziglang")
    if package and package.origin:
        for filename in ("zig", "zig.exe"):
            binary = Path(package.origin).parent / filename
            if binary.is_file():
                return binary
    raise FileNotFoundError("Install ziglang in this Python environment, or pass --zig /path/to/zig")


def verify_pe(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 256 or data[:2] != b"MZ":
        raise RuntimeError(f"Compiler did not create a Windows executable: {path}")
    offset = struct.unpack_from("<I", data, 0x3C)[0]
    if offset + 96 > len(data) or data[offset:offset + 4] != b"PE\0\0":
        raise RuntimeError("Invalid PE header")
    machine = struct.unpack_from("<H", data, offset + 4)[0]
    optional_header = offset + 24
    magic = struct.unpack_from("<H", data, optional_header)[0]
    subsystem = struct.unpack_from("<H", data, optional_header + 68)[0]
    if (machine, magic, subsystem) != (0x8664, 0x20B, 2):
        raise RuntimeError(f"Expected AMD64 PE32+ Windows GUI executable; received {machine:#x}, {magic:#x}, {subsystem}")
    return {
        "path": str(path.resolve()),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "architecture": "AMD64",
        "format": "PE32+",
        "subsystem": "Windows GUI",
        "windows_execution_tested": False,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zig", help="Zig compiler executable (otherwise use PATH or installed ziglang)")
    parser.add_argument("--output", type=Path, default=root / "dist" / "ShotBoxMatte-Windows" / "ShotBoxMatte.exe")
    options = parser.parse_args()
    zig = find_zig(options.zig)
    output = options.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).with_name("launcher.c")
    cache = Path(tempfile.gettempdir()) / "shotbox-zig-cache"
    environment = os.environ.copy()
    environment["ZIG_GLOBAL_CACHE_DIR"] = str(cache / "global")
    environment["ZIG_LOCAL_CACHE_DIR"] = str(cache / "local")
    command = [
        str(zig), "cc", "-target", "x86_64-windows-gnu", "-std=c11",
        "-municode", "-O2", "-Wall", "-Wextra", "-Werror", "-s",
        str(source), "-o", str(output), "-luser32", "-lkernel32",
        "-Wl,--subsystem,windows", "-Wl,--dynamicbase,--nxcompat,--high-entropy-va",
    ]
    subprocess.run(command, env=environment, check=True)
    print(json.dumps(verify_pe(output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
