"""Assemble portable app files around an installed Windows runtime."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[2]
PYTHON_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"


def app_metadata(root: Path) -> dict:
    # Load our trusted, stdlib-only hashing helper without importing Qt or CUDA.
    spec = importlib.util.spec_from_file_location("shotbox_build_info", PROJECT / "shotbox_matte/build_info.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build_metadata(root)


def prepare_python(runtime: Path, archive: Path | None = None) -> None:
    """Vendor official embedded Python, verifying its pinned archive checksum."""
    runtime.mkdir(parents=True, exist_ok=True)
    if not (runtime / "python.exe").is_file():
        if archive is None:
            archive = Path(tempfile.gettempdir()) / "shotbox-python-3.12.10-embed-amd64.zip"
            if not archive.is_file():
                print("Downloading the official Windows embedded Python runtime...", flush=True)
                urllib.request.urlretrieve(PYTHON_URL, archive)
        expected = (PROJECT / "packaging/windows/python-embed.sha256").read_text().split()[0]
        if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
            raise ValueError("The embedded Python archive checksum does not match")
        with zipfile.ZipFile(archive) as source:
            source.extractall(runtime)
    (runtime / "python312._pth").write_text("python312.zip\n.\nLib/site-packages\n..\nimport site\n")


def assemble(destination: Path, *, copy_models: bool = True, launcher: Path | None = None,
             python_archive: Path | None = None) -> None:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if launcher is not None and launcher.resolve() != destination / "ShotBoxMatte.exe":
        shutil.copy2(launcher, destination / "ShotBoxMatte.exe")
    if not (destination / "ShotBoxMatte.exe").is_file():
        raise RuntimeError("Build the launcher first with packaging/windows/build_launcher.py")
    for name in ("windows_bootstrap.py", "models.local.json"):
        shutil.copy2(PROJECT / name, destination / name)
    package = destination / "shotbox_matte"
    if package.exists():
        for cache in package.rglob("__pycache__"):
            shutil.rmtree(cache)
        for old in package.glob("*.py"):
            if not (PROJECT / "shotbox_matte" / old.name).is_file():
                old.unlink()
    shutil.copytree(PROJECT / "shotbox_matte", destination / "shotbox_matte", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    metadata = app_metadata(destination)
    if metadata != app_metadata(PROJECT):
        raise RuntimeError("The copied app source does not match the project; retry assembly")
    shutil.copytree(PROJECT / "third_party_licenses", destination / "third_party_licenses", dirs_exist_ok=True)
    runtime = destination / "windows_runtime"
    prepare_python(runtime, python_archive)
    if copy_models:
        print("Copying all six local models into the portable folder...", flush=True)
        manifest = json.loads((PROJECT / "models/manifest.json").read_text())
        for record in manifest["models"].values():
            source = PROJECT / "models" / record["file"]
            target = destination / "models" / record["file"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and not target.is_symlink() and target.stat().st_size == record["bytes"]:
                continue  # Full content verification is a separate explicit build check.
            partial = target.with_suffix(target.suffix + ".copying")
            if partial.is_symlink():
                partial.unlink()
            if sys.platform.startswith("linux"):
                # Independent CoW files where supported, otherwise ordinary byte copies.
                subprocess.run(["cp", "--reflink=auto", str(source), str(partial)], check=True)
            else:
                shutil.copy2(source, partial)
            partial.replace(target)
        shutil.copy2(PROJECT / "models/manifest.json", destination / "models/manifest.json")
    (destination / "CHECK_WINDOWS.bat").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\n"windows_runtime\\python.exe" -u -X utf8 windows_bootstrap.py --self-test\r\n'
        'set "SHOTBOX_RESULT=%ERRORLEVEL%"\r\necho.\r\necho Report: logs\\windows-self-test.json\r\npause\r\nexit /b %SHOTBOX_RESULT%\r\n',
        newline="",
    )
    (destination / "CLI.bat").write_text(
        '@echo off\r\ncd /d "%~dp0"\r\n"windows_runtime\\python.exe" -u -X utf8 windows_bootstrap.py %*\r\nexit /b %ERRORLEVEL%\r\n',
        newline="",
    )
    (destination / "START_HERE.txt").write_text(
        "SHOTBOX MATTE — WINDOWS PORTABLE\n\n"
        "Double-click ShotBoxMatte.exe to open the app. Keep this entire folder together.\n"
        "Python, Qt, the CUDA inference libraries and all six models are included.\n"
        "Target: Windows 10/11 x64 with a compatible NVIDIA GPU and driver.\n"
        "No model downloads or Python installation are needed.\n\n"
        "Alpha Gen selects its subject automatically from the source footage.\n\n"
        "Use the Single shot and Batch queue tabs. Add MOV/MP4/MXF files, Ctrl/Shift-select\n"
        "sequence folders, scan a parent folder or drop multiple mixed sources.\n"
        "One job is added per movie/sequence; click Start batch or Start queue to run.\n"
        "Select source In/Out frames and save named presets in the shared controls.\n"
        "Scrub/play previews; use Overlay to compare the matte against the source.\n"
        "Timing shows elapsed time, stage ETA and estimated job finish after a matching\n"
        "workflow has been measured. Diagnostics include the build and Copy Log/Open Logs.\n\n"
        "Output folders contain matte EXRs. Job manifests and previews are kept in logs/jobs/.\n\n"
        "Third-party notices: third_party_licenses/ and each runtime package's license files.\n\n"
        "Settings: settings.ini beside the executable. Startup log: logs/windows-startup.log.\n"
        "Run CHECK_WINDOWS.bat to check imports, model paths and GPU support.\n"
        "CLI example: CLI.bat C:\\shots\\plate_v001 --frame-cap 20 --dry-run\n\n"
        "This Windows package was assembled on Linux. Actual Windows execution and\n"
        "Windows CUDA inference have not been verified on this build host.\n",
        encoding="utf-8",
    )
    report = {**metadata, "target": "Windows x64", "python": "3.12.10 embedded", "models_included": copy_models,
              "windows_execution_tested": False, "entry_point": "ShotBoxMatte.exe"}
    (destination / "portable-package.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "dist" / "ShotBoxMatte-Windows")
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--launcher", type=Path, help="Optional precompiled launcher to copy into the bundle")
    parser.add_argument("--python-archive", type=Path, help="Optional official embedded Python ZIP")
    args = parser.parse_args()
    assemble(args.output, copy_models=not args.skip_models, launcher=args.launcher, python_archive=args.python_archive)
