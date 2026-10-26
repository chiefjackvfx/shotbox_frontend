"""Internal command entry point for Shotbox workers and Nuke integration."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import signal
import sys
import threading
from typing import Any, Sequence

from .models import DEFAULT_MODEL_ROOT


def resolution(value: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in value.lower().split("x"))
        if width <= 0 or height <= 0:
            raise ValueError
        return width, height
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a positive WIDTHxHEIGHT, for example 960x544.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shotbox-matte",
        description="Generate RGB grayscale matte EXRs from image sequences or MOV/MP4/MXF using native LTX-2.5 Alpha Gen.",
        epilog=("Weights are read from local paths; no weights are downloaded. "
                "Use --dry-run to inspect sequence selection, padding and provisional output naming. "
                "Frame skip is the number of frames omitted between selected frames."),
    )
    parser.add_argument("input", nargs="?", help="image-sequence folder, sequence frame, or MOV/MP4/MXF file")
    parser.add_argument("--input", dest="input_option", metavar="PATH", help="alternative to positional input")
    parser.add_argument("--sequence", help="sequence ID reported by --list-sequences")
    parser.add_argument("--list-sequences", action="store_true", help="list detected image sequences and MOV/MP4/MXF movies, then exit")
    parser.add_argument("--source-color", choices=("auto", "linear-srgb", "srgb", "acescg"), default="auto",
                        help="source encoding; linear sources are converted for model input (default: %(default)s)")
    parser.add_argument("--fps", type=float, default=24.0, help="model frame rate (default: %(default)s)")
    parser.add_argument("--frame-skip", type=int, default=0, help="omit N frames between selected frames (default: 0)")
    parser.add_argument("--frame-cap", type=int, default=0, help="maximum selected frames; 0 means all, with no shot-length limit")
    parser.add_argument("--temporal-mode", choices=("segmented", "full-range"), default="segmented",
                        help="segmented (default) or experimental full-range single pass beyond model limits")
    parser.add_argument("--source-in", type=int, help="inclusive source start: filename frame number, or 1-based position for movies/stills")
    parser.add_argument("--source-out", type=int, help="inclusive source end; range is applied before frame skip and cap")
    parser.add_argument("--resolution", type=resolution, metavar="WIDTHxHEIGHT", help="fit inside this box, preserving aspect ratio")
    parser.add_argument("--generation-width", type=int, default=960, help="generation fit box width (default: 960)")
    parser.add_argument("--generation-height", type=int, default=544, help="generation fit box height (default: 544)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--suffix", default="_roto", help="task suffix inserted before _vNNN (default: %(default)s)")
    parser.add_argument("--first-frame", type=int, help="first output number; inferred from selected source, otherwise 1001")
    parser.add_argument("--padding", type=int, default=4, help="minimum output frame number digits (default: 4)")
    parser.add_argument("--precision", choices=("half", "float"), default="half", help="EXR precision (default: half)")
    parser.add_argument("--existing", choices=("version-up", "overwrite"), default="version-up")
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT,
                        help="native split-pack folder (default: this application's models folder)")
    parser.add_argument("--models-config", type=Path, help="JSON object of component paths; paths relative to this file are supported")
    for key in ("transformer", "text-encoder", "video-vae", "audio-vae", "spatial-upsampler", "alpha-lora"):
        parser.add_argument(f"--{key}", type=Path, metavar="PATH", help=f"override local {key} component")
    parser.add_argument("--offload", choices=("none", "cpu", "disk"), default="cpu",
                        help="native model offload policy (default: cpu)")
    parser.add_argument("--low-vram", action="store_true",
                        help="tile reference VAE encoding; matte decoding uses native automatic tiling")
    parser.add_argument("--validate-models", action="store_true", help="check local model files and exit without inference")
    parser.add_argument("--dry-run", action="store_true", help="report planned processing without reserving outputs or loading models")
    parser.add_argument("--json-progress", action="store_true", help="emit newline-delimited JSON events on stdout")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--cancel-file", type=Path, help=argparse.SUPPRESS)
    return parser


class Reporter:
    def __init__(self, machine: bool = False):
        self.machine = machine
        self.stream = sys.stdout

    def event(self, kind: str, **payload: Any) -> None:
        if self.machine:
            print(json.dumps({"type": kind, **payload}, default=str, allow_nan=False), file=self.stream, flush=True)
        elif kind == "progress":
            total = payload.get("total", 0)
            count = f" {payload.get('current', 0)}/{total}" if total else ""
            print(f"[{payload.get('stage', 'processing')}{count}] {payload.get('message', '')}", file=self.stream, flush=True)
        elif kind in ("error", "cancelled"):
            print(f"{kind.capitalize()}: {payload.get('message', '')}", file=sys.stderr, flush=True)
        elif kind == "result":
            result = payload["result"]
            print(f"Exported {result.get('frame_count', '?')} frames to {result['output_dir']}", file=self.stream, flush=True)
        else:
            data = payload.get("plan", payload.get("models", payload.get("sequences", payload)))
            print(json.dumps(data, indent=2, default=str), file=self.stream, flush=True)

    def progress(self, stage: str, current: int, total: int, message: str) -> None:
        self.event("progress", stage=stage, current=current, total=total, message=message)


def load_models(args: argparse.Namespace) -> dict[str, Any]:
    from .models import discover_models

    found = discover_models(args.model_root).to_dict()
    if args.models_config:
        config_path = args.models_config.expanduser().resolve()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Model configuration must be a JSON object of component paths.")
        if "models" in data:
            data = data["models"]
        if not isinstance(data, dict):
            raise ValueError("The models entry must be an object.")
        for key, value in data.items():
            if isinstance(value, str) and value:
                path = Path(os.path.expandvars(value)).expanduser()
                data[key] = str(path if path.is_absolute() else config_path.parent / path)
        found.update(data)
    for key in ("transformer", "text_encoder", "video_vae", "audio_vae", "spatial_upsampler", "alpha_lora"):
        value = getattr(args, key)
        if value is not None:
            found[key] = str(value.expanduser().resolve())
    return found


def options_from_args(args: argparse.Namespace):
    from .core import JobOptions

    width, height = args.resolution or (args.generation_width, args.generation_height)
    return JobOptions(
        input_path=args.input_option or args.input,
        sequence=args.sequence,
        source_color=args.source_color,
        fps=args.fps,
        frame_skip=args.frame_skip,
        frame_cap=args.frame_cap,
        temporal_mode=args.temporal_mode,
        source_start=args.source_in,
        source_end=args.source_out,
        generation_width=width,
        generation_height=height,
        seed=args.seed,
        suffix=args.suffix,
        first_frame=args.first_frame,
        padding=args.padding,
        precision=args.precision,
        existing=args.existing,
        models=load_models(args),
        offload=args.offload,
        low_vram=args.low_vram,
    )


def execute(options: Any, reporter: Reporter, cancel_file: Path | None = None) -> int:
    from .core import CancelledError, run_job

    cancelled = threading.Event()
    old_handlers: dict[int, Any] = {}

    def handle_cancel(_signum: int, _frame: Any) -> None:
        cancelled.set()

    def is_cancelled() -> bool:
        return cancelled.is_set() or bool(cancel_file and cancel_file.exists())

    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.signal(signum, handle_cancel)
    try:
        # Third-party model diagnostics must not corrupt the JSON event stream.
        with contextlib.redirect_stdout(sys.stderr):
            result = run_job(options, progress=reporter.progress, cancelled=is_cancelled)
        # The backend checks cancellation before its commit point. A late signal
        # after a successful commit must still report the completed result.
        reporter.event("result", result=result)
        return 0
    except (CancelledError, KeyboardInterrupt):
        reporter.event("cancelled", message="Processing cancelled; incomplete outputs are not marked complete.")
        return 130
    except Exception as exc:
        reporter.event("error", message=str(exc))
        return 1
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    reporter = Reporter(args.json_progress or args.worker is not None)
    try:
        if args.worker:
            from .core import JobOptions

            payload = json.loads(args.worker.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Worker job must be a JSON object.")
            # Old saved jobs may contain text conditioning, but Alpha Gen uses
            # only its fixed empty prompt. Discard that retired option entirely.
            payload.pop("prompt", None)
            return execute(JobOptions(**payload), reporter, args.cancel_file or Path(str(args.worker) + ".cancel"))
        if args.input and args.input_option:
            parser.error("Provide either positional input or --input, not both.")
        if args.validate_models:
            from .models import ModelConfig, validate_models

            models = load_models(args)
            errors = validate_models(ModelConfig.from_dict(models))
            reporter.event("models", models=models, errors=errors, valid=not errors)
            if errors and not reporter.machine:
                for error in errors:
                    print(error, file=sys.stderr)
            return 1 if errors else 0
        input_path = args.input_option or args.input
        if not input_path:
            parser.error("An image-sequence or MOV/MP4/MXF input path is required, or use --validate-models.")
        if args.list_sequences:
            from .sequences import discover_sequences

            reporter.event("sequences", sequences=[seq.to_dict() for seq in discover_sequences(Path(input_path))])
            return 0
        options = options_from_args(args)
        if args.dry_run:
            from .core import plan_job

            reporter.event("plan", plan=plan_job(options).to_dict())
            return 0
        return execute(options, reporter, args.cancel_file)
    except KeyboardInterrupt:
        reporter.event("cancelled", message="Processing cancelled.")
        return 130
    except (ImportError, OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        reporter.event("error", message=str(exc))
        return 1
