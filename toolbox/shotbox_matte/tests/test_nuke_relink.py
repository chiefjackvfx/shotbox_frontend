"""Relink transactions with synthetic image files and fake host graph objects."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
import shotbox_nuke
from shotbox_nuke import cache, node as node_module, preferences, readback, relink, render
from shotbox_nuke.protocol import ProtocolError
import test_nuke_published_cache as published_tests

completed = published_tests.completed
project_precomp = published_tests.project_precomp
published_receipt = published_tests.published_receipt


class Knob:
    def __init__(self, value):
        self.saved = value

    def value(self):
        return self.saved

    def setValue(self, value):
        self.saved = value

    def toScript(self):
        return json.dumps(self.saved)

    def fromScript(self, script):
        self.saved = json.loads(script)


class Node:
    def __init__(self, name, **knobs):
        self.name = name
        self.values = {key: Knob(value) for key, value in knobs.items()}
        self.children = []
        self.connections = []

    def __getitem__(self, key):
        return self.values[key]

    def fullName(self):
        return self.name

    def knob(self, name):
        return self.values.get(name)

    def allKnobs(self):
        return list(self.values.values())

    def nodes(self):
        return self.children

    def inputs(self):
        return len(self.connections)

    def input(self, index):
        return self.connections[index]

    def setInput(self, index, node):
        self.connections[index] = node


@pytest.fixture
def moved(published_receipt, tmp_path, monkeypatch):
    data = json.loads(published_receipt.read_text())
    data["precomp"].update(kind="alpha", precision="half", channels=["rgba.alpha"])
    data["settings"]["precision"] = "half"
    data["capture"].pop("source_files")
    for record in data["precomp"]["files"]:
        record.update(channels=["rgba.alpha"], box=[0, 0, 32, 16])
    cache.atomic_write_json(published_receipt, data)
    previous = Path(data["storage"]["output_directory"])
    destination = tmp_path / "artist moved EXRs"
    destination.mkdir()
    for frame, record in zip(range(-2, 1), data["precomp"]["files"]):
        (previous / record["path"]).rename(destination / ("renamed_matte.%04d.exr" % frame))
    unrelated = destination / "unrelated_bg.1001.exr"
    unrelated.write_bytes(b"unrelated plate in shared folder")
    group = Node("ShotBoxMatte1", sb_uuid=str(uuid.uuid4()), sb_active_manifest=str(published_receipt),
                 sb_pending_manifest=str(tmp_path / "failed latest/integration.json"),
                 sb_precomp_path=str(previous / data["precomp"]["pattern"]), sb_result_kind="alpha", sb_status="ready")
    internal = Node("ShotBoxMatte1.SBPrecomp", file=group["sb_precomp_path"].value(), which=1)
    group.children = [internal]
    owned = Node("ArtistRenamedRead", file=str(previous / data["precomp"]["pattern"]),
                 sb_read_owner=group["sb_uuid"].value(), sb_read_manifest=str(published_receipt),
                 sb_read_pattern=str(previous / data["precomp"]["pattern"]), xpos=480, ypos=960)
    consumer = Node("Merge")
    consumer.connections = [group]
    peers = [group, owned, consumer]
    state = SimpleNamespace(receipt=published_receipt, original=published_receipt.read_bytes(), data=data,
                            group=group, internal=internal, owned=owned, consumer=consumer, peers=peers,
                            destination=destination, cache_root=tmp_path / "private cache", decode=[],
                            activation_error=None, install_error=None, decode_error=None, undo=[], undo_disabled=False)
    monkeypatch.setattr(preferences, "load", lambda: {"cache_root": str(state.cache_root), "application_root": ""})
    monkeypatch.setattr(readback, "_parent", lambda node: None)
    monkeypatch.setattr(readback, "_nodes", lambda parent: state.peers)
    monkeypatch.setattr(readback, "find", lambda node: state.owned)
    undo = SimpleNamespace(disabled=lambda: state.undo_disabled,
                           begin=lambda label: state.undo.append(("begin", label)),
                           end=lambda: state.undo.append(("end", "")))
    monkeypatch.setitem(sys.modules, "nuke", SimpleNamespace(delete=lambda node: state.peers.remove(node), Undo=undo))
    def decode(report):
        assert state.undo == []
        state.decode.append(report)
        if state.decode_error:
            raise RuntimeError(state.decode_error)
    def activate(node, path):
        report = cache.validate_cache(path)
        node["sb_active_manifest"].setValue(str(path))
        node["sb_precomp_path"].setValue(str(Path(report["precomp_root"]) / report["manifest"]["precomp"]["pattern"]))
        node["sb_status"].setValue("relinked")
        state.internal["file"].setValue(node["sb_precomp_path"].value())
        if state.activation_error:
            raise RuntimeError(state.activation_error)
    def install(node, path):
        state.owned["file"].setValue(node["sb_precomp_path"].value())
        state.owned["sb_read_manifest"].setValue(str(path))
        state.owned["sb_read_pattern"].setValue(state.owned["file"].value())
        state.consumer.setInput(0, state.owned)
        if state.install_error:
            raise RuntimeError(state.install_error)
    monkeypatch.setattr(render, "decode_outputs", decode)
    monkeypatch.setattr(node_module, "activate", activate)
    monkeypatch.setattr(readback, "install", install)
    return state


def _graph(state):
    return [[knob.toScript() for knob in current.allKnobs()]
            for current in (state.group, state.internal, state.owned)], state.consumer.input(0)


@pytest.mark.parametrize("selection", ["renamed_matte.-002.exr", "renamed_matte.%04d.exr", "renamed_matte.####.exr",
                                      "renamed_matte.%04d.exr -2-0", "renamed_matte.####.exr -2-0x1"])
def test_moved_and_renamed_sequence_creates_private_receipt_preserving_shared_source(moved, selection):
    original_hashes = {path.name: cache.sha256_file(path) for path in moved.destination.iterdir()}
    pending = moved.group["sb_pending_manifest"].value()
    result = relink.relink(moved.group, str(moved.destination / selection))
    receipt = Path(result["manifest_path"])
    assert receipt != moved.receipt and moved.cache_root in receipt.parents
    assert moved.receipt.read_bytes() == moved.original
    assert moved.group["sb_active_manifest"].value() == str(receipt)
    assert moved.group["sb_pending_manifest"].value() == pending
    assert result["manifest"]["precomp"]["pattern"] == "renamed_matte.%04d.exr"
    assert result["manifest"]["capture"] == moved.data["capture"]
    assert result["manifest"]["result"] == moved.data["result"]
    assert [record["sha256"] for record in result["manifest"]["precomp"]["files"]] == [
        record["sha256"] for record in moved.data["precomp"]["files"]]
    assert moved.owned["file"].value() == str(moved.destination / "renamed_matte.%04d.exr")
    assert moved.owned["xpos"].value() == 480 and moved.owned["ypos"].value() == 960
    assert moved.consumer.input(0) is moved.owned
    assert {path.name: cache.sha256_file(path) for path in moved.destination.iterdir()} == original_hashes
    assert moved.decode and moved.decode[0]["manifest"]["precomp"]["channels"] == ["rgba.alpha"]
    assert moved.undo == [("begin", "Relink ShotBox matte"), ("end", "")]


@pytest.mark.parametrize("damage", ["same_size_corrupt", "missing", "extra_frame", "wrong_padding"])
def test_wrong_sequence_rejected_before_any_private_write_or_graph_change(moved, damage):
    path = moved.destination / "renamed_matte.-002.exr"
    if damage == "same_size_corrupt":
        path.write_bytes(b"x" * path.stat().st_size)
    elif damage == "missing":
        path.unlink()
    elif damage == "extra_frame":
        (moved.destination / "renamed_matte.0001.exr").write_bytes(b"extra matching frame")
    else:
        path.rename(moved.destination / "renamed_matte.-02.exr")
    before = _graph(moved)
    with pytest.raises(ProtocolError):
        relink.relink(moved.group, str(moved.destination / "renamed_matte.%04d.exr"))
    assert _graph(moved) == before
    assert moved.receipt.read_bytes() == moved.original
    assert not moved.cache_root.exists()
    assert moved.decode == []
    assert moved.undo == []


@pytest.mark.parametrize("failure", ["activation_error", "install_error"])
def test_install_failure_restores_group_internal_read_external_read_and_connections(moved, failure):
    setattr(moved, failure, "injected install failure")
    before = _graph(moved)
    with pytest.raises(RuntimeError, match="injected"):
        relink.relink(moved.group, str(moved.destination / "renamed_matte.-002.exr"))
    assert _graph(moved) == before
    assert list(moved.cache_root.rglob("integration.json")) == []
    assert moved.receipt.read_bytes() == moved.original
    assert moved.undo == [("begin", "Relink ShotBox matte"), ("end", "")]


def test_decoder_failure_preserves_graph_without_writing_receipt(moved):
    moved.decode_error = "decoded wrong channels or geometry"
    before = _graph(moved)
    with pytest.raises(RuntimeError, match="decoded wrong"):
        relink.relink(moved.group, str(moved.destination / "renamed_matte.-002.exr"))
    assert _graph(moved) == before
    assert not moved.cache_root.exists()
    assert moved.undo == []


def test_existing_disabled_undo_state_is_preserved(moved):
    moved.undo_disabled = True
    result = relink.relink(moved.group, str(moved.destination / "renamed_matte.####.exr"))
    assert result["valid"]
    assert moved.undo == [] and moved.undo_disabled is True
    assert Path(result["manifest_path"]).is_file()


def test_new_owned_read_is_removed_if_install_fails_after_creation(moved, monkeypatch):
    previous = _graph(moved)
    existing = moved.owned
    moved.owned = None
    created = Node("NewShotBoxRead", file="new.exr", sb_read_owner=moved.group["sb_uuid"].value())
    def fail_install(node, path):
        moved.peers.append(created)
        moved.consumer.setInput(0, created)
        raise RuntimeError("injected after Read creation")
    monkeypatch.setattr(readback, "install", fail_install)
    with pytest.raises(RuntimeError, match="after Read creation"):
        relink.relink(moved.group, str(moved.destination / "renamed_matte.####.exr"))
    moved.owned = existing
    assert _graph(moved) == previous
    assert created not in moved.peers and existing in moved.peers
    assert list(moved.cache_root.rglob("integration.json")) == []


def test_symlink_frame_escape_is_rejected_before_writing_private_receipt(moved, tmp_path):
    frame = moved.destination / "renamed_matte.-002.exr"
    outside = tmp_path / "outside.exr"
    outside.write_bytes(frame.read_bytes())
    frame.unlink()
    frame.symlink_to(outside)
    before = _graph(moved)
    with pytest.raises(ProtocolError, match="escapes"):
        relink.relink(moved.group, str(moved.destination / "renamed_matte.####.exr"))
    assert _graph(moved) == before
    assert not moved.cache_root.exists()


def test_pending_receipt_used_only_when_there_is_no_active_receipt(moved):
    moved.group["sb_active_manifest"].setValue("")
    moved.group["sb_pending_manifest"].setValue(str(moved.receipt))
    assert relink.relink(moved.group, str(moved.destination / "renamed_matte.####.exr"))["valid"]


def test_explicit_manifest_overrides_node_receipt_selection(moved):
    moved.group["sb_active_manifest"].setValue("/nonexistent/other_receipt.json")
    assert relink.relink(moved.group, str(moved.destination / "renamed_matte.####.exr"), moved.receipt)["valid"]


@pytest.mark.parametrize("selection", ["not_a_sequence.exr", "matte.%08d.exr", "matte.####.####.exr",
                                      "renamed_matte.0001.exr", "matte.png"])
def test_bad_selection_preserves_existing_reference(moved, selection):
    before = _graph(moved)
    with pytest.raises((ValueError, ProtocolError)):
        relink.relink(moved.group, str(moved.destination / selection))
    assert _graph(moved) == before
    assert not moved.cache_root.exists()


def test_json_selection_delegates_to_existing_reload_result(moved, monkeypatch):
    calls = []
    expected = object()
    monkeypatch.setattr(shotbox_nuke, "reload_result", lambda *args: calls.append(args) or expected)
    assert relink.relink(moved.group, str(moved.receipt)) is expected
    assert calls == [(moved.group, str(moved.receipt))]


def test_relink_does_not_mutate_loaded_manifest_object(moved, monkeypatch):
    original = deepcopy(moved.data)
    reader = cache.read_manifest
    monkeypatch.setattr(cache, "read_manifest", lambda path, **kwargs:
                        moved.data if Path(path) == moved.receipt else reader(path, **kwargs))
    relink.relink(moved.group, str(moved.destination / "renamed_matte.####.exr"))
    assert moved.data == original
