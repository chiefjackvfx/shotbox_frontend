"""Pure ownership failure matrix; host callbacks have separate Nuke tests."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke.state import Owner, SessionState, StateError, recover_attempt_state


def ready(session, node="node", fingerprint="fingerprint"):
    attempt = session.begin(node, fingerprint=fingerprint)
    for stage in ("capturing", "preflight", "generating", "validating"):
        session.transition(attempt.owner, stage)
    return attempt.owner


def test_single_session_lock_rejects_duplicate_and_other_node():
    state = SessionState()
    first = state.begin("first")
    for node in ("first", "second"):
        with pytest.raises(StateError, match="already running"):
            state.begin(node)
    assert state.finish(first.owner, "failed")
    state.begin("second")


@pytest.mark.parametrize("stage", ["probing", "capturing", "preflight", "generating", "validating"])
@pytest.mark.parametrize("outcome", ["failed", "cancelled"])
def test_every_failure_releases_lock_without_changing_active_cache(stage, outcome):
    state = SessionState()
    state.active["node"] = "previous"
    attempt = state.begin("node")
    for next_stage in ("capturing", "preflight", "generating", "validating"):
        if state.current.stage == stage:
            break
        state.transition(attempt.owner, next_stage)
    assert state.finish(attempt.owner, outcome)
    assert state.active["node"] == "previous"
    state.begin("other")


def test_cancel_after_commit_can_activate_success():
    state = SessionState()
    owner = ready(state)
    assert state.request_cancel(owner)
    state.activate(owner, "new cache", "fingerprint")
    assert state.finish(owner, "complete", result={"status": "complete"})
    assert state.active["node"] == "new cache"


def test_stale_input_and_wrong_session_owner_never_activate():
    state = SessionState()
    owner = ready(state)
    for candidate, fingerprint in [(owner, "changed"), (Owner("other", owner.node_id, owner.attempt_id), "fingerprint")]:
        assert not state.may_activate(candidate, fingerprint)
        with pytest.raises(StateError):
            state.activate(candidate, "bad cache", fingerprint)
    assert state.finish(owner, "complete")
    assert not state.active


def test_deleted_node_and_late_callback_cannot_take_new_attempt_lock():
    state = SessionState()
    owner = ready(state)
    assert state.invalidate_node(owner.node_id) == owner
    replacement = state.begin("copied-node")
    assert not state.finish(owner, "complete")
    assert state.current.owner == replacement.owner
    assert not state.may_activate(owner, "fingerprint")


def test_close_and_reopen_invalidate_old_owners():
    old = SessionState()
    owner = ready(old)
    assert old.close() == owner
    assert old.close() is None
    with pytest.raises(StateError):
        old.begin("node")
    reopened = SessionState()
    ready(reopened)
    assert not reopened.owns(owner)
    for stage in ("probing", "capturing", "preflight", "generating", "validating"):
        assert recover_attempt_state(stage) == "failed"
    assert recover_attempt_state("complete") == "complete"


def test_skipped_transition_and_early_completion_rejected():
    state = SessionState()
    attempt = state.begin("node")
    with pytest.raises(StateError):
        state.transition(attempt.owner, "generating")
    with pytest.raises(StateError):
        state.finish(attempt.owner, "complete")
