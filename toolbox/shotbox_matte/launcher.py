"""Create or reuse a project-local environment, then open ShotBox Matte."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


SETUP_NEEDED = 75
PROBE = r'''
import importlib
from importlib.metadata import version
import json
import sys

errors = []
python_ok = (3, 12) <= sys.version_info[:2] < (3, 14)
if not python_ok:
    errors.append("Python 3.12 or 3.13 is required.")
else:
    for module in ("numpy", "PIL.Image", "OpenImageIO", "av", "PyQt6.QtWidgets",
                   "torch", "torchvision", "torchaudio", "transformers", "ltx_pipelines.ic_lora"):
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"{module}: {exc}")
    for name, expected in {"torch": "2.11.0", "torchvision": "0.26.0", "torchaudio": "2.11.0",
                           "transformers": "5.14.1", "ltx-core": "1.5.0", "ltx-pipelines": "1.5.0"}.items():
        try:
            if version(name).split("+")[0] != expected:
                errors.append(f"{name} needs version {expected}.")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    torch = sys.modules.get("torch")
    if torch is not None and getattr(torch.version, "cuda", None) != "13.0":
        errors.append("The CUDA 13.0 PyTorch build is required.")
print(json.dumps({"python_ok": python_ok, "errors": errors}))
'''


def environment_path(project: Path, *, windows: bool | None = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    names = (".venv-windows", ".venv") if windows else (".venv", ".venv-linux")
    for name in names:
        if environment_python(project / name, windows=windows).is_file():
            return project / name
    # Keep a copied environment for another operating system intact.
    if not windows and (project / ".venv").exists():
        return project / ".venv-linux"
    return project / names[0]


def environment_python(environment: Path, *, windows: bool | None = None) -> Path:
    windows = os.name == "nt" if windows is None else windows
    return environment / ("Scripts/python.exe" if windows else "bin/python")


def probe(python: Path, project: Path) -> dict:
    try:
        result = subprocess.run([str(python), "-c", PROBE], cwd=project,
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            # Optional native libraries can print notices before our JSON report.
            return json.loads(result.stdout.strip().splitlines()[-1])
        return {"python_ok": False, "errors": [result.stderr.strip() or "Python could not start."]}
    except (OSError, ValueError, IndexError) as exc:
        return {"python_ok": False, "errors": [str(exc)]}


def runtime_environment(project: Path) -> dict[str, str]:
    environment = dict(os.environ)
    tools = project / ".launcher"
    environment.update(UV_NO_CONFIG="1", UV_PYTHON_INSTALL_DIR=str(tools / "python"),
                       UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(tools / "cache")), PYTHONUTF8="1",
                       SHOTBOX_PORTABLE_DIR=str(project), SHOTBOX_JOB_DIR=str(project / "logs" / "jobs"))
    return environment


def setup(project: Path, environment: Path, uv: Path, report: dict) -> Path:
    python = environment_python(environment)
    child_environment = runtime_environment(project)

    def run(arguments: list[str]) -> None:
        subprocess.run([str(uv), "--no-config", *arguments], cwd=project,
                       env=child_environment, check=True)

    if not report["python_ok"]:
        print(f"Creating local environment: {environment}", flush=True)
        run(["venv", "--allow-existing", "--python", sys.executable, str(environment)])
    print("Installing ShotBox's desktop and CUDA inference dependencies. First setup can take a while.", flush=True)
    run(["pip", "install", "--python", str(python), "--torch-backend", "cu130",
         "--requirements", str(project / "launcher-requirements.txt"),
         "--editable", str(project) + "[desktop]"])
    final = probe(python, project)
    if final["errors"]:
        raise RuntimeError("Environment check failed after setup:\n" + "\n".join(final["errors"]))
    return python


def main(argv: list[str] | None = None, *, project: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="optional sequence folder/frame or MOV/MP4/MXF to open")
    parser.add_argument("--setup-only", action="store_true", help="prepare/check the environment without opening the UI")
    parser.add_argument("--uv", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--no-setup", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    project = (project or Path(__file__).resolve().parent).resolve()
    source = str(Path(args.input).expanduser().resolve()) if args.input else None
    environment = environment_path(project)
    python = environment_python(environment)
    report = probe(python, project)
    if report["errors"]:
        if args.no_setup:
            return SETUP_NEEDED
        if args.uv is None or not args.uv.is_file():
            print("Run launch.bat on Windows or ./launch.sh on Linux to install the local environment.", file=sys.stderr)
            return 1
        try:
            python = setup(project, environment, args.uv.resolve(), report)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            print(f"ShotBox setup failed: {exc}\nCheck the internet connection and available disk space, then run the launcher again.", file=sys.stderr)
            return 1
    else:
        print(f"Using existing environment: {environment}", flush=True)
    if args.setup_only:
        print("ShotBox environment is ready.", flush=True)
        return 0
    command = [str(python), str(project / "launch_ui.py")]
    if source:
        command.append(source)
    return subprocess.call(command, cwd=project, env=runtime_environment(project))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
