"""Pure host contracts deliberately do not import Nuke or Qt."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins" / "nuke"))
from shotbox_nuke.protocol import EventParser, FrameMap, ProtocolError, generation_size


def result_event():
    return {"type": "result", "result": {"status": "complete", "frame_count": 3,
            **dict.fromkeys(("output_dir", "first_source", "first_output", "source_preview",
                            "matte_preview", "manifest", "job_dir"), "/owned/path")}}


@pytest.mark.parametrize("start,end", [(1001, 1003), (-2, 0), (0, 0), (-249, 0), (1001, 1278), (-5000, 5000)])
def test_frame_map_endpoints_and_boundaries(start, end):
    mapping = FrameMap(start, end)
    assert mapping.cache_index(start) == 1
    assert mapping.cache_index(end) == mapping.count
    assert mapping.nuke_frame(1) == start
    assert mapping.nuke_frame(mapping.count) == end
    for frame in (start - 1, end + 1):
        with pytest.raises(ProtocolError):
            mapping.cache_index(frame)
    for index in (0, mapping.count + 1):
        with pytest.raises(ProtocolError):
            mapping.nuke_frame(index)


@pytest.mark.parametrize("start,end", [(0, -1), (1, 0), (True, 5), (1.0, 3)])
def test_invalid_maps(start, end):
    with pytest.raises(ProtocolError):
        FrameMap(start, end)


def test_split_utf8_multiple_lines_unknown_stage_and_unterminated_result():
    progress = {"type": "progress", "stage": "future-stage", "current": 1, "total": 3, "message": "café 漢字"}
    raw = (json.dumps(progress, ensure_ascii=False) + "\n" + json.dumps(result_event())).encode()
    parser = EventParser()
    events = []
    for byte in raw:
        events.extend(parser.feed(bytes([byte])))
    assert events == [progress]
    assert parser.finish(0) == result_event()
    parser = EventParser()
    assert len(parser.feed(raw + b"\n")) == 2
    assert parser.finish(0) == result_event()


@pytest.mark.parametrize("raw", [b'noise from stderr\n', b'{"type":"result","result":{}}\n',
                                 b'{"type":"progress","stage":"x","current":NaN}\n',
                                 b'{"type":"result","type":"progress","stage":"x"}\n', b'\xff\n'])
def test_malformed_stdout_fails_safely(raw):
    with pytest.raises(ProtocolError):
        EventParser().feed(raw)


def test_bound_and_incomplete_utf8():
    parser = EventParser(max_line_bytes=16)
    with pytest.raises(ProtocolError, match="byte limit"):
        parser.feed(b"x" * 17)
    with pytest.raises(ProtocolError):
        parser.finish(0)
    parser = EventParser()
    parser.feed(b'\xe2')
    with pytest.raises(ProtocolError, match="final"):
        parser.finish(0)


@pytest.mark.parametrize("exit_code,normal", [(1, True), (130, True), (-9, False), (0, False)])
def test_successful_result_does_not_override_exit_failure(exit_code, normal):
    parser = EventParser()
    parser.feed(json.dumps(result_event()).encode())
    with pytest.raises(ProtocolError):
        parser.finish(exit_code, normal)


def test_missing_result_and_duplicate_terminal():
    with pytest.raises(ProtocolError, match="without"):
        EventParser().finish(0)
    parser = EventParser()
    with pytest.raises(ProtocolError, match="Multiple"):
        parser.feed((json.dumps(result_event()) + "\n") .encode() * 2)


@pytest.mark.parametrize("kind", ["probe", "plan", "validation"])
def test_bridge_versions_and_terminal_types(kind):
    event = {"type": kind, "protocol_version": 1, "report": {"valid": True}, "plan": {}, "capture": {}}
    parser = EventParser(kind)
    parser.feed(json.dumps(event).encode())
    assert parser.finish(0)["type"] == kind
    event["protocol_version"] = 2
    with pytest.raises(ProtocolError):
        parser = EventParser(kind)
        parser.feed(json.dumps(event).encode() + b"\n")


@pytest.mark.parametrize("width,height,target", [(1920, 1080, 544), (1080, 1920, 544), (512, 512, 32),
                                                (10000, 1, 1088), (1, 10000, 544), (3840, 2160, 1088)])
def test_generation_dimensions_match_backend(width, height, target):
    from shotbox_matte.imaging import spatial_plan
    plan = spatial_plan(width, height, 1920, target)
    assert generation_size(width, height, target) == (max(32, plan.resized_width), max(32, plan.resized_height))


def test_import_boundaries_in_fresh_interpreter():
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from shotbox_nuke import protocol, cache, state, preferences
for name in sys.modules:
    assert not name.startswith(('shotbox_matte', 'PyQt', 'PySide', 'torch', 'nuke', 'OpenImageIO')), name
"""
    subprocess.run([sys.executable, "-c", script, str(root.parents[1] / "plugins/nuke")], check=True)
