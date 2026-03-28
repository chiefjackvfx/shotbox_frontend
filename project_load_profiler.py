from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import time


WALL_CATEGORY_ORDER = (
    "project_switch_total",
    "job_lookup",
    "timeline_build",
)

WORK_CATEGORY_ORDER = (
    "shot_card_create",
    "task_card_create",
    "thumbnail_load",
    "disk_latest_nk",
    "disk_latest_render_info",
    "disk_latest_preview",
)


@dataclass
class WorkAggregate:
    count: int = 0
    total_seconds: float = 0.0
    requested_count: int = 0

    @property
    def average_seconds(self) -> float:
        if self.count <= 0:
            return 0.0
        return self.total_seconds / self.count


@dataclass
class ProfileSession:
    job_id: int | None
    job_name: str
    started_wall_time: float
    started_perf_counter: float
    wall_categories: dict[str, float] = field(
        default_factory=lambda: {name: 0.0 for name in WALL_CATEGORY_ORDER}
    )
    work_categories: dict[str, WorkAggregate] = field(
        default_factory=lambda: {name: WorkAggregate() for name in WORK_CATEGORY_ORDER}
    )
    async_tokens: dict[int, tuple[str, float]] = field(default_factory=dict)
    next_async_token: int = 1
    status: str = "running"
    reason: str | None = None
    completed_perf_counter: float | None = None
    report_ready: bool = False


class _NullMeasurement:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


class ProjectLoadProfiler:
    def __init__(
        self,
        enabled: bool = False,
        *,
        perf_counter=None,
        wall_time=None,
        printer=None,
    ) -> None:
        self._enabled = bool(enabled)
        self._perf_counter = perf_counter or time.perf_counter
        self._wall_time = wall_time or time.time
        self._printer = printer or print
        self._session: ProfileSession | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    @property
    def session(self) -> ProfileSession | None:
        return self._session

    def has_active_session(self) -> bool:
        return self._session is not None

    def has_pending_report(self) -> bool:
        session = self._session
        return bool(session is not None and session.report_ready)

    def pending_async_count(self) -> int:
        session = self._session
        if session is None:
            return 0
        return len(session.async_tokens)

    def start_session(self, job_id: int | None, job_name: str) -> ProfileSession | None:
        if not self._enabled:
            return None
        self._session = ProfileSession(
            job_id=job_id,
            job_name=str(job_name or ""),
            started_wall_time=self._wall_time(),
            started_perf_counter=self._perf_counter(),
        )
        return self._session

    def discard_session(self) -> None:
        self._session = None

    def measure_wall(self, category: str):
        if not self._enabled or self._session is None:
            return _NullMeasurement()
        return _Measurement(self, category, wall=True)

    def measure_work(self, category: str):
        if not self._enabled or self._session is None:
            return _NullMeasurement()
        return _Measurement(self, category, wall=False)

    def record_wall_duration(self, category: str, elapsed: float) -> None:
        session = self._session
        if not self._enabled or session is None:
            return
        if category not in session.wall_categories:
            session.wall_categories[category] = 0.0
        session.wall_categories[category] += max(0.0, float(elapsed))

    def record_work_duration(self, category: str, elapsed: float, count: int = 1) -> None:
        session = self._session
        if not self._enabled or session is None:
            return
        if category not in session.work_categories:
            session.work_categories[category] = WorkAggregate()
        aggregate = session.work_categories[category]
        aggregate.count += max(0, int(count))
        aggregate.total_seconds += max(0.0, float(elapsed))
        if category != "thumbnail_load":
            aggregate.requested_count = aggregate.count

    def start_async_work(self, category: str) -> int | None:
        session = self._session
        if not self._enabled or session is None:
            return None
        if category not in session.work_categories:
            session.work_categories[category] = WorkAggregate()
        aggregate = session.work_categories[category]
        aggregate.requested_count += 1
        token = session.next_async_token
        session.next_async_token += 1
        session.async_tokens[token] = (category, self._perf_counter())
        return token

    def finish_async_work(self, token: int | None) -> None:
        session = self._session
        if token is None or not self._enabled or session is None:
            return
        started = session.async_tokens.pop(token, None)
        if not started:
            return
        category, started_at = started
        elapsed = max(0.0, self._perf_counter() - started_at)
        self.record_work_duration(category, elapsed)

    def mark_success(self) -> None:
        session = self._session
        if session is None:
            return
        if session.wall_categories.get("project_switch_total", 0.0) <= 0.0:
            session.wall_categories["project_switch_total"] = max(
                0.0, self._perf_counter() - session.started_perf_counter
            )
        session.status = "success"
        session.reason = None
        session.completed_perf_counter = self._perf_counter()
        session.report_ready = True

    def mark_failed(self, reason: str | None = None) -> None:
        session = self._session
        if session is None:
            return
        if session.wall_categories.get("project_switch_total", 0.0) <= 0.0:
            session.wall_categories["project_switch_total"] = max(
                0.0, self._perf_counter() - session.started_perf_counter
            )
        session.status = "failed"
        session.reason = reason
        session.completed_perf_counter = self._perf_counter()
        session.report_ready = True

    def mark_incomplete(self, reason: str | None = None) -> None:
        session = self._session
        if session is None:
            return
        if session.wall_categories.get("project_switch_total", 0.0) <= 0.0:
            session.wall_categories["project_switch_total"] = max(
                0.0, self._perf_counter() - session.started_perf_counter
            )
        session.status = "incomplete"
        session.reason = reason
        session.completed_perf_counter = self._perf_counter()
        session.report_ready = True

    def emit_report_if_ready(self, *, grace_seconds: float = 0.0, force: bool = False) -> bool:
        session = self._session
        if session is None or not session.report_ready:
            return False
        if not force and session.status == "success" and session.async_tokens:
            completed_at = session.completed_perf_counter or self._perf_counter()
            if (self._perf_counter() - completed_at) < max(0.0, float(grace_seconds)):
                return False
        self._emit_report(session)
        self._session = None
        return True

    def _emit_report(self, session: ProfileSession) -> None:
        timestamp = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(session.started_wall_time)
        )
        total_seconds = session.wall_categories.get("project_switch_total", 0.0)
        header = (
            f"[ProjectLoadProfiler] {timestamp} | status={session.status} "
            f"| job={session.job_name or '(untitled)'} | job_id={session.job_id} "
            f"| total={total_seconds:.3f}s"
        )
        if session.reason:
            header += f" | reason={session.reason}"
        self._printer(header)
        self._printer("[ProjectLoadProfiler] Wall-clock phases:")
        for category in WALL_CATEGORY_ORDER:
            seconds = session.wall_categories.get(category, 0.0)
            self._printer(
                f"[ProjectLoadProfiler]   {category}: {seconds:.3f}s"
            )

        self._printer("[ProjectLoadProfiler] Summed work:")
        for category, aggregate in self._sorted_work_items(session):
            line = (
                f"[ProjectLoadProfiler]   {category}: count={aggregate.count} "
                f"total={aggregate.total_seconds:.3f}s avg={aggregate.average_seconds:.3f}s"
            )
            if category == "thumbnail_load" and aggregate.requested_count != aggregate.count:
                line += f" requested={aggregate.requested_count}"
            self._printer(line)

        self._printer(
            "[ProjectLoadProfiler] Note: summed per-item totals can exceed wall-clock "
            "time because some work overlaps asynchronously."
        )

    def _sorted_work_items(self, session: ProfileSession):
        indexed = []
        for idx, category in enumerate(WORK_CATEGORY_ORDER):
            indexed.append((idx, category, session.work_categories.get(category, WorkAggregate())))
        indexed.sort(key=lambda item: (-item[2].total_seconds, item[0]))
        return [(category, aggregate) for _, category, aggregate in indexed]


