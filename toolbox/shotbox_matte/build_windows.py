"""Build the complete Windows x64 portable application, including local models."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ltx-source", type=Path, help="Clean checkout of the pinned LTX revision (full build)")
    parser.add_argument("--output", type=Path, default=ROOT / "dist/ShotBoxMatte-Windows")
    parser.add_argument("--zig", help="Zig compiler executable")
    parser.add_argument("--uv", default="uv", help="uv executable")
    parser.add_argument("--python-archive", type=Path, help="Previously downloaded official Windows embedded Python ZIP")
    parser.add_argument("--code-only", action="store_true", help="Refresh an existing bundle without downloading dependencies")
    parser.add_argument("--full-model-hashes", action="store_true", help="Also recheck all model bytes on a code-only refresh")
    args = parser.parse_args()
    output = args.output.resolve()
    scripts = ROOT / "packaging/windows"
    if not args.code_only and args.ltx_source is None:
        parser.error("--ltx-source is required for a full build; use --code-only to refresh an existing bundle")
    if args.code_only and not all((output / name).is_file() for name in (
            "windows_runtime/python.exe", "windows_runtime/Lib/site-packages/torch/__init__.py", "models/manifest.json")):
        parser.error("--code-only requires an existing complete portable folder with runtime and local models")

    def run(script: str, *options: str) -> None:
        subprocess.run([sys.executable, str(scripts / script), *options], check=True)

    launcher_args = ["--output", str(output / "ShotBoxMatte.exe")]
    if args.zig:
        launcher_args.extend(["--zig", args.zig])
    run("build_launcher.py", *launcher_args)
    assembly_args = ["--output", str(output)]
    if args.python_archive:
        assembly_args.extend(["--python-archive", str(args.python_archive.resolve())])
    run("assemble_bundle.py", *assembly_args)
    if not args.code_only:
        run("prepare_dependencies.py", "--ltx-source", str(args.ltx_source.resolve()),
            "--wheelhouse", str(ROOT / "build/windows/wheels"),
            "--cache-dir", str(ROOT / "build/windows/uv-cache"), "--uv", args.uv,
            "--install-target", str(output / "windows_runtime/Lib/site-packages"))
    validation_args = ["--full-model-hashes"] if not args.code_only or args.full_model_hashes else []
    run("validate_bundle.py", str(output), *validation_args)
    run("export_update.py", "--bundle", str(output),
        "--output", str(output.parent / "ShotBoxMatte-Windows-update.zip"))
    print(f"Windows portable application ready: {output / 'ShotBoxMatte.exe'}")
    print("Actual Windows launch and GPU execution must be tested on Windows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
