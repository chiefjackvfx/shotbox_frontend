"""Portable Windows entry point, run by ShotBoxMatte.exe's private Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import traceback

def configure_runtime(root: Path) -> None:
    """Keep imports, DLLs, settings and weights rooted in the portable folder."""
    root = root.resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["SHOTBOX_PORTABLE_DIR"] = str(root)
    os.environ["PYTHONUTF8"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    runtime = root / "windows_runtime"
    packages = runtime / "Lib" / "site-packages"
    plugins = packages / "PyQt6" / "Qt6" / "plugins"
    os.environ["QT_PLUGIN_PATH"] = str(plugins)
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(plugins / "platforms")
    if os.name == "nt":
        from shotbox_matte._windows import configure_dlls

        configure_dlls(root)


def self_test(root: Path, *, require_cuda: bool = True) -> int:
    """Report actual Windows import/GPU/model readiness without loading weights."""
    import importlib
    import platform

    from shotbox_matte.build_info import build_metadata
    from shotbox_matte.models import discover_models, validate_models

    report = {**build_metadata(root), "platform": platform.platform(), "python": sys.version, "imports": {}, "errors": []}
    for module in ("numpy", "PIL.Image", "OpenImageIO", "av", "PyQt6.QtWidgets", "torch",
                   "torchvision", "torchaudio", "transformers", "ltx_pipelines.ic_lora"):
        try:
            imported = importlib.import_module(module)
            report["imports"][module] = str(getattr(imported, "__version__", "imported"))
        except Exception as exc:
            report["errors"].append(f"{module}: {exc}")
    config = discover_models(root / "models")
    report["models"] = config.to_dict()
    report["errors"].extend(validate_models(config))
    if "torch" in sys.modules:
        torch = sys.modules["torch"]
        try:
            available = torch.cuda.is_available()
            report["cuda"] = {"available": available, "runtime": torch.version.cuda}
            if available:
                report["cuda"].update(device=torch.cuda.get_device_name(), bf16=torch.cuda.is_bf16_supported())
                if require_cuda and not report["cuda"]["bf16"]:
                    report["errors"].append("The GPU does not support the released BF16 model pack.")
            elif require_cuda:
                report["errors"].append("CUDA is unavailable; a compatible NVIDIA GPU and driver are required.")
        except Exception as exc:
            report["errors"].append(f"CUDA capability check: {exc}")
    report["ok"] = not report["errors"]
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    text = json.dumps(report, indent=2, default=str)
    (logs / "windows-self-test.json").write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent
    configure_runtime(root)
    if args and args[0] == "--self-test":
        return self_test(root, require_cuda="--no-cuda-check" not in args)
    if not args or (len(args) == 1 and not args[0].startswith("-")) or args[0] == "--gui":
        from shotbox_matte.gui import main as gui_main

        gui_args = args[1:] if args[0:1] == ["--gui"] else args
        input_path = gui_args[0] if gui_args else None
        return int(gui_main(input_path) or 0)
    from shotbox_matte.cli import main as cli_main

    return int(cli_main(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
