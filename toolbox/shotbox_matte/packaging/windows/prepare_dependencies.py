"""Build pinned pure LTX wheels and resolve the Windows x64 runtime from Linux.

This prepares dependency artifacts only. It neither downloads model weights nor
copies any extension modules from the build host's Python environment.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shlex
import subprocess
import sys
import zipfile


LTX_REVISION = "e98f6d927d83c92ef2be6aec76c8440b19306e39"
PLATFORM = "x86_64-pc-windows-msvc"
PYTHON_VERSION = "3.12"
HERE = Path(__file__).resolve().parent


def run(command: list[str]) -> None:
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True)


def validate_lock(path: Path) -> None:
    """Fail if a host-platform CUDA package slipped into Windows resolution."""
    names = set(re.findall(r"^([a-z0-9][a-z0-9-]*)==", path.read_text(encoding="utf-8"), re.MULTILINE))
    forbidden = {name for name in names if name.startswith(("nvidia-", "cuda-")) or name in {"triton", "mps-sdpa", "natten"}}
    if forbidden:
        raise RuntimeError(f"Non-Windows packages found in runtime lock: {', '.join(sorted(forbidden))}")
    for required in ("torch", "torchvision", "torchaudio", "pyqt6", "openimageio", "av", "numpy", "ltx-core", "ltx-pipelines"):
        if required not in names:
            raise RuntimeError(f"Required runtime package absent from lock: {required}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ltx-source", required=True, type=Path, help="Git checkout of the pinned official LTX-2 source")
    parser.add_argument("--wheelhouse", type=Path, default=HERE / "wheels")
    parser.add_argument("--cache-dir", type=Path, default=HERE / ".uv-cache")
    parser.add_argument("--requirements", type=Path, default=HERE / "requirements.txt")
    parser.add_argument("--install-target", type=Path, help="Optionally install Windows wheels into a new portable runtime site-packages folder")
    parser.add_argument("--uv", default="uv", help="uv executable")
    args = parser.parse_args(argv)
    source, wheelhouse, cache = args.ltx_source.resolve(), args.wheelhouse.resolve(), args.cache_dir.resolve()
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != LTX_REVISION:
        raise RuntimeError(f"LTX checkout must be {LTX_REVISION}; found {revision}")
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip():
        raise RuntimeError("The pinned LTX checkout has local changes; use an unmodified source checkout.")
    wheelhouse.mkdir(parents=True, exist_ok=True)
    for package in ("ltx-core", "ltx-pipelines"):
        run([args.uv, "build", "--wheel", "--cache-dir", str(cache), "--out-dir", str(wheelhouse), str(source / "packages" / package)])
        wheel = wheelhouse / (package.replace("-", "_") + "-1.5.0-py3-none-any.whl")
        with zipfile.ZipFile(wheel) as archive:
            metadata = archive.read(package.replace("-", "_") + "-1.5.0.dist-info/WHEEL").decode()
            if "Root-Is-Purelib: true" not in metadata or "Tag: py3-none-any" not in metadata:
                raise RuntimeError(f"Expected a portable pure Python LTX wheel: {wheel}")
    common = ["--python-platform", PLATFORM, "--python-version", PYTHON_VERSION,
              "--only-binary", ":all:", "--torch-backend", "cu130",
              "--find-links", str(wheelhouse), "--cache-dir", str(cache)]
    run([args.uv, "pip", "compile", "--quiet", str(HERE / "requirements.in"), *common,
         "--generate-hashes", "--emit-index-annotation", "--output-file", str(args.requirements)])
    validate_lock(args.requirements)
    if args.install_target:
        run([args.uv, "pip", "install", "--target", str(args.install_target.resolve()),
             *common, "--require-hashes", "-r", str(args.requirements.resolve())])
    else:
        command = [args.uv, "pip", "install", "--target", "<windows_runtime/Lib/site-packages>",
                   *common, "--require-hashes", "-r", str(args.requirements.resolve())]
        print("\nInstall into the portable runtime with:\n" + shlex.join(command))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Windows dependency preparation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
