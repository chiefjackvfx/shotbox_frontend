"""Source inspection shared by single-shot setup and EXR thumbnail creation."""
from pathlib import Path
import math


def exr_source(path):
    """Return a validated sequence pattern, actual files, and native frame range."""
    from denoise_template import resolve_source
    from nuke_headless_tasks import resolve_preview_sequence_input
    pattern, _, _ = resolve_preview_sequence_input(str(path))
    pattern, first, last = resolve_source(pattern)
    if first is None:
        return str(path), [Path(path)], 1, 1
    import re
    token = re.search(r'#+|%0?\d*d', Path(pattern).name)
    def filename(frame):
        width = len(token[0]) if token[0].startswith('#') else int(re.sub(r'\D', '', token[0]) or 0)
        return Path(pattern).with_name(Path(pattern).name[:token.start()] + f'{frame:0{width}d}' + Path(pattern).name[token.end():])
    return pattern, [filename(frame) for frame in range(first, last + 1)], first, last


def probe_single_media(path, *, fallback_colourspace='', automatic=True, fallback_fps=25):
    from media_colourspace import camera_identity, lens_identity, detect_input_colourspace
    if Path(path).suffix.lower() == '.exr':
        import OpenEXR
        _, files, first, last = exr_source(path)
        source = OpenEXR.InputFile(str(files[0]))
        try:
            header = source.header()
        finally:
            source.close()
        metadata = {key: value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)
                    for key, value in header.items()}
        window = header['displayWindow']
        width, height = window.max.x - window.min.x + 1, window.max.y - window.min.y + 1
        rate = header.get('framesPerSecond')
        fps = float(rate.n / rate.d) if hasattr(rate, 'n') and getattr(rate, 'd', 0) else float(fallback_fps)
        frames = last - first + 1
        source_path = str(files[0])
    else:
        from timeline_import import probe_media
        info = probe_media(path)
        if info.error:
            raise ValueError(f'{Path(path).name}: {info.error}')
        metadata, frames, fps = info.metadata, info.frames, info.fps
        first, last, source_path = 1, frames, str(path)
        width = height = None
    if frames < 1 or not math.isfinite(fps) or fps <= 0:
        raise ValueError(f'{Path(path).name}: invalid frame count or frame rate')
    detection = detect_input_colourspace(metadata)
    colourspace = (detection.colourspace if automatic else None) or fallback_colourspace
    if automatic and not detection.colourspace and Path(path).suffix.lower() == '.exr':
        colourspace = 'ACES - ACEScg'
    identity = camera_identity(metadata)
    timing = dict(single_shot=True, cut_start=0, speed=1.0, frames=frames,
                  source_first=first, source_last=last, fps=fps,
                  camera_make=identity['make'], camera_model=identity['model'],
                  input_colourspace=colourspace, colourspace_source=detection.source if automatic else 'manual',
                  colourspace_reason=detection.reason if automatic else 'Manual input transform')
    timing.update(lens_identity(metadata))
    if width and height:
        timing.update(width=width, height=height)
    for key in ('camera_metadata_source', 'exposure_index', 'white_balance_kelvin'):
        if key in metadata:
            timing[key] = metadata[key]
    return source_path, timing
