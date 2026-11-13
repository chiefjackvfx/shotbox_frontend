"""Small, synchronous activity estimator; no host or network dependencies."""
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
import socket
import time
import uuid


def _wire_text(value, limit, name):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError("invalid " + name)
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("invalid " + name) from exc
    return value


def validate_context(context, schema_version=1):
    if type(schema_version) is not int or schema_version not in (1, 2):
        raise ValueError("unsupported collector schema_version")
    if schema_version == 2:
        if not isinstance(context, dict) or any(
            type(context.get(key)) is not int or not 0 < context[key] < 2**63
            for key in ("job_id", "shot_id")
        ):
            raise ValueError("schema 2 requires positive integer job_id and shot_id")
        path = _wire_text(context.get("script_path", "unknown"), 2048, "script_path")
        return dict(job_id=context["job_id"], shot_id=context["shot_id"], script_path=path)
    if not isinstance(context, dict) or any(
        not isinstance(context.get(key), str) or not context[key].strip()
        for key in ("project_id", "shot_id")
    ):
        raise ValueError("context requires explicit project_id and shot_id strings")
    try:
        return json.loads(json.dumps(context, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("context must be JSON serializable") from exc


class Collector:
    """Serialized, synchronous collector with an injected quick accepting sink.

    The default is the original schema-1 local diagnostic API. Opt into the
    backend profile with ``schema_version=2`` and positive signed-64-bit integer
    ``job_id``/``shot_id`` context. An optional script_path defaults to the literal
    ``unknown``; no filesystem or filename inference is performed. Identity is a
    local binding label only, and is never emitted in schema-2 batches.

    Each schema-2 sink argument is exactly one backend batch. Only transport may
    wrap batches in {schema_version: 2, batches: [...]}. A normal sink return
    acknowledges that batch; exceptions retain an immutable retry snapshot.
    Neither mode performs networking or provides crash durability itself.
    """

    def __new__(cls, *, schema_version=1, **kwargs):
        if cls is Collector and schema_version == 2:
            return object.__new__(_SpanCollector)
        return object.__new__(cls)

    def __init__(self, *, identity, context, emit, monotonic=time.monotonic,
                 utc_now=lambda: datetime.now(timezone.utc), schema_version=1):
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("identity must be an explicit nonempty string")
        if not callable(emit):
            raise ValueError("emit must be a callable sink")
        self.identity = identity
        self.machine = socket.gethostname()
        self.schema_version = schema_version
        self.context = validate_context(context, schema_version)
        self.emit = emit
        self.monotonic = monotonic
        self.utc_now = utc_now
        self.session_id = str(uuid.uuid4())
        self.sequence = 0
        self._pending = deque()
        self.blocked = False
        self.last_error = None
        self.running = False
        self.focused = True
        self.rendering = False
        self.last_activity = None
        self.comp_seconds = 0.0
        self.excluded_gap_seconds = 0.0
        self.last_tick = self.monotonic()
        self.last_utc = self.utc_now()
        self.started_at = self.last_utc.isoformat()

    def start(self):
        if self.blocked:
            raise BufferError("flush retained interval before restarting")
        if self.running:
            return
        self.last_activity = None
        self.rendering = False
        self.running = True
        self.last_tick = self.monotonic()
        self.last_utc = self.utc_now()
        self.started_at = self.last_utc.isoformat()

    def switch_context(self, context):
        context = validate_context(context, self.schema_version)
        if context != self.context:
            self.flush("context_switch")
            self.context = dict(context)
            self.last_activity = None

    def stop(self, reason="stop"):
        if not self.running:
            return
        try:
            self.flush(reason)
        finally:
            self.running = False
            self.last_activity = None

    def _settle(self):
        now = self.monotonic()
        wall = self.utc_now()
        gap = max(now - self.last_tick, (wall - self.last_utc).total_seconds())
        if self.running and gap > 60.0:
            self.excluded_gap_seconds += gap
            self.last_activity = None
        self.last_utc = wall
        if self.running and not self.blocked and self.focused and not self.rendering and self.last_activity is not None:
            end = min(now, self.last_activity + 20.0)
            self.comp_seconds += max(0.0, end - self.last_tick)
        self.last_tick = now
        return now

    def tick(self):
        """Settle elapsed state; schema 2 may seal at limits/epoch changes.

        The sink must be quick and non-reentrant; never use a network sink.
        Schema-1 diagnostic ticks preserve their original no-emission behavior.
        """
        return self._settle()

    def set_rendering(self, rendering):
        self._settle()
        self.rendering = bool(rendering)
        self.last_activity = None

    def set_focus(self, focused):
        self._settle()
        if self.focused != bool(focused):
            self.last_activity = None
        self.focused = bool(focused)

    def activity(self):
        now = self._settle()
        if self.running and not self.blocked and self.focused and not self.rendering:
            self.last_activity = now

    @property
    def pending_count(self):
        return len(self._pending)

    def retry_pending(self):
        while self._pending:
            try:
                self.emit(deepcopy(self._pending[0]))
            except Exception as exc:
                self.last_error = type(exc).__name__
                return
            self._pending.popleft()
        self.last_error = None

    def flush(self, reason="checkpoint"):
        self._settle()
        if len(self._pending) >= 128:
            self.retry_pending()
            if len(self._pending) >= 128:
                self.blocked = True
                self.last_activity = None
                raise BufferError("pending queue full; tracking paused; interval retained")
        self.blocked = False
        ended_at = self.utc_now().isoformat()
        self.sequence += 1
        record = dict(schema_version=1, component="nuke_time_calc",
                      session_id=self.session_id, sequence=self.sequence,
                      identity=self.identity, machine=self.machine, context=dict(self.context),
                      started_at=self.started_at, ended_at=ended_at,
                      comp_seconds=self.comp_seconds,
                      excluded_gap_seconds=self.excluded_gap_seconds, reason=reason)
        self._pending.append(deepcopy(record))
        self.comp_seconds = 0.0
        self.excluded_gap_seconds = 0.0
        self.started_at = ended_at
        self.retry_pending()
        return record


class _SpanCollector(Collector):
    """comp-v1: 20s idle allowance, >60s missing-observation exclusion.

    Durations and endpoints are differences of rounded *absolute* epoch offsets,
    never sums of rounded tick deltas. Contiguous active spans coalesce. UTC is
    fixed per epoch; up to 1s cumulative sample/clock jitter leaves that mapping
    unchanged. Larger corrections, backwards monotonic readings and long gaps
    exclude the uncertain interval and start a new session with fresh activity.
    Excluded-gap timestamps use the old epoch and are uncertainty diagnostics,
    not evidence that the artist was working during that wall interval.

    Automatic seals enforce 256 spans and 24h windows. Queue saturation retains
    at most 128 sealed batches plus one bounded interval and fails closed. Time
    after saturation is not extrapolated: recovery starts a new epoch/session.
    All retained state is memory-only until an accepting durable sink owns it.
    """

    def __init__(self, **kwargs):
        from math import isfinite
        mono = kwargs.get("monotonic", time.monotonic)
        utc = kwargs.get("utc_now", lambda: datetime.now(timezone.utc))

        def checked_monotonic():
            value = mono()
            if type(value) not in (int, float) or not isfinite(value):
                raise ValueError("monotonic must return finite seconds")
            return value

        def checked_utc():
            value = utc()
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise ValueError("utc_now must return an aware datetime")
            return value.astimezone(timezone.utc)

        kwargs.update(monotonic=checked_monotonic, utc_now=checked_utc)
        super().__init__(**kwargs)
        _wire_text(self.machine, 255, "machine")
        self._spans = []
        self._excluded_us = 0
        self._retained_end = None
        self._epoch_tick = self.last_tick
        self._epoch_utc = self.last_utc

    def start(self):
        was_running = self.running
        previous_tick = self.last_tick
        super().start()
        if not was_running:
            expected = datetime.fromisoformat(self._timestamp(self.last_tick))
            if self.sequence and (self.last_tick < previous_tick or abs((self.last_utc - expected).total_seconds()) > 1):
                self.session_id = str(uuid.uuid4())
                self.sequence = 0
            if not self.sequence:
                self._epoch_tick = self.last_tick
                self._epoch_utc = self.last_utc
            self.started_at = self._timestamp(self.last_tick)

    def _timestamp(self, tick):
        from datetime import timedelta
        return (self._epoch_utc + timedelta(
            microseconds=round((tick - self._epoch_tick) * 1_000_000))).isoformat()

    def _settle(self):
        now = self.monotonic()
        wall = self.utc_now()
        if self.blocked:
            self.last_tick, self.last_utc = now, wall
            return now
        if not self.running:
            return self.last_tick
        elapsed = now - self.last_tick
        expected = datetime.fromisoformat(self._timestamp(now))
        if self.running and (elapsed < 0 or elapsed > 60 or abs((wall - expected).total_seconds()) > 1):
            self._seal(self.last_tick, "clock_epoch_end")
            gap = max(0.0, elapsed, (wall - self.last_utc).total_seconds())
            gap_end = self.last_tick + gap
            cursor = self.last_tick
            while cursor < gap_end:
                end = min(gap_end, cursor + 86400)
                self._excluded_us = (round((end - self._epoch_tick) * 1_000_000)
                                     - round((cursor - self._epoch_tick) * 1_000_000))
                self._seal(end, "excluded_gap")
                cursor = end
            self.session_id = str(uuid.uuid4())
            self.sequence = 0
            self._epoch_tick, self._epoch_utc = now, wall
            self.started_at = wall.isoformat()
            self.last_activity = None
        elif self.running and not self.blocked:
            while self.last_tick < now:
                boundary = self._epoch_tick + (datetime.fromisoformat(self.started_at) - self._epoch_utc).total_seconds() + 86400
                end = min(now, boundary)
                self._accumulate(self.last_tick, end)
                self.last_tick = end
                if end == boundary:
                    self._seal(end, "window_limit")
        self.last_tick = now
        self.last_utc = wall
        return now

    def _accumulate(self, start, end):
        if not self.focused or self.rendering or self.last_activity is None:
            return
        end = min(end, self.last_activity + 20.0)
        start_us = round((start - self._epoch_tick) * 1_000_000)
        end_us = round((end - self._epoch_tick) * 1_000_000)
        duration = end_us - start_us
        if duration <= 0:
            return
        start_at, end_at = self._timestamp(start), self._timestamp(end)
        if self._spans and self._spans[-1]["end"] == start_at:
            self._spans[-1]["end"] = end_at
            self._spans[-1]["duration_microseconds"] += duration
        else:
            if len(self._spans) >= 256:
                self._seal(start, "span_limit")
            self._spans.append(dict(start=start_at, end=end_at,
                                    duration_microseconds=duration))
        self.comp_seconds += duration / 1_000_000

    def flush(self, reason="checkpoint"):
        _wire_text(reason, 64, "reason")
        self._settle()
        if self.blocked:
            record = self._seal(self._retained_end, reason)
            self.session_id = str(uuid.uuid4())
            self.sequence = 0
            self._epoch_tick, self._epoch_utc = self.last_tick, self.last_utc
            self.started_at = self.last_utc.isoformat()
            return record
        return self._seal(self.last_tick, reason)

    def _seal(self, tick, reason):
        if len(self._pending) >= 128:
            self.retry_pending()
            if len(self._pending) >= 128:
                self.blocked = True
                self.last_activity = None
                self._retained_end = tick
                raise BufferError("pending queue full; tracking paused; interval retained")
        self.blocked = False
        self._retained_end = None
        if self.sequence == 2**63 - 1:
            self.session_id = str(uuid.uuid4())
            self.sequence = 0
        self.sequence += 1
        ended_at = self._timestamp(tick)
        record = dict(session_id=self.session_id, sequence=self.sequence,
                      machine=self.machine, collector_version="2.0", policy_version="comp-v1",
                      job_id=self.context["job_id"], shot_id=self.context["shot_id"],
                      script_path=self.context["script_path"], reason=reason,
                      started_at=self.started_at, ended_at=ended_at,
                      comp_microseconds=sum(s["duration_microseconds"] for s in self._spans),
                      excluded_gap_microseconds=self._excluded_us, active_spans=deepcopy(self._spans))
        self._pending.append(deepcopy(record))
        self._spans = []
        self._excluded_us = 0
        self.comp_seconds = 0.0
        self.started_at = ended_at
        self.retry_pending()
        return record
