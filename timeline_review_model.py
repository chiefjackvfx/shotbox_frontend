"""Desktop editorial timing and preview discovery; no server-side review session."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
import math
import re

from path_mapping import convert_path


def integer(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def natural(value):
    return tuple((1, int(x)) if x.isdigit() else (0, x.casefold())
                 for x in re.split(r"(\d+)", str(value)))


def preview_versions(shot):
    """Match the web's numeric version and legacy filename tie-breaking."""
    base = shot.get("base_path")
    if not base:
        return []
    root = Path(convert_path(base)) / "renders" / "precomp" / "previews"
    entries = []
    try:
        for path in root.glob("*.mp4"):
            match = re.search(r"_v(\d+)(?:_preview)?\.mp4$", path.name, re.I)
            if match and path.is_file():
                stat = path.stat()
                entries.append((int(match[1]), stat.st_mtime_ns,
                                path.name.lower().endswith("_preview.mp4"), path))
    except OSError:
        pass
    entries.sort(key=lambda x: (-x[0], -x[1], x[2], x[3].name.lower()))
    versions = {}
    for version, mtime, legacy, path in entries:
        versions.setdefault(version, {"version": version, "path": str(path), "identity": mtime})
    return list(versions.values())


def prepare_timeline(timeline, cancelled=None):
    """Discover/probe local previews in a background worker, including failures."""
    import av
    from copy import deepcopy
    data = deepcopy(timeline)
    for shot in data.get("shots", []):
        if cancelled and cancelled.is_set():
            return None
        shot["versions"] = preview_versions(shot)
        for media in shot["versions"]:
            if cancelled and cancelled.is_set():
                return None
            try:
                with av.open(media["path"]) as container:
                    stream = container.streams.video[0]
                    media["fps"] = float(stream.average_rate or stream.base_rate or 25)
                    duration = (float(stream.duration * stream.time_base)
                                if stream.duration is not None else
                                float(container.duration or 0) / av.time_base)
                    media["frame_count"] = stream.frames or max(1, round(duration * media["fps"]))
                    media["aspect_ratio"] = stream.width / max(1, stream.height)
            except Exception as exc:
                media["error"] = str(exc)
        shot["preview"] = next(iter(shot["versions"]), None)
    return data


@dataclass
class Cut:
    shot: dict
    start: int
    duration: int
    fps: float

    @property
    def end(self):
        return self.start + self.duration

    @property
    def media(self):
        return self.shot.get("preview") or {}

    @property
    def handles(self):
        return max(0, integer(self.shot.get("handles"), 0))

    @property
    def offset(self):
        count = self.media.get("frame_count", 0)
        length = self.duration * self.media.get("fps", self.fps) / self.fps
        return min(self.handles, max(0, count - length)) if count else self.handles

    def source_frame(self, frame):
        target = math.floor(self.offset + (frame - self.start) * self.media.get("fps", self.fps) / self.fps + 1e-7)
        return max(0, min(max(0, self.media.get("frame_count", 1) - 1), target))

    def working_frame(self, frame, task=None):
        metadata = (task or {}).get("frameio_metadata") or {}
        first = integer((metadata.get("mapping") or {}).get("working_start"), 1001)
        return first + self.handles + frame - self.start

    def task_frame(self, task):
        ref = task.get("frame_reference")
        ref = ref[0] if isinstance(ref, list) and ref else ref
        if type(ref) is not int:
            return self.start, False
        target = self.start + ref - self.working_frame(self.start, task)
        return max(self.start, min(self.end - 1, target)), not self.start <= target < self.end


class Edit:
    def __init__(self, timeline=None):
        self.timeline = timeline or {}
        shots = self.timeline.get("shots", [])
        rates = Counter(s["preview"]["fps"] for s in shots
                        if s.get("preview") and s["preview"].get("fps"))
        self.fps = rates.most_common(1)[0][0] if rates else 25.0
        self.cuts = []
        for shot in shots:
            media = shot.get("preview") or {}
            handles = max(0, integer(shot.get("handles"), 0))
            start, end = integer(shot.get("edit_inpoint")), integer(shot.get("edit_outpoint"))
            count, media_fps = media.get("frame_count", 0), media.get("fps", self.fps)
            duration = integer(shot.get("duration"), 0)
            fallback = next(v for v in (duration - 2 * handles,
                            round((count - 2 * handles) * self.fps / media_fps),
                            duration, round(count * self.fps / media_fps), round(2 * self.fps)) if v > 0)
            self.cuts.append(Cut(shot, start if start is not None else 0,
                                 end - start if start is not None and end is not None and end > start else fallback,
                                 self.fps))
        self.cuts.sort(key=lambda c: (integer(c.shot.get("edit_inpoint"), math.inf), natural(c.shot.get("title")), c.shot["id"]))
        self.sequential = any(integer(c.shot.get("edit_inpoint")) is None
                              or integer(c.shot.get("edit_outpoint"), c.start) <= c.start
                              or (i and c.start < self.cuts[i - 1].end)
                              for i, c in enumerate(self.cuts))
        if self.sequential:
            cursor = self.cuts[0].start if self.cuts else 0
            for cut in self.cuts:
                cut.start, cursor = cursor, cursor + cut.duration
        self.start = self.cuts[0].start if self.cuts else 0
        self.end = self.cuts[-1].end if self.cuts else 0

    def at(self, frame):
        return next((c for c in self.cuts if c.start <= frame < c.end), None)

    def by_id(self, shot_id):
        return next((c for c in self.cuts if c.shot["id"] == shot_id), None)


def timecode(frame, fps):
    nominal = max(1, math.floor(float(fps) + .5))
    seconds, frames = divmod(max(0, int(frame)), nominal)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}:{frames:02}"


def parse_timecode(value, fps):
    if not re.fullmatch(r"\d{2,}:\d{2}:\d{2}:\d{2,3}", value.strip()):
        raise ValueError("Enter timecode as HH:MM:SS:FF.")
    h, m, s, f = map(int, value.split(":"))
    nominal = max(1, math.floor(float(Fraction(str(fps))) + .5))
    if m > 59 or s > 59 or f >= nominal:
        raise ValueError(f"Use minutes/seconds 00–59 and frames 00–{nominal - 1}.")
    return ((h * 60 + m) * 60 + s) * nominal + f
