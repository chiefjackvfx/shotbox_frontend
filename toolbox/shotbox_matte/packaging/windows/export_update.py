"""Export a small update ZIP for an existing complete Windows portable folder."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import zipfile

PROJECT = Path(__file__).resolve().parents[2]


def export_update(bundle: Path, destination: Path) -> dict:
    bundle = bundle.resolve()
    spec = importlib.util.spec_from_file_location("shotbox_build_info", PROJECT / "shotbox_matte/build_info.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    metadata = module.build_metadata(bundle)
    declared = json.loads((bundle / "portable-package.json").read_text(encoding="utf-8"))
    if any(declared.get(key) != value for key, value in metadata.items()):
        raise ValueError("The portable folder has changed; assemble it again before exporting an update")
    names = [*metadata["source_files"], "ShotBoxMatte.exe", "portable-package.json"]
    if (bundle / "START_HERE.txt").is_file():
        names.append("START_HERE.txt")
    missing = [name for name in names if not (bundle / name).is_file()]
    if missing:
        raise ValueError(f"The portable folder is missing update files: {', '.join(missing)}")
    report = {**metadata, "kind": "code-update", "requires_existing_portable_install": True,
              "includes_models": False, "includes_runtime": False, "files": names}
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".copying")
    try:
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in names:
                archive.write(bundle / name, name)
            archive.writestr("update-package.json", json.dumps(report, indent=2) + "\n")
            archive.writestr("UPDATE_README.txt",
                            f"ShotBox Matte {metadata['app_version']} ({metadata['build_id']})\n\n"
                            "Close ShotBox Matte, then extract this ZIP into your existing complete\n"
                            "ShotBoxMatte-Windows folder, replacing matching files. Reopen the EXE.\n"
                            "This update includes the launcher and app code. Keep your existing\n"
                            "windows_runtime/ and models/ directories. Your settings and logs remain.\n"
                            "The app header/log build ID must match the ID above.\n"
                            "Actual Windows execution has not been tested on the Linux build host.\n")
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=PROJECT / "dist/ShotBoxMatte-Windows")
    parser.add_argument("--output", type=Path, default=PROJECT / "dist/ShotBoxMatte-Windows-update.zip")
    args = parser.parse_args()
    report = export_update(args.bundle, args.output)
    print(f"Code update ready: {args.output} ({report['build_id']})")
