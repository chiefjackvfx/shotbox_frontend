"""Pure session lock, attempt stages and asynchronous ownership guards."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import uuid

STAGES = ("probing", "capturing", "preflight", "generating", "validating")
TERMINAL = ("complete", "failed", "cancelled")


class StateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Owner:
    session_token: str
    node_id: str
    attempt_id: str


@dataclass
class Attempt:
    owner: Owner
    fingerprint: str
    stage: str = "probing"
    cancel_requested: bool = False
    error: str = ""
    result: object = None
    activated: bool = False


class SessionState:
    """Exactly one live attempt per script session, separate from active caches."""

    def __init__(self, session_token: str | None = None):
        self.session_token = session_token or str(uuid.uuid4())
        self.current: Attempt | None = None
        self.active: dict[str, object] = {}
        self.history: dict[Owner, Attempt] = {}
        self._invalid_nodes: set[str] = set()
        self._closed = False
        self._lock = threading.RLock()

    def begin(self, node_id: str, attempt_id: str | None = None, fingerprint: str = "") -> Attempt:
        with self._lock:
            if self._closed or node_id in self._invalid_nodes:
                raise StateError("Script session or node is no longer live.")
            if self.current is not None:
                raise StateError("A ShotBox attempt is already running in this session.")
            owner = Owner(self.session_token, node_id, attempt_id or str(uuid.uuid4()))
            if owner in self.history:
                raise StateError("Attempt identity was already used.")
            attempt = Attempt(owner, fingerprint)
            self.current = attempt
            self.history[owner] = attempt
            return attempt

    def owns(self, owner: Owner) -> bool:
        with self._lock:
            return (not self._closed and owner.session_token == self.session_token
                    and owner.node_id not in self._invalid_nodes
                    and self.current is not None and self.current.owner == owner)

    def transition(self, owner: Owner, stage: str) -> None:
        with self._lock:
            if not self.owns(owner):
                raise StateError("Attempt no longer owns this session.")
            current = self.current
            if stage not in STAGES or STAGES.index(stage) != STAGES.index(current.stage) + 1:
                raise StateError(f"Invalid attempt transition {current.stage} -> {stage}.")
            current.stage = stage

    def request_cancel(self, owner: Owner) -> bool:
        with self._lock:
            if not self.owns(owner):
                return False
            self.current.cancel_requested = True
            return True

    def may_activate(self, owner: Owner, fingerprint: str) -> bool:
        with self._lock:
            return (self.owns(owner) and self.current.stage == "validating"
                    and bool(fingerprint) and fingerprint == self.current.fingerprint)

    def activate(self, owner: Owner, cache_ref: object, fingerprint: str) -> None:
        with self._lock:
            if not self.may_activate(owner, fingerprint):
                raise StateError("Owner or input changed; completed cache cannot automatically activate.")
            # Host performs its guarded node update before committing this reference.
            self.active[owner.node_id] = cache_ref
            self.current.activated = True

    def finish(self, owner: Owner, outcome: str, *, error: str = "", result=None) -> bool:
        with self._lock:
            if not self.owns(owner):
                return False
            if outcome not in TERMINAL or (outcome == "complete" and self.current.stage != "validating"):
                raise StateError("Invalid terminal attempt state.")
            self.current.stage, self.current.error, self.current.result = outcome, error, result
            self.current = None
            return True

    def invalidate_node(self, node_id: str) -> Owner | None:
        with self._lock:
            self._invalid_nodes.add(node_id)
            if self.current is not None and self.current.owner.node_id == node_id:
                owner = self.current.owner
                self.current.stage, self.current.error = "cancelled", "Node deleted or ownership invalidated."
                self.current = None
                return owner
            return None

    def close(self) -> Owner | None:
        with self._lock:
            owner = self.current.owner if self.current is not None else None
            if owner is not None:
                self.invalidate_node(owner.node_id)
            self._closed = True
            return owner


def recover_attempt_state(state: str) -> str:
    """Opening a saved script never resumes an owned process."""
    return "failed" if state in STAGES else state