class _Measurement:
    def __init__(self, profiler: ProjectLoadProfiler, category: str, *, wall: bool) -> None:
        self._profiler = profiler
        self._category = category
        self._wall = wall
        self._started_at = None

    def __enter__(self):
        self._started_at = self._profiler._perf_counter()
        return None

    def __exit__(self, exc_type, exc, tb):
        if self._started_at is None:
            return False
        elapsed = max(0.0, self._profiler._perf_counter() - self._started_at)
        if self._wall:
            self._profiler.record_wall_duration(self._category, elapsed)
        else:
            self._profiler.record_work_duration(self._category, elapsed)
        return False


_INSTALLED_PROFILER: ProjectLoadProfiler | None = None


def install_profiler(profiler: ProjectLoadProfiler | None) -> None:
    global _INSTALLED_PROFILER
    _INSTALLED_PROFILER = profiler


def installed_profiler() -> ProjectLoadProfiler | None:
    return _INSTALLED_PROFILER


@contextmanager
def measure_installed_work(category: str):
    profiler = installed_profiler()
    if profiler is None:
        yield
        return
    with profiler.measure_work(category):
        yield


def start_installed_async_work(category: str) -> int | None:
    profiler = installed_profiler()
    if profiler is None:
        return None
    return profiler.start_async_work(category)


def finish_installed_async_work(token: int | None) -> None:
    profiler = installed_profiler()
    if profiler is None:
        return
    profiler.finish_async_work(token)
