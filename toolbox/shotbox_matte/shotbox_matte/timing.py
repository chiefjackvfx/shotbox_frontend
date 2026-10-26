"""Per-stage timing: estimates apply only to progress actually observed."""
from __future__ import annotations

import math
from datetime import datetime, timedelta
import json
import time

from .temporal import temporal_chunks


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


class StageTimer:
    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.started: float | None = None
        self.finished: float | None = None
        self.stage = ""
        self.stage_started = 0.0
        self.total = 0.0
        self.current = 0.0
        self.first_current = 0.0
        self.first_sample = 0.0
        self.stage_totals: dict[str, float] = {}
        self._samples: dict[tuple[str, float], tuple[float, float, float]] = {}

    def start(self) -> None:
        self.__init__(self.clock)
        self.started = self.clock()
        self.stage_started = self.started

    def observe(self, stage: str, current: float, total: float) -> None:
        if self.started is None:
            self.start()
        now = self.clock()
        if stage != self.stage or total != self.total:
            if self.stage:
                self.stage_totals[self.stage] = self.stage_totals.get(self.stage, 0) + now - self.stage_started
            self.stage, self.stage_started = stage, now
            self.total = total
            sample = self._samples.get((stage, total), (current, now, current))
            self.first_current, self.first_sample, prior_current = sample
            self.current = max(prior_current, current)
        else:
            self.current = max(self.current, current)
        self._samples[(stage, total)] = (self.first_current, self.first_sample, self.current)

    def finish(self) -> None:
        if self.started is not None and self.finished is None:
            self.finished = self.clock()
            if self.stage:
                self.stage_totals[self.stage] = self.stage_totals.get(self.stage, 0) + self.finished - self.stage_started

    def text(self) -> str:
        if self.started is None:
            return "Elapsed — · Stage ETA unknown"
        now = self.finished if self.finished is not None else self.clock()
        elapsed = duration(now - self.started)
        if self.finished is not None:
            return f"Elapsed {elapsed}"
        advanced = self.current - self.first_current
        sample_seconds = now - self.first_sample
        remaining = self.total - self.current
        eta = "unknown"
        if advanced > 0 and sample_seconds >= 1 and remaining >= 0:
            estimate = sample_seconds / advanced * remaining
            if math.isfinite(estimate):
                eta = f"~{duration(estimate)}"
        stage = self.stage.replace("_", " ").capitalize() or "Starting"
        active_seconds = self.stage_totals.get(self.stage, 0) + now - self.stage_started
        return f"Elapsed {elapsed} · {stage} {duration(active_seconds)} · Stage ETA {eta}"


class JobHistory:
    """Bounded successful-job budgets, used for explicitly approximate finish times.

    Active stage seconds are measured independently of the wall-clock sampling
    used by StageTimer's streaming throughput estimate. Future stages remain in
    the budget until complete, including decode/export before they have started.
    """

    DEFAULTS = {
        "generation_width": 960, "generation_height": 544, "offload": "cpu",
        "low_vram": False, "precision": "half", "fps": 24.0,
        "source_color": "auto",
    }
    SELECTED_STAGES = {"prepare", "export"}
    PADDED_STAGES = {"conditioning", "inference", "decode"}

    def __init__(self, stored=None):
        self.records: dict[str, dict] = {}
        if isinstance(stored, dict):
            for key, record in list(stored.items())[-30:]:
                if not isinstance(key, str) or not isinstance(record, dict):
                    continue
                selected, padded, stages = record.get("selected"), record.get("padded"), record.get("stages")
                if (type(selected) is not int or selected < 1
                        or type(padded) is not int or padded < selected
                        or not isinstance(stages, dict)):
                    continue
                clean = {stage: float(seconds) for stage, seconds in stages.items()
                         if isinstance(stage, str) and isinstance(seconds, (int, float))
                         and not isinstance(seconds, bool) and math.isfinite(seconds) and 0 <= seconds < 604800}
                if clean:
                    self.records[key] = {"selected": selected, "padded": padded, "stages": clean}
        self.key = ""
        self.selected = 0
        self.padded = 0
        self.fps = 24.0
        self.temporal_mode = "segmented"
        self.chunked = False
        self.completed: set[str] = set()

    @classmethod
    def fingerprint(cls, options: dict) -> str:
        values = {key: options.get(key, default) for key, default in cls.DEFAULTS.items()}
        values["fps"] = float(values["fps"])
        # Preserve existing segmented history keys; full-range work learns separately.
        if options.get("temporal_mode", "segmented") != "segmented":
            values["temporal_mode"] = options["temporal_mode"]
        return json.dumps(values, sort_keys=True)

    def start(self, options: dict) -> None:
        self.key = self.fingerprint(options)
        self.selected, self.padded = 0, 0
        self.fps = float(options.get("fps", 24.0))
        self.temporal_mode = options.get("temporal_mode", "segmented")
        self.chunked = False
        self.completed.clear()

    def observe(self, stage: str, current: float, total: float) -> None:
        # Inspection is synchronous and reports only a start event. Subsequent
        # preparation proves that fixed phase has already finished.
        if stage == "prepare":
            self.completed.add("inspect")
        if stage in {"decode", "export"} and not self.chunked:
            self.completed.update({"models", "text_encoder", "conditioning", "inference"})
        if stage in {"commit", "complete"}:
            self.completed.update({"decode", "export"})
        if stage == "complete":
            self.completed.add("commit")
        if (stage == "prepare" and isinstance(total, (int, float))
                and not isinstance(total, bool) and math.isfinite(total) and total >= 1):
            self.selected = int(total)
            chunks = temporal_chunks(self.selected, self.fps, self.temporal_mode)
            self.padded = sum(chunk.padded_count for chunk in chunks)
            self.chunked = len(chunks) > 1
        if total > 0 and current >= total:
            self.completed.add(stage)

    def remaining(self, timer: StageTimer) -> float | None:
        record = self.records.get(self.key)
        if record is None or not self.selected or timer.started is None or timer.finished is not None:
            return None
        actual = dict(timer.stage_totals)
        if timer.stage:
            actual[timer.stage] = actual.get(timer.stage, 0) + timer.clock() - timer.stage_started
        remaining = 0.0
        for stage, seconds in record["stages"].items():
            if stage in self.completed:
                continue
            multiplier = 1.0
            if stage in self.SELECTED_STAGES:
                multiplier = self.selected / record["selected"]
            elif stage in self.PADDED_STAGES:
                multiplier = self.padded / record["padded"]
            elif stage in {"models", "text_encoder"}:
                multiplier = len(temporal_chunks(self.selected, self.fps, self.temporal_mode)) / len(temporal_chunks(record["selected"], self.fps, self.temporal_mode))
            remaining += max(0, seconds * multiplier - actual.get(stage, 0))
        return remaining

    def text(self, timer: StageTimer, now: datetime | None = None) -> str:
        if timer.finished is not None:
            return "Estimated finish —"
        remaining = self.remaining(timer)
        if remaining is None:
            return "Estimated finish unknown (learning)"
        finish = (now or datetime.now().astimezone()) + timedelta(seconds=remaining)
        return f"Estimated finish ~{finish:%H:%M:%S} · ~{duration(remaining)} remaining (historical)"

    def record_success(self, timer: StageTimer) -> bool:
        if not self.key or not self.selected or timer.finished is None or not timer.stage_totals:
            return False
        self.records.pop(self.key, None)
        self.records[self.key] = {"selected": self.selected, "padded": self.padded,
                                  "stages": dict(timer.stage_totals)}
        while len(self.records) > 30:
            self.records.pop(next(iter(self.records)))
        return True
