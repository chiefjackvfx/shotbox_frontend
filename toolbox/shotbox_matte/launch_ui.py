#!/usr/bin/env python3
"""Open ShotBox Matte, automatically using this project's virtual environment."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch the ShotBox Matte desktop UI.")
    parser.add_argument("input", nargs="?", help="optional image-sequence folder/frame or MOV/MP4/MXF file to open")
    args = parser.parse_args()
    input_path = str(Path(args.input).expanduser().resolve()) if args.input else None

    script = Path(__file__).resolve()
    project = script.parent
    names = (".venv-windows", ".venv") if os.name == "nt" else (".venv", ".venv-linux")
    environments = [project / name for name in names]
    active = Path(sys.prefix).resolve()
    python = next((folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                   for folder in environments
                   if (folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).is_file()), None)
    if python is not None and active not in [folder.resolve() for folder in environments]:
        command = [str(python), str(script)]
        if input_path:
            command.append(input_path)
        return subprocess.call(command, cwd=project)

    # Keep desktop worker launches working when this script is run elsewhere.
    os.chdir(project)
    from shotbox_matte.gui import main as launch

    return launch(input_path)


if __name__ == "__main__":
    raise SystemExit(main())
