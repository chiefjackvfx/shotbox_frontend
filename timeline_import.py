"""Read-only timeline discovery, local media matching and import preparation.

All source positions here are zero-based. Ranges are end-exclusive. No source
timeline is rewritten, and no original source location is opened as a fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from timeline_limits import validate_video_track

from arri_metadata import read_embedded_arri_metadata
from media_colourspace import ColourspaceDetection, camera_identity, detect_input_colourspace, lens_identity

MEDIA_EXTENSIONS = {'.mov', '.mp4', '.mxf', '.avi', '.mkv', '.r3d', '.braw', '.arx'}
EPSILON = 0.000001


class Cancelled(Exception):
    pass


def checkpoint(cancel: Callable[[], bool] = lambda: False):
    if cancel():
        raise Cancelled('Cancelled')


def fingerprint(path: str) -> tuple[int, int]:
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def basename(value: str) -> str:
    value = unquote(value).replace('\\', '/')
    return value.rsplit('/', 1)[-1]


def timecode_frames(value: str, fps: float) -> float:
    """Convert SMPTE timecode to frame count, including 29.97/59.94 drop frame."""
    parts = re.fullmatch(r'(\d{2,}):(\d{2}):(\d{2})([:;])(\d{2})', value.strip())
    if not parts:
        raise ValueError(f'Invalid or missing timecode: {value!r}')
    hours, minutes, seconds, separator, frames = parts.groups()
    h, m, s, f = map(int, (hours, minutes, seconds, frames))
    nominal = round(fps)
    if m >= 60 or s >= 60 or f >= nominal:
        raise ValueError(f'Invalid timecode: {value}')
    result = ((h * 60 + m) * 60 + s) * nominal + f
    if separator == ';':
        if not any(abs(fps - rate) < 0.001 for rate in (30000 / 1001, 60000 / 1001)):
            raise ValueError('Drop-frame timecode requires 29.97 or 59.94 fps')
        drop = 2 if nominal == 30 else 4
        if s == 0 and m % 10 and f < drop:
            raise ValueError(f'Invalid dropped frame label: {value}')
        total_minutes = h * 60 + m
        result -= drop * (total_minutes - total_minutes // 10)
    return float(result)


def xml_rate(element, fallback: float = 25) -> float:
    if element is None:
        return fallback
    base = float(element.findtext('rate/timebase', str(fallback)))
    return base * 1000 / 1001 if element.findtext('rate/ntsc', '').upper() == 'TRUE' else base


@dataclass
class Discovery:
    folder: str
    media: list[str] = field(default_factory=list)
    timelines: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def discover(folder: str, cancel=lambda: False) -> Discovery:
    root = Path(folder).resolve()
    if not root.is_dir():
        raise ValueError('Select an existing media folder')
    result = Discovery(str(root))
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=lambda e: result.warnings.append(str(e))):
        checkpoint(cancel)
        dirs[:] = sorted(d for d in dirs if not Path(directory, d).is_symlink())
        for name in sorted(files):
            path = Path(directory, name)
            if path.is_symlink():
                continue
            ext = path.suffix.lower()
            if ext in MEDIA_EXTENSIONS:
                result.media.append(str(path))
            elif ext == '.otio':
                result.timelines.append(str(path))
            elif ext == '.drt':
                result.timelines.append(str(path))
            elif ext == '.xml':
                try:
                    if ET.parse(path).getroot().tag == 'xmeml':
                        result.timelines.append(str(path))
                    else:
                        result.warnings.append(f'{name}: unsupported XML format (requires xmeml)')
                except (ET.ParseError, OSError) as exc:
                    result.warnings.append(f'{name}: {exc}')
    return result


@dataclass
class MediaInfo:
    path: str
    frames: int = 0
    fps: float = 0
    start: float | None = None
    signature: tuple[int, int] | None = None
    error: str = ''
    metadata: dict[str, str] = field(default_factory=dict)


def probe_media(path: str, cancel=lambda: False) -> MediaInfo:
    info = MediaInfo(str(Path(path).resolve()))
    try:
        info.signature = fingerprint(info.path)
        executable = shutil.which('ffprobe')
        if not executable:
            raise ValueError('ffprobe is required; install FFmpeg and add it to PATH')
        command = [executable, '-v', 'error', '-select_streams', 'v:0',
                   '-show_streams', '-show_format', '-of', 'json', info.path]
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as process:
            import time
            deadline = time.monotonic() + 30
            try:
                while True:
                    checkpoint(cancel)
                    try:
                        output, error = process.communicate(timeout=0.15)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() > deadline:
                            raise ValueError('Media probe timed out after 30 seconds')
                if process.returncode:
                    raise ValueError(error.strip() or 'Cannot read media')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        data = json.loads(output)
        stream = data['streams'][0]
        info.metadata = {f'format.{key}': str(value) for key, value in data.get('format', {}).get('tags', {}).items()}
        info.metadata.update({f'stream.{key}': str(value) for key, value in stream.get('tags', {}).items()})
        info.metadata.update({f'video.{key}': str(stream[key]) for key in ('color_space', 'color_transfer', 'color_primaries') if key in stream})
        info.metadata.update(read_embedded_arri_metadata(info.path, cancel))
        checkpoint(cancel)
        info.fps = float(Fraction(stream['r_frame_rate']))
        count = stream.get('nb_frames')
        if count and count != 'N/A':
            info.frames = int(count)
        else:
            seconds = (float(stream['duration_ts']) * float(Fraction(stream['time_base']))
                       if stream.get('duration_ts') is not None else float(stream['duration']))
            exact = seconds * info.fps
            if abs(exact - round(exact)) > 0.01:
                raise ValueError('Media length is not an integral frame count')
            info.frames = round(exact)
        if info.frames <= 0 or info.fps <= 0:
            raise ValueError('Invalid media frame count/rate')
        tags = {**data.get('format', {}).get('tags', {}), **stream.get('tags', {})}
        tc = tags.get('timecode') or tags.get('TIMECODE')
        if tc:
            info.start = timecode_frames(tc, info.fps)
    except Cancelled:
        raise
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError) as exc:
        info.error = str(exc)
    return info


@dataclass
class TimelineClip:
    uid: str
    name: str
    reference: str
    file_name: str
    reel: str
    track: int
    timeline_start: int
    duration: int
    fps: float
    source_start: float | None
    speed: float = 1.0
    error: str = ''

    @property
    def identity(self):
        return basename(self.file_name or self.reference or self.name).casefold()

    @property
    def pairing_key(self):
        return self.track, self.timeline_start, self.duration, self.identity


@dataclass
class TimelineDocument:
    path: str
    name: str
    fps: float
    clips: list[TimelineClip]
    warnings: list[str] = field(default_factory=list)
    signature: tuple[int, int] | None = None


def read_xml(path: str) -> TimelineDocument:
    root = ET.parse(path).getroot()
    sequences = root.findall('sequence')
    if root.tag != 'xmeml' or len(sequences) != 1:
        raise ValueError('XML must contain exactly one xmeml sequence')
    sequence = sequences[0]
    fps = xml_rate(sequence, 0)
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError('XML sequence has no valid frame rate')
    files = {f.get('id'): f for f in root.iter('file') if f.find('pathurl') is not None}
    result = TimelineDocument(path, Path(path).stem, fps, [], signature=fingerprint(path))
    for number, track in enumerate(sequence.findall('media/video/track'), 1):
        if track.findall('transitionitem'):
            raise ValueError(f'V{number}: transitions are not supported')
        for index, element in enumerate(track.findall('clipitem')):
            validate_video_track(number)
            if element.findtext('enabled', 'TRUE').upper() == 'FALSE':
                result.warnings.append(f'V{number}: disabled clip omitted')
                continue
            errors = []
            file = element.find('file')
            if file is not None and file.find('pathurl') is None:
                file = files.get(file.get('id'), file)
            name = element.findtext('name', 'unnamed')
            start = int(element.findtext('start', '-1'))
            end = int(element.findtext('end', '-1'))
            if start < 0 or end <= start:
                errors.append('Invalid editorial range or unsupported transition')
            clip_fps = xml_rate(element, xml_rate(file, fps))
            if abs(clip_fps - fps) > EPSILON:
                errors.append('Mixed source/timeline rates require conversion outside this importer')
            if element.find('sequence') is not None or file is None:
                errors.append('Nested sequence or missing external media reference')
            source_in = float(element.findtext('in', '0'))
            speed = 1.0
            for filter_element in element.findall('filter'):
                if filter_element.findtext('enabled', 'TRUE').upper() == 'FALSE':
                    continue
                effect = filter_element.find('effect')
                if effect is None:
                    continue
                if effect.findtext('effectid') != 'timeremap':
                    continue
                parameters = {p.findtext('parameterid'): p for p in effect.findall('parameter')}
                def value(key, default):
                    return parameters[key].findtext('value', default) if key in parameters else default
                speed = float(value('speed', '100')) / 100
                if value('variablespeed', '0') != '0' or value('reverse', 'FALSE').upper() == 'TRUE' or speed <= 0:
                    errors.append('Only forward constant-speed retimes are supported')
                graph = parameters.get('graphdict')
                anchors = graph.findall('keyframe') if graph is not None else []
                cut_anchor = next((k for k in anchors if k.findtext('speedkfin', '').upper() == 'TRUE'), None)
                if cut_anchor is None:
                    errors.append('Retime has no reliable source cut anchor')
                else:
                    source_in = float(cut_anchor.findtext('value'))
            source_start = None
            try:
                source_start = timecode_frames(file.findtext('timecode/string', ''), clip_fps) + source_in
            except (ValueError, AttributeError):
                pass  # Review can supply the local cut start explicitly.
            result.clips.append(TimelineClip(
                f'{number}:{index}', name, file.findtext('pathurl', '') if file is not None else '',
                file.findtext('name', '') if file is not None else '',
                file.findtext('timecode/reel/name', '') if file is not None else '',
                number, start, end - start, clip_fps, source_start, speed, '; '.join(errors)))
    if sequence.findall('media/audio/track/clipitem'):
        result.warnings.append('Audio is not imported')
    _validate_structure(result)
    return result


def read_otio(path: str) -> TimelineDocument:
    try:
        import opentimelineio as otio
    except ImportError as exc:
        setup_script = 'setup_runtime.ps1' if os.name == 'nt' else 'setup_runtime.sh'
        raise ValueError(
            f'OTIO could not be loaded in {sys.executable}: {exc}. '
            f'Run Shotbox\'s {setup_script} to update its dependencies, then restart Shotbox.'
        ) from exc
    timeline = otio.adapters.read_from_file(path)
    if not isinstance(timeline, otio.schema.Timeline):
        raise ValueError('OTIO root must be a Timeline')
    rate = timeline.global_start_time.rate if timeline.global_start_time is not None else timeline.duration().rate
    result = TimelineDocument(path, Path(path).stem, rate, [], signature=fingerprint(path))
    number = 0
    for track in timeline.tracks:
        if not isinstance(track, otio.schema.Track):
            raise ValueError('Nested OTIO stacks are not supported')
        if track.kind != otio.schema.TrackKind.Video:
            if any(isinstance(item, otio.schema.Clip) for item in track):
                result.warnings.append('Audio/non-video tracks are not imported')
            continue
        number += 1
        for index, clip in enumerate(track):
            if isinstance(clip, otio.schema.Gap):
                continue
            if not isinstance(clip, otio.schema.Clip):
                raise ValueError(f'V{number}: unsupported transition or nested timeline')
            validate_video_track(number)
            if not clip.enabled:
                result.warnings.append(f'V{number} {clip.name}: disabled clip omitted')
                continue
            reference = clip.media_reference
            errors = []
            if not isinstance(reference, otio.schema.ExternalReference):
                errors.append('Only external media references are supported')
            source = clip.trimmed_range()
            parent = track.range_of_child(clip)
            start = parent.start_time.rescaled_to(rate).value
            duration = parent.duration.rescaled_to(rate).value
            if abs(start - round(start)) > EPSILON or abs(duration - round(duration)) > EPSILON:
                errors.append('Non-integral editorial range')
            if abs(source.start_time.rate - rate) > EPSILON:
                errors.append('Mixed source/timeline rates require conversion outside this importer')
            speed = 1.0
            time_effects = [e for e in clip.effects if isinstance(e, otio.schema.TimeEffect)]
            if len(time_effects) > 1:
                errors.append('Multiple timing effects are not supported')
            for effect in time_effects:
                if isinstance(effect, otio.schema.LinearTimeWarp) and effect.time_scalar > 0:
                    speed = effect.time_scalar
                else:
                    errors.append('Only forward constant-speed retimes are supported')
            result.clips.append(TimelineClip(
                f'{number}:{index}', clip.name, getattr(reference, 'target_url', ''), reference.name,
                clip.metadata.get('Resolve_OTIO', {}).get('Reel Number', ''), number,
                round(start), round(duration), rate, source.start_time.rescaled_to(rate).value,
                speed, '; '.join(errors)))
    _validate_structure(result)
    result.warnings = list(dict.fromkeys(result.warnings))
    return result


def read_timeline(path: str) -> TimelineDocument:
    if Path(path).suffix.lower() == '.drt':
        from drt_import import read_drt
        return read_drt(path)
    return read_otio(path) if Path(path).suffix.lower() == '.otio' else read_xml(path)


def _validate_structure(document):
    primary = [c for c in document.clips if c.track == 1]
    if not primary:
        raise ValueError('Timeline has no V1 clips to create shots')
    occupied = set()
    for clip in document.clips:
        validate_video_track(clip.track)
        key = clip.track, clip.timeline_start
        if key in occupied:
            clip.error = 'Multiple clips occupy the same track/start'
        occupied.add(key)
        if clip.track > 1 and not any(c.timeline_start == clip.timeline_start and c.duration == clip.duration for c in primary):
            raise ValueError(f'V{clip.track} {clip.name}: layer is not aligned to a V1 cut')


@dataclass
class Match:
    path: str | None
    reason: str
    candidates: list[str]


def match_media(clip: TimelineClip, media: dict[str, MediaInfo]) -> Match:
    hints = {basename(s) for s in (clip.reference, clip.file_name) if s}
    # Display names are less reliable: use only when no explicit media identity exists.
    if not hints:
        hints = {basename(clip.name)}
    exact = [p for p in media if Path(p).name in hints]
    insensitive = [p for p in media if Path(p).name.casefold() in {s.casefold() for s in hints}]
    candidates = exact or insensitive
    reason = 'Exact filename' if exact else 'Case-insensitive filename'
    if len(candidates) > 1:
        if len({Path(p).name.casefold() for p in candidates}) > 1:
            return Match(None, 'Conflicting source identity hints', candidates)
        compatible = [p for p in candidates if _covers_cut(clip, media[p])]
        if len(compatible) == 1:
            return Match(compatible[0], reason + ' + timecode coverage', candidates)
    if len(candidates) == 1:
        return Match(candidates[0], reason, candidates)
    if candidates:
        return Match(None, 'Ambiguous filename', candidates)
    stems = {Path(s).stem.casefold() for s in hints} | {clip.reel.casefold()}
    suggestions = [p for p in media if Path(p).stem.casefold() in stems]
    return Match(None, 'Confirm suggested media' if suggestions else 'Missing local media', suggestions)


def local_start(clip: TimelineClip, media: MediaInfo) -> float | None:
    if clip.source_start is None or media.start is None:
        return None
    value = clip.source_start - media.start
    return float(round(value)) if abs(value - round(value)) <= EPSILON else value


def _covers_cut(clip, media):
    start = local_start(clip, media)
    return (not media.error and abs(clip.fps - media.fps) <= EPSILON and start is not None
            and start >= -EPSILON and start + (clip.duration - 1) * clip.speed <= media.frames - 1 + EPSILON)


def timing_conflicts(document: TimelineDocument, companion: TimelineDocument | None):
    """Return all differing paired source mappings. Never choose a winner."""
    conflicts = {}
    if companion is None:
        return conflicts
    if abs(document.fps - companion.fps) > EPSILON:
        raise ValueError('Companion timeline frame rate differs')
    lookup = {}
    for clip in companion.clips:
        lookup.setdefault(clip.pairing_key, []).append(clip)
    for clip in document.clips:
        matches = lookup.get(clip.pairing_key, [])
        if len(matches) != 1:
            raise ValueError(f'{clip.name} at {clip.timeline_start}: companion structure/identity does not align; select another timeline or no companion')
        other = matches[0]
        if clip.source_start is None or other.source_start is None:
            continue
        if abs(clip.source_start - other.source_start) > EPSILON or abs(clip.speed - other.speed) > EPSILON or clip.error != other.error:
            conflicts[clip.uid] = other
    if len(document.clips) != len(companion.clips):
        raise ValueError('Companion has a different number of clip occurrences')
    return conflicts


@dataclass
class ReviewRow:
    clip: TimelineClip
    shot_index: int
    match: Match
    local_cut_start: float | None = None
    available_head: int = 0
    available_tail: int = 0
    timing_source: str = ''
    alternative: TimelineClip | None = None
    errors: list[str] = field(default_factory=list)
    input_colourspace: str | None = None
    colourspace_detection: ColourspaceDetection | None = None


@dataclass
class PreparedImport:
    shots: list
    rows: list[ReviewRow]
    errors: list[str]
    documents: list[TimelineDocument]
    media: dict[str, MediaInfo]
    detected_handles: dict[int, tuple[int, int]] = field(default_factory=dict)

    def revalidate(self, cancel=lambda: False):
        for document in self.documents:
            if fingerprint(document.path) != document.signature:
                raise ValueError(f'Timeline changed since review: {document.path}')
        for path, expected in self.media.items():
            checkpoint(cancel)
            current = probe_media(path, cancel)
            if current.error or current.signature != expected.signature or (current.frames, current.fps, current.start, current.metadata) != (expected.frames, expected.fps, expected.start, expected.metadata):
                raise ValueError(f'Media changed or became unavailable since review: {path}')


def prepare_import(document: TimelineDocument, media: dict[str, MediaInfo], *, companion=None,
                   path_overrides=None, timing_choices=None, offset_overrides=None,
                   handles=None, accepted_handles=None, excluded=(), auto_colourspace=False,
                   colourspace_overrides=None, manual_colourspace=None) -> PreparedImport:
    """Resolve in memory. handles=None uses measured head/tail availability."""
    from import_xml_v2 import ParsedClip, XMLTimelineParser
    path_overrides = path_overrides or {}
    timing_choices = timing_choices or {}
    offset_overrides = offset_overrides or {}
    accepted_handles = accepted_handles or {}
    colourspace_overrides = colourspace_overrides or {}
    conflicts = timing_conflicts(document, companion)
    primaries = [c for c in document.clips if c.track == 1]
    shot_indices = {c.timeline_start: i for i, c in enumerate(primaries)}
    rows, errors, resolved = [], [], {}
    for clip in document.clips:
        index = shot_indices.get(clip.timeline_start, -1)
        if index in excluded:
            continue
        other = conflicts.get(clip.uid)
        choice = timing_choices.get(clip.uid)
        effective = other if choice == 'companion' and other else clip
        match = match_media(effective, media)
        if clip.uid in path_overrides:
            override = str(Path(path_overrides[clip.uid]).resolve())
            match = Match(override if override in media else None, 'Manual selection', [override])
        row = ReviewRow(clip, index, match, alternative=other,
                        timing_source=companion.path if choice == 'companion' and other else document.path)
        rows.append(row)
        if other and choice not in ('selected', 'companion'):
            row.errors.append('Choose XML or OTIO timing')
        if effective.error:
            row.errors.append(effective.error)
        info = media.get(match.path)
        if info is None:
            row.errors.append(match.reason)
            continue
        if info.error:
            row.errors.append(info.error)
            continue
        row.colourspace_detection = detect_input_colourspace(info.metadata)
        row.input_colourspace = (colourspace_overrides.get(clip.uid) or row.colourspace_detection.colourspace
                                if auto_colourspace else manual_colourspace)
        if auto_colourspace and not row.input_colourspace:
            row.errors.append(row.colourspace_detection.reason)
        if abs(info.fps - document.fps) > EPSILON:
            row.errors.append('Media frame rate differs from timeline')
        start = offset_overrides.get(clip.uid, local_start(effective, info))
        row.local_cut_start = start
        if start is None or not math.isfinite(start):
            row.errors.append('Missing timecode: enter local cut start')
            continue
        speed = effective.speed
        if speed <= 0 or not math.isfinite(speed):
            row.errors.append('Invalid constant speed')
            continue
        last = start + (clip.duration - 1) * speed
        if start < -EPSILON or last > info.frames - 1 + EPSILON:
            row.errors.append('Editorial cut lies outside local media')
        row.available_head = max(0, math.floor((start + EPSILON) / speed))
        row.available_tail = max(0, math.floor((info.frames - 1 - last + EPSILON) / speed))
        resolved[clip.uid] = (effective, info, start)
    parser = XMLTimelineParser()
    parser.clips_by_track = {clip.track: [] for clip in document.clips}
    for row in rows:
        if row.clip.uid not in resolved or row.clip.track not in parser.clips_by_track:
            continue
        effective, info, start = resolved[row.clip.uid]
        clip = row.clip
        parsed = ParsedClip(clip.name, info.path, 0, 0, clip.duration,
                            clip.timeline_start, clip.timeline_start + clip.duration, clip.track)
        parsed.media_timing = {'cut_start': start, 'speed': effective.speed,
                               'frames': info.frames, 'original_reference': clip.reference,
                               'timing_source': row.timing_source, 'signature': info.signature}
        identity = camera_identity(info.metadata)
        parsed.media_timing.update(camera_make=identity['make'], camera_model=identity['model'])
        parsed.media_timing.update(lens_identity(info.metadata))
        for key in ('camera_metadata_source', 'exposure_index', 'white_balance_kelvin'):
            if key in info.metadata:
                parsed.media_timing[key] = info.metadata[key]
        if row.input_colourspace:
            parsed.media_timing.update(input_colourspace=row.input_colourspace,
                                       colourspace_source='manual' if clip.uid in colourspace_overrides or not auto_colourspace else row.colourspace_detection.source,
                                       colourspace_reason='Manual input transform' if clip.uid in colourspace_overrides or not auto_colourspace else row.colourspace_detection.reason)
        parser.clips_by_track[clip.track].append(parsed)
    shots = parser._link_clips_to_shots(handles or 0)
    by_start = {s.edit_inpoint: s for s in shots}
    detected = {}
    for start, index in shot_indices.items():
        if index in excluded:
            continue
        shot_rows = [r for r in rows if r.shot_index == index]
        head = min((r.available_head for r in shot_rows), default=0)
        tail = min((r.available_tail for r in shot_rows), default=0)
        detected[index] = (head, tail)
        accepted = accepted_handles.get(index, (head, tail) if handles is None else (handles, handles))
        if any(not isinstance(v, int) or v < 0 for v in accepted) or (handles is not None and max(accepted) > handles):
            errors.append(f'Shot {index + 1}: accepted handles must be nonnegative and within any manual request')
        if accepted[0] > head or accepted[1] > tail:
            errors.append(f'Shot {index + 1}: requested {accepted[0]}/{accepted[1]} head/tail; available {head}/{tail}. Explicitly accept fewer handles or choose longer media.')
        shot = by_start.get(start)
        if shot is not None:
            shot.index = index
            shot.cut_duration = shot.duration
            shot.head_handles, shot.tail_handles = accepted
            shot.detected_head_handles, shot.detected_tail_handles = head, tail
            shot.requested_handles = handles
            shot.duration += sum(accepted)
            shot.fps = document.fps
            shot.timeline_inpoint = shot.edit_inpoint
            shot.timeline_outpoint = shot.edit_outpoint
    for row in rows:
        errors.extend(f'Shot {row.shot_index + 1} V{row.clip.track} {row.clip.name}: {error}' for error in row.errors)
    if not rows:
        errors.append('No included shots')
    used_media = {r.match.path: media[r.match.path] for r in rows if r.match.path in media}
    return PreparedImport(shots, rows, errors, [document] + ([companion] if companion else []), used_media, detected)
