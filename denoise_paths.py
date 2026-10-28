"""Shared plate identities and read-only denoise output discovery."""
from pathlib import Path
import os
import re


def normalized_sources(sources):
    if isinstance(sources, str):
        sources = [sources]
    return list(dict.fromkeys(str(p).strip() for p in sources
                             if p and str(p).strip().lower() not in {"none", "offline", "—"}))


def source_identity(source):
    from nuke_headless_tasks import resolve_preview_sequence_input
    local = str(Path(source).resolve())
    return os.path.normcase(resolve_preview_sequence_input(local)[0])


def plate_identifier(index, count):
    return f"plate_{index:02d}" if count > 1 else None


def output_stem(shot_name, plate=None):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(shot_name)).strip(" .") or "shot"
    if plate:
        if not re.fullmatch(r"plate_\d+", plate):
            raise ValueError("Invalid denoise plate identifier.")
        name += "_" + plate
    return name + "_DN"


def resolve_output(shot_dir, shot_name, plate=None):
    """Return latest populated sequence (or future v001), without creating folders."""
    parent = Path(shot_dir).resolve() / "renders/precomp"
    stem = output_stem(shot_name, plate)
    version_pattern = re.compile(re.escape(stem) + r"_v(\d+)$")
    candidates = []
    if parent.is_dir():
        for folder in parent.iterdir():
            match = version_pattern.fullmatch(folder.name)
            if not match or not folder.is_dir():
                continue
            frame_pattern = re.compile(re.escape(folder.name) + r"_(\d{4,})\.exr$")
            frames = [int(m[1]) for path in folder.iterdir()
                      if path.is_file() and (m := frame_pattern.fullmatch(path.name))]
            if frames:
                candidates.append((int(match[1]), folder, min(frames), max(frames)))
    if candidates:
        _, folder, first, last = max(candidates, key=lambda item: item[0])
    else:
        folder, first, last = parent / f"{stem}_v001", None, None
    return folder / f"{folder.name}_####.exr", first, last
