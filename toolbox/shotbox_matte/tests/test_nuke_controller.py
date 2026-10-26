"""Fault-injection unit tests; fake Qt/process objects are not host evidence."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke import controller, node, process, qt_compat
from shotbox_nuke.protocol import EventParser
from shotbox_nuke.state import SessionState


class Knob:
    def __init__(self, value=""):
        self.current = value
        self.enabled = True

    def value(self):
        return self.current

    def setValue(self, value):
        self.current = value

    def setEnabled(self, value):
        self.enabled = value


class Node:
    def __init__(self):
        self.knobs = {"sb_active_manifest": Knob("previous/integration.json"),
                      "sb_uuid": Knob("node"), "sb_force": Knob()}
        self.deleted = False

    def __getitem__(self, key):
        if self.deleted:
            raise ValueError("A PythonObject is not attached to a node")
        return self.knobs[key]


@pytest.fixture
def owned_run(tmp_path, monkeypatch):
    session = SessionState()
    session.active["node"] = "previous/integration.json"
    owner = session.begin("node", fingerprint="snapshot").owner
    for stage in ("capturing", "preflight", "generating", "validating"):
        session.transition(owner, stage)
    run = controller.Run.__new__(controller.Run)
    run.owner = owner
    run.node = Node()
    run.path = tmp_path / "integration.json"
    run.directory = tmp_path
    run.application = tmp_path
    run.python = Path(sys.executable)
    run.child = None
    run.started = 0
    run.cancel_time = None
    run.data = {"state": "validating", "fingerprint": {"digest": "snapshot"}, "outputs": []}
    monkeypatch.setattr(controller, "SESSION", session)
    monkeypatch.setattr(controller, "RUNS", {owner.node_id: run})
    monkeypatch.setattr(node, "set_status", lambda *args: None)
    return run, session


@pytest.mark.parametrize("outcome", ["failed", "cancelled", "complete"])
@pytest.mark.parametrize("fault", ["disk_full", "deleted_node", "status_knob", "force_knob", "unexpected_writer"])
def test_terminal_faults_always_release_ownership_and_keep_previous_cache(owned_run, monkeypatch, outcome, fault):
    run, session = owned_run

    def fail(message):
        raise OSError(message)

    if fault == "disk_full":
        monkeypatch.setattr(run, "write", lambda: fail("No space left on device"))
    elif fault == "deleted_node":
        run.node.deleted = True
    elif fault == "status_knob":
        monkeypatch.setattr(run, "status", lambda message: (_ for _ in ()).throw(RuntimeError("Node deleted")))
    elif fault == "force_knob":
        monkeypatch.setattr(run.node.knobs["sb_force"], "setEnabled",
                            lambda value: (_ for _ in ()).throw(ValueError("Knob deleted")))
    else:
        monkeypatch.setattr(run, "write", lambda: (_ for _ in ()).throw(RuntimeError("Unexpected writer error")))
    if fault == "unexpected_writer":
        with pytest.raises(RuntimeError, match="Unexpected writer error"):
            run.finish(outcome, "terminal fixture")
    else:
        run.finish(outcome, "terminal fixture")
    assert session.current is None
    assert controller.RUNS == {}
    assert session.active["node"] == "previous/integration.json"
    assert run.node.knobs["sb_active_manifest"].value() == "previous/integration.json"
    assert session.history[run.owner].stage == outcome
    assert session.begin("another-node").owner.node_id == "another-node"


def test_activation_error_preserves_complete_attempt_for_reload_and_previous_active(owned_run, monkeypatch):
    run, session = owned_run
    monkeypatch.setattr(run, "ensure_fresh", lambda: None)
    monkeypatch.setattr(node, "activate", lambda *args: (_ for _ in ()).throw(RuntimeError("Read knob install failed")))
    run.validated({"report": {"valid": True, "outputs": [{"path": "matte.0001.exr"}]}}, 0)
    assert run.data["state"] == "complete"
    assert run.data["validation"]["valid"]
    assert "Reload Result" in run.data["message"]
    assert session.current is None and not controller.RUNS
    assert session.active["node"] == "previous/integration.json"
    assert run.node["sb_active_manifest"].value() == "previous/integration.json"


def test_uuid_change_blocks_activation_even_when_session_and_fingerprint_match(owned_run, monkeypatch):
    run, session = owned_run
    run.node["sb_uuid"].setValue("replacement-node")
    inspected = []
    monkeypatch.setattr(controller.freshness, "fingerprint", lambda node: inspected.append(node))
    with pytest.raises(RuntimeError, match="owner changed"):
        run.ensure_fresh()
    assert inspected == []
    assert session.active["node"] == "previous/integration.json"


class Stream:
    def __init__(self, fault=None):
        self.fault = fault
        self.closed = False

    def write(self, chunk):
        if self.fault == "write":
            raise OSError("No space left on device while writing log")

    def flush(self):
        if self.fault == "flush":
            raise OSError("No space left on device while flushing log")

    def close(self):
        self.closed = True
        if self.fault == "close":
            raise OSError("Log close failed")


class OwnedProcess:
    def __init__(self, stdout):
        self.stdout = stdout
        self.stderr = b"diagnostic stream\n"
        self.killed = 0
        self.deleted = 0

    def readAllStandardOutput(self):
        value, self.stdout = self.stdout, b""
        return value

    def readAllStandardError(self):
        value, self.stderr = self.stderr, b""
        return value

    def kill(self):
        self.killed += 1

    def deleteLater(self):
        self.deleted += 1


@pytest.fixture
def child(monkeypatch):
    adapter = SimpleNamespace(QProcess=SimpleNamespace(ExitStatus=SimpleNamespace(NormalExit="normal")))
    monkeypatch.setattr(qt_compat, "qt_core", lambda: adapter)
    value = process.ChildProcess.__new__(process.ChildProcess)
    value.process = OwnedProcess(b'{"type":"probe","protocol_version":1,"report":{"ready":true}}\n')
    value.parser = EventParser(expected_terminal="probe")
    value.stdout, value.stderr = Stream(), Stream()
    value.closed = False
    value.failure = None
    value.progress = lambda event: None
    value.terminals = []
    value.done = lambda event, error, code: value.terminals.append((event, error, code))
    return value


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
@pytest.mark.parametrize("fault", ["write", "flush", "close"])
def test_log_io_failure_reports_one_terminal_and_closes_owned_resources(child, stream_name, fault):
    getattr(child, stream_name).fault = fault
    getattr(child, "_" + stream_name)()
    if fault != "close":
        assert child.process.killed >= 1
    child._finished(1, "normal")
    assert len(child.terminals) == 1
    terminal, error, code = child.terminals[0]
    assert terminal is None and error and code == 1
    assert child.closed and child.stdout.closed and child.stderr.closed
    assert child.process.deleted == 1
    child._finished(1, "normal")
    assert len(child.terminals) == 1 and child.process.deleted == 1


@pytest.mark.parametrize("failed_stream", [False, True])
def test_completion_callback_exception_is_not_delivered_twice(child, failed_stream):
    calls = []
    if failed_stream:
        child.stdout.fault = "write"

    def callback(event, error, code):
        calls.append((event, error, code))
        raise RuntimeError("User completion callback failed")

    child.done = callback
    with pytest.raises(RuntimeError, match="completion callback failed"):
        child._finished(0, "normal")
    assert len(calls) == 1
    assert (calls[0][0] is None) == failed_stream
    assert bool(calls[0][1]) == failed_stream
    assert child.process.deleted == 1 and child.stdout.closed and child.stderr.closed
    child._finished(0, "normal")
    assert len(calls) == 1


def test_malformed_child_protocol_kills_owned_child_without_success_callback(child):
    child.process.stdout = b'{"type":"probe","protocol_version":99,"report":{}}\n'
    child._stdout()
    assert child.process.killed == 1
    child._finished(0, "normal")
    assert len(child.terminals) == 1 and child.terminals[0][0] is None
    assert "protocol" in child.terminals[0][1].lower()


@pytest.mark.parametrize('native_cancel', [True, False])
def test_capture_abort_releases_worker_lock_and_preserves_previous_result(owned_run, monkeypatch, native_cancel):
    run, session = owned_run

    class RenderCancelled(RuntimeError):
        pass

    monkeypatch.setitem(sys.modules, 'nuke', SimpleNamespace(CancelledError=RenderCancelled))
    run.data['state'] = 'capturing'

    class Child:
        def __init__(self, python, application, directory, arguments, terminal, progress, done):
            self.done = done

        def start(self):
            self.done({'report': {}}, None, 0)

    monkeypatch.setattr(process, 'ChildProcess', Child)
    error = RenderCancelled('User aborted capture') if native_cancel else RuntimeError('Capture decoder failed')

    def capture_failed(event, code):
        raise error

    run.launch([], 'probe', capture_failed)
    assert run.data['state'] == ('cancelled' if native_cancel else 'failed')
    assert session.current is None and not controller.RUNS
    assert session.active['node'] == 'previous/integration.json'
    assert run.node['sb_active_manifest'].value() == 'previous/integration.json'
