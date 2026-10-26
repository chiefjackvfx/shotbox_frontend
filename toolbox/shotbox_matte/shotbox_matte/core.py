"""One processing backend shared by the headless CLI and desktop worker.

The reference workflow ingests display RGB video. EXR preparation here uses an
explicit sRGB transfer (and AP1/D60 conversion for ACEScg), clipping out-of-display
range source values. It does not implement a show-specific OCIO/filmic view. The
decoded matte is data and bypasses this colour preparation entirely.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import math
from typing import Any, Callable, Iterator

from .export import ExportSession, frame_filename, preview_output, validate_suffix
from .imaging import SOURCE_COLORS, SpatialPlan, prepare_color, prepare_spatial, read_image_window, restore_spatial, spatial_plan, write_exr, write_preview
from .media import default_source_color, read_source_frames
from .sequences import SequenceInfo, discover_sequences, select_sequence_indices
from .temporal import TemporalChunk, padded_frame_count, temporal_chunks


Progress = Callable[[str, int, int, str], None]


class CancelledError(RuntimeError):
    """Cooperative cancellation; no output has been committed as complete."""


@dataclass
class JobOptions:
    input_path: str
    sequence: str | None = None
    source_color: str = "auto"
    fps: float = 24.0
    frame_skip: int = 0
    frame_cap: int = 0
    generation_width: int = 960
    generation_height: int = 544
    seed: int = 42
    suffix: str = "_roto"
    first_frame: int | None = None
    padding: int = 4
    precision: str = "half"
    existing: str = "version-up"
    models: dict = field(default_factory=dict)
    offload: str = "cpu"
    low_vram: bool = False
    source_start: int | None = None
    source_end: int | None = None
    temporal_mode: str = "segmented"
    output_channels: str = "rgb"


@dataclass(frozen=True)
class JobPlan:
    sequence: SequenceInfo
    selected_files: tuple[Path, ...]
    source_numbers: tuple[int | None, ...]
    spatial: SpatialPlan
    padded_count: int
    first_frame: int
    output_preview: Path
    warnings: tuple[str, ...]
    source_color: str = "linear-srgb"
    selected_indices: tuple[int, ...] = ()
    requested_source_start: int | None = None
    requested_source_end: int | None = None
    fps: float = 24.0
    frame_skip: int = 0
    chunks: tuple[TemporalChunk, ...] = ()
    temporal_mode: str = "segmented"
    output_channels: str = "rgb"

    @property
    def selected_count(self) -> int:
        return len(self.selected_files)

    @property
    def added_copies(self) -> int:
        return self.padded_count - self.selected_count

    def to_dict(self) -> dict:
        overlap_count = sum(left.stop - right.start for left, right in zip(self.chunks, self.chunks[1:]))
        return {"sequence": self.sequence.to_dict(),
                "source_width": self.sequence.width, "source_height": self.sequence.height,
                "source_count": self.sequence.count, "original_count": self.sequence.count,
                "selected_count": self.selected_count, "padded_count": self.padded_count,
                "added_copies": self.added_copies,
                "chunk_count": len(self.chunks),
                "temporal_mode": self.temporal_mode,
                "experimental_full_range": self.temporal_mode == "full-range",
                "temporal_chunks": [chunk.to_dict() for chunk in self.chunks],
                "overlap_count": overlap_count,
                "padding_count": sum(chunk.padded_count - chunk.selected_count for chunk in self.chunks),
                "temporal_blend": "linear" if overlap_count else "none",
                "selected_files": [str(p) for p in self.selected_files],
                "selected_indices": list(self.selected_indices),
                "source_range_mode": self.sequence.source_range_mode,
                "source_start": self.sequence.source_start if self.requested_source_start is None else self.requested_source_start,
                "source_end": self.sequence.source_end if self.requested_source_end is None else self.requested_source_end,
                "requested_source_start": self.requested_source_start,
                "requested_source_end": self.requested_source_end,
                "selected_source_start": (self.source_numbers[0] if self.sequence.source_range_mode == "frame-number" else self.selected_indices[0] + 1),
                "selected_source_end": (self.source_numbers[-1] if self.sequence.source_range_mode == "frame-number" else self.selected_indices[-1] + 1),
                "source_numbers": list(self.source_numbers), "first_frame": self.first_frame,
                "output_preview": str(self.output_preview), "output_is_provisional": True,
                "generation_width": self.spatial.width, "generation_height": self.spatial.height,
                "spatial": self.spatial.to_dict(), "warnings": list(self.warnings),
                "output_channels": ["A"] if self.output_channels == "alpha" else ["R", "G", "B"],
                "source_color": self.source_color,
                "prompt": "",
                "fps": self.fps, "frame_skip": self.frame_skip,
                "color_preparation": (
                    "Display-encoded sRGB input clipped to [0,1]; no transfer curve applied. "
                    if self.source_color == "srgb" else
                    "ACEScg converted to linear Rec.709, then clipped and sRGB encoded. "
                    if self.source_color == "acescg" else
                    "Linear Rec.709 input clipped and sRGB encoded. "
                ) + "Matte EXRs preserve raw decoded values."}


def pad_frames(frames):
    import numpy as np
    if frames.ndim != 4 or not len(frames):
        raise ValueError("Expected a non-empty [frames, height, width, channels] batch.")
    extra = padded_frame_count(len(frames)) - len(frames)
    return np.concatenate((frames, np.repeat(frames[-1:], extra, axis=0)), axis=0) if extra else frames


def trim_frames(frames, original_count: int):
    if original_count < 1 or len(frames) < original_count:
        raise ValueError(f"Inference decoded {len(frames)} frames; expected at least {original_count}.")
    return frames[:original_count]


def plan_job(options: JobOptions) -> JobPlan:
    """Inspect headers and calculate a plan. No weights, CUDA, Qt, or output writes."""
    if options.source_color not in ("auto", *SOURCE_COLORS):
        raise ValueError(f"Source colour must be one of: auto, {', '.join(SOURCE_COLORS)}.")
    if not isinstance(options.fps, (float, int)) or not math.isfinite(options.fps) or options.fps <= 0:
        raise ValueError("FPS must be a finite positive number.")
    if not isinstance(options.seed, int) or options.seed < 0 or options.seed >= 2**63:
        raise ValueError("Seed must be an integer from 0 through 2^63 - 1.")
    if options.precision not in ("half", "float"):
        raise ValueError("EXR precision must be half or float.")
    if options.output_channels not in ("rgb", "alpha"):
        raise ValueError("EXR output_channels must be rgb or alpha.")
    validate_suffix(options.suffix)
    sequences = discover_sequences(Path(options.input_path))
    if options.sequence:
        matches = [s for s in sequences if s.id == options.sequence]
        if not matches:
            raise ValueError(f"Sequence {options.sequence!r} was not found. Choices: {', '.join(s.id for s in sequences)}")
        sequence = matches[0]
    elif len(sequences) == 1:
        sequence = sequences[0]
    else:
        raise ValueError(f"Multiple image sequences or movies found; select one: {', '.join(s.id for s in sequences)}")
    indices = select_sequence_indices(sequence, options.frame_skip, options.frame_cap,
                                      options.source_start, options.source_end)
    files = tuple(sequence.files[index] for index in indices)
    numbers = tuple(sequence.frame_numbers[index] for index in indices)
    source_color = default_source_color(files[0]) if options.source_color == "auto" else options.source_color
    chunks = temporal_chunks(len(files), options.fps, options.temporal_mode)
    padded = sum(chunk.padded_count for chunk in chunks)
    spatial = spatial_plan(sequence.width, sequence.height, options.generation_width, options.generation_height)
    first = options.first_frame if options.first_frame is not None else (numbers[0] if numbers[0] is not None else 1001)
    output = preview_output(sequence.output_source, options.suffix, options.existing)
    frame_filename(output, first, options.padding)
    warnings = []
    if options.temporal_mode == "full-range" and (padded > 145 or (padded - 1) / options.fps > 6.0 + 1e-9):
        warnings.append(f"Experimental full range: {len(files)} selected frames → {padded} model frames in one pass, beyond the supported 145-frame / 6-second range. RGB may leak into the matte; GPU memory use can increase substantially. No automatic fallback to segments.")
    elif len(chunks) > 1:
        warnings.append(f"Processing {len(files)} selected frames in {len(chunks)} segments of at most 121 model frames, blending any shared matte frames. Review transitions for subject changes and seams.")
    elif padded > 121:
        warnings.append("More than 121 model frames increases memory demand; the supported upper limit remains 145 frames / 6 seconds.")
    known = [n for n in sequence.frame_numbers if n is not None]
    if len(known) > 1 and any(b - a != 1 for a, b in zip(known, known[1:])):
        warnings.append("The source sequence has frame-number gaps. Selected frames export with consecutive output numbering.")
    if options.frame_skip:
        warnings.append("Frame skip selects every N+1 source frame; output numbering remains consecutive and the configured FPS is passed to the model.")
    if sequence.fps is not None and abs(sequence.fps - options.fps) > 0.01:
        warnings.append(f"Movie metadata reports {sequence.fps:g} FPS; processing uses the configured {options.fps:g} FPS.")
    return JobPlan(sequence, files, numbers, spatial, padded, first, output, tuple(warnings), source_color, indices,
                   options.source_start, options.source_end, options.fps, options.frame_skip, chunks, options.temporal_mode,
                   options.output_channels)


def _infer_mattes(
    plan: JobPlan, options: JobOptions, engine: Any, sources: Iterator[Any],
    source_preview: Path, progress: Progress, check_cancel: Callable[[], None],
    cancelled: Callable[[], bool],
) -> Iterator[Any]:
    """Stream bounded source segments, holding only their shared RGB/matte tails."""
    import numpy as np

    pending: list[Any] = []
    source_tail = None
    prepared_count = 0
    decoded_before = 0
    for chunk_index, chunk in enumerate(plan.chunks):
        check_cancel()
        frames = np.empty((chunk.padded_count, plan.spatial.height, plan.spatial.width, 3), dtype=np.float32)
        overlap = prepared_count - chunk.start
        if overlap:
            frames[:overlap] = source_tail
        source_tail = None
        for index in range(overlap, chunk.selected_count):
            check_cancel()
            try:
                source = next(sources)
            except StopIteration as exc:
                raise ValueError(f"Source decoded {prepared_count} selected frames; expected {plan.selected_count}. The movie or sequence may be truncated.") from exc
            pixels = prepare_color(source, plan.source_color)
            frames[index] = prepare_spatial(pixels, plan.spatial)
            if prepared_count == 0:
                write_preview(source_preview, pixels)
            prepared_count += 1
            progress("prepare", prepared_count, plan.selected_count, plan.selected_files[prepared_count - 1].name)
        frames[chunk.selected_count:] = frames[chunk.selected_count - 1]
        next_start = plan.chunks[chunk_index + 1].start if chunk_index + 1 < len(plan.chunks) else chunk.stop
        if next_start < chunk.stop:
            source_tail = frames[next_start - chunk.start:chunk.selected_count].copy()
        label = "Full range" if options.temporal_mode == "full-range" else f"Segment {chunk_index + 1}/{len(plan.chunks)}"

        def chunk_progress(stage: str, current: int, total: int, message: str) -> None:
            if len(plan.chunks) == 1:
                progress(stage, current, total, message)
            elif stage == "decode":
                progress(stage, decoded_before + current, plan.padded_count, f"{label}: {message}")
            else:
                progress(stage, chunk_index * total + current, len(plan.chunks) * total, f"{label}: {message}")

        check_cancel()
        progress("inference", decoded_before, plan.padded_count,
                 f"{label}: {chunk.selected_count} selected → {chunk.padded_count} model frames")
        inference_options = dict(fps=options.fps, seed=options.seed, progress=chunk_progress, cancelled=cancelled)
        if options.temporal_mode == "full-range":
            inference_options["force_full_range"] = True
        generated = engine.infer(frames, **inference_options)
        decoded_count = 0
        next_pending = []
        try:
            for index, matte in enumerate(generated):
                check_cancel()
                decoded_count = index + 1
                if decoded_count > chunk.padded_count:
                    raise ValueError(f"Inference returned more than {chunk.padded_count} expected model frames in {label.lower()}.")
                if index >= chunk.selected_count:
                    continue
                if index < len(pending):
                    weight = (index + 1) / (len(pending) + 1)
                    matte = pending[index] * (1 - weight) + matte * weight
                if chunk.start + index >= next_start:
                    next_pending.append(matte)
                else:
                    yield matte
            if decoded_count != chunk.padded_count:
                raise ValueError(f"Inference decoded {decoded_count} frames; expected {chunk.padded_count} padded model frames in {label.lower()}.")
        finally:
            closer = getattr(generated, "close", None)
            if closer is not None:
                closer()
        pending = next_pending
        decoded_before += decoded_count
        # Release this segment's full RGB buffer before allocating the next one.
        del frames
    check_cancel()
    try:
        next(sources)
    except StopIteration:
        pass
    else:
        raise ValueError("Source decoder returned more selected frames than planned.")


def run_job(options: JobOptions, progress: Progress | None = None,
            cancelled: Callable[[], bool] | None = None) -> dict:
    progress = progress or (lambda stage, current, total, message: None)
    cancelled = cancelled or (lambda: False)

    def check_cancel():
        if cancelled():
            raise CancelledError("Job cancelled. No replacement sequence was committed.")

    check_cancel()
    progress("inspect", 0, 1, "Inspecting source sequence or movie")
    plan = plan_job(options)
    check_cancel()
    # Keep per-frame canvas metadata separate from model rasters. Window origins
    # may animate; changing raster dimensions requires a different spatial plan.
    windows = []
    if plan.sequence.media_type != "video":
        for path in plan.selected_files:
            check_cancel()
            window = read_image_window(path)
            if (window.width, window.height) != (plan.sequence.width, plan.sequence.height):
                raise ValueError(f"Sequence frame size changed in {path.name}: expected {plan.sequence.width} x {plan.sequence.height}, got {window.width} x {window.height}. Split frames with different data-window sizes into separate jobs.")
            windows.append(window)
    # These imports belong exclusively to execution, keeping --dry-run headless and lightweight.
    from .native import ModelConfig, NativeAlphaEngine
    config = ModelConfig.from_dict(options.models)
    engine = NativeAlphaEngine(config, offload=options.offload, low_vram=options.low_vram)
    manifest = plan.to_dict()
    manifest["source_windows"] = [window.to_dict() for window in windows]
    session = ExportSession.reserve(plan.sequence.output_source, options.suffix, options.existing, manifest)
    generated = None
    sources = None
    try:
        check_cancel()
        progress("prepare", 0, plan.selected_count, "Reading and converting source frames")
        sources = read_source_frames(plan.sequence, plan.selected_indices, cancelled=cancelled)
        generated = _infer_mattes(plan, options, engine, sources, session.job_dir / "_shotbox_source.png",
                                  progress, check_cancel, cancelled)
        names = []
        decoded_count = 0
        for index, matte in enumerate(generated):
            check_cancel()
            decoded_count = index + 1
            if decoded_count > plan.selected_count:
                raise ValueError(f"Inference returned more than {plan.selected_count} expected selected frames.")
            matte = restore_spatial(matte, plan.spatial)
            name = frame_filename(session.output_dir, plan.first_frame + index, options.padding)
            write_exr(session.stage_dir / name, matte, options.precision,
                      window=windows[index] if windows else None, channels=options.output_channels)
            if index == 0:
                write_preview(session.job_dir / "_shotbox_matte.png", matte)
            names.append(name)
            progress("export", index + 1, plan.selected_count, name)
        if decoded_count != plan.selected_count:
            raise ValueError(f"Inference decoded {decoded_count} frames; expected {plan.selected_count} selected frames.")
        check_cancel()
        progress("commit", 0, 1, "Committing completed EXR sequence")
        session.commit(names)
        result = {"output_dir": str(session.output_dir), "frame_count": len(names),
                  "first_source": str(plan.selected_files[0]), "first_output": str(session.output_dir / names[0]),
                  "source_preview": str(session.job_dir / "_shotbox_source.png"),
                  "matte_preview": str(session.job_dir / "_shotbox_matte.png"),
                  "manifest": str(session.manifest_path), "job_dir": str(session.job_dir), "status": "complete"}
        # A UI/progress sink disappearing after commit must not mark successful files failed.
        try:
            channel_label = "alpha-only" if options.output_channels == "alpha" else "RGB grayscale"
            progress("complete", 1, 1, f"Saved {len(names)} {channel_label} EXRs to {session.output_dir}")
        except Exception:
            pass
        return result
    except BaseException as exc:
        try:
            session.fail(exc, isinstance(exc, (CancelledError, KeyboardInterrupt)))
        except Exception as manifest_error:
            exc.add_note(f"Could not update the partial-job manifest: {manifest_error}")
        raise
    finally:
        # Close a streamed decoder immediately if export or cancellation ends iteration early.
        for iterator in (sources, generated):
            closer = getattr(iterator, "close", None)
            if closer is not None:
                try:
                    closer()
                except Exception:
                    pass
