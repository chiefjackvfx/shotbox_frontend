"""Immutable job snapshots and the sequential desktop queue's state."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any
import uuid


@dataclass
class QueueJob:
    """Keep options private JSON so subsequent UI/nested-dictionary edits cannot leak."""

    snapshot: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "pending"
    message: str = ""
    output_dir: str = ""

    @classmethod
    def capture(cls, options: dict[str, Any]) -> QueueJob:
        return cls(json.dumps(options, allow_nan=False))

    @property
    def options(self) -> dict[str, Any]:
        return json.loads(self.snapshot)


class JobQueue:
    def __init__(self) -> None:
        self.jobs: list[QueueJob] = []
        self.running = False
        self.active_id: str | None = None

    def add(self, options: dict[str, Any]) -> QueueJob:
        job = QueueJob.capture(options)
        self.jobs.append(job)
        return job

    def next(self) -> QueueJob | None:
        if not self.running or self.active_id is not None:
            return None
        job = next((entry for entry in self.jobs if entry.status == "pending"), None)
        if job is None:
            self.running = False
            return None
        job.status = "running"
        self.active_id = job.id
        return job

    def finish(self, status: str, message: str = "", output_dir: str = "") -> None:
        if status not in {"complete", "failed", "cancelled"}:
            raise ValueError("Queue jobs finish as complete, failed, or cancelled.")
        job = next((entry for entry in self.jobs if entry.id == self.active_id), None)
        if job is not None:
            job.status, job.message, job.output_dir = status, message, output_dir
        self.active_id = None
        if status == "cancelled":
            self.running = False

    def pause(self) -> None:
        self.running = False

    def remove_pending(self, ids: set[str]) -> None:
        self.jobs[:] = [job for job in self.jobs if job.id not in ids or job.status != "pending"]

    def clear_finished(self) -> None:
        self.jobs[:] = [job for job in self.jobs if job.status in {"pending", "running"}]
