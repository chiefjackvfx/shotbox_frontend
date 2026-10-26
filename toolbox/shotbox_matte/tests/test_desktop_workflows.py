"""Queue/preset/timing behavior independent of Qt and native inference."""
import math
from datetime import datetime
import json

import pytest

from shotbox_matte.diagnostics import log_level
from shotbox_matte.presets import validated_preset, validated_presets
from shotbox_matte.queueing import JobQueue
from shotbox_matte.timing import JobHistory, StageTimer, duration


def test_queue_captures_nested_options_and_individual_settings():
    queue = JobQueue()
    options = {"input_path": "shotA", "suffix": "_first", "models": {"video_vae": "a"}}
    first = queue.add(options)
    options["suffix"], options["models"]["video_vae"] = "_second", "b"
    second = queue.add(options)
    first.options["models"]["video_vae"] = "attempted mutation"
    assert first.options["suffix"] == "_first"
    assert first.options["models"]["video_vae"] == "a"
    assert second.options["suffix"] == "_second"
    assert second.options["models"]["video_vae"] == "b"


def test_queue_sequential_pause_failure_and_cancellation():
    queue = JobQueue()
    first, second, third = [queue.add({"input_path": str(i)}) for i in range(3)]
    assert queue.next() is None
    queue.running = True
    assert queue.next() is first
    assert queue.next() is None
    queue.pause()
    queue.finish("complete", output_dir="resultA")
    assert queue.next() is None and first.output_dir == "resultA"
    queue.running = True
    assert queue.next() is second
    queue.finish("failed", "worker error")
    assert queue.next() is third
    queue.finish("cancelled")
    assert not queue.running and queue.active_id is None
    assert [job.status for job in queue.jobs] == ["complete", "failed", "cancelled"]


def test_only_pending_can_be_removed_and_only_finished_cleared():
    queue = JobQueue()
    running, pending = queue.add({}), queue.add({})
    queue.running = True
    queue.next()
    queue.remove_pending({running.id, pending.id})
    assert queue.jobs == [running]
    queue.clear_finished()
    assert queue.jobs == [running]
    queue.finish("complete")
    queue.clear_finished()
    assert not queue.jobs
    with pytest.raises(ValueError):
        queue.finish("unknown")


def test_presets_exclude_input_model_paths_and_legacy_prompt():
    clean = validated_preset({
        "input_path": "source", "sequence": "seq", "models": {"video_vae": "model"},
        "model_root": "external", "portable_dir": "old", "prompt": "legacy conditioning",
        "fps": 23.976, "source_start": 1001, "source_end": 1009,
        "first_frame": None, "suffix": "_alpha", "low_vram": True,
    })
    assert clean == {"fps": 23.976, "source_start": 1001,
                     "source_end": 1009, "first_frame": None, "suffix": "_alpha", "low_vram": True}


@pytest.mark.parametrize("value", [None, [], "bad", 3])
def test_malformed_preset_containers_are_ignored(value):
    assert validated_preset(value) == {}
    assert validated_presets(value) == {}


def test_malformed_preset_fields_do_not_break_settings_or_enable_invalid_ranges():
    clean = validated_preset({"fps": math.nan, "padding": 12, "generation_width": 8000,
                              "first_frame": -1, "precision": ["half"], "source_color": "invalid",
                              "source_start": 10, "source_end": 2, "suffix": "../bad", "low_vram": "false",
                              "frame_skip": True, "prompt": {"bad": 1}, "seed": 12})
    assert clean == {"seed": 12}
    assert validated_presets({"valid": {"seed": 7, "prompt": "legacy"}, "bad": [], "": {}}) == {"valid": {"seed": 7}}


def test_timer_estimates_only_from_observed_progress_and_counts_stage_segments():
    now = [0.0]
    timer = StageTimer(lambda: now[0])
    timer.start()
    timer.observe("prepare", 0, 10)
    now[0] = 2
    assert "ETA unknown" in timer.text()
    timer.observe("prepare", 2, 10)
    assert "Stage ETA ~0:08" in timer.text()
    timer.observe("prepare", 1, 10)
    assert timer.current == 2
    now[0] = 5
    timer.observe("inference", 0, 8)
    assert "ETA unknown" in timer.text()
    assert timer.stage_totals == {"prepare": 5}
    now[0] = 9
    timer.observe("inference", 2, 8)
    assert "Stage ETA ~0:12" in timer.text()
    now[0] = 13
    timer.finish()
    assert timer.text() == "Elapsed 0:13"
    assert timer.stage_totals == {"prepare": 5, "inference": 8}
    assert duration(3601) == "1:00:01"


def test_timer_retains_samples_when_streaming_decode_and_export_alternate():
    now = [0.0]
    timer = StageTimer(lambda: now[0])
    timer.start()
    timer.observe("decode", 0, 10)
    now[0] = 2
    timer.observe("decode", 1, 10)
    timer.observe("export", 1, 10)
    now[0] = 3
    timer.observe("decode", 2, 10)
    assert "Stage ETA ~0:12" in timer.text()
    now[0] = 5
    timer.observe("export", 2, 10)
    assert "Stage ETA ~0:24" in timer.text()
    now[0] = 6
    timer.finish()
    assert timer.stage_totals == {"decode": 4, "export": 2}


def test_whole_job_history_learns_persists_and_includes_future_decode():
    now = [0.0]
    options = {"generation_width": 960, "seed": 42, "input_path": "shotA"}
    timer = StageTimer(lambda: now[0])
    history = JobHistory()
    timer.start()
    history.start(options)
    timer.observe("prepare", 0, 5)
    history.observe("prepare", 0, 5)
    assert history.text(timer) == "Estimated finish unknown (learning)"
    assert not history.record_success(timer)
    # Measured successful job:5s preparation,10s inference,18sdecode,5sexport.
    for stamp, stage, current, total in [(5, "inference", 0, 8), (15, "decode", 0, 9),
                                          (33, "export", 0, 5)]:
        now[0] = stamp
        timer.observe(stage, current, total)
        history.observe(stage, current, total)
    now[0] = 38
    timer.finish()
    assert history.record_success(timer)
    stored = json.loads(json.dumps(history.records))
    reloaded = JobHistory(stored)
    # Inputs/seeds differ but compatible compute/export settings use history.
    reloaded.start({"generation_width": 960, "seed": 17, "input_path": "shotB"})
    now[0] = 100
    timer.start()
    timer.observe("prepare", 0, 5)
    reloaded.observe("prepare", 0, 5)
    assert reloaded.remaining(timer) == 38
    assert reloaded.text(timer, datetime(2026, 10, 21, 12)) == (
        "Estimated finish ~12:00:38 · ~0:38 remaining (historical)"
    )
    # Completion removes the whole stage budget, even if this run was faster.
    now[0] = 102
    timer.observe("prepare", 5, 5)
    reloaded.observe("prepare", 5, 5)
    assert reloaded.remaining(timer) == 33  # Future inference+decode+export.
    timer.observe("inference", 0, 8)
    reloaded.observe("inference", 0, 8)
    now[0] = 106
    assert reloaded.remaining(timer) == 29
    timer.observe("inference", 8, 8)
    reloaded.observe("inference", 8, 8)
    assert reloaded.remaining(timer) == 23  # Decode18 remains before it starts.
    reloaded.start({"generation_width": 1920})
    reloaded.observe("prepare", 0, 5)
    assert reloaded.remaining(timer) is None
    assert "unknown (learning)" in reloaded.text(timer)


def test_long_shot_history_tracks_all_segments_and_survives_reload():
    now = [0.0]
    timer = StageTimer(lambda: now[0])
    history = JobHistory()
    history.start({"fps": 24})
    timer.start()
    history.observe("prepare", 121, 250)
    assert history.selected == 250 and history.padded == 291
    history.observe("inference", 8, 24)
    history.observe("decode", 121, 291)
    history.observe("export", 104, 250)
    assert "inference" not in history.completed
    assert "decode" not in history.completed
    history.observe("inference", 24, 24)
    assert "inference" in history.completed
    timer.observe("inference", 24, 24)
    now[0] = 10
    timer.finish()
    assert history.record_success(timer)
    restored = JobHistory(json.loads(json.dumps(history.records)))
    assert restored.records[history.key]["padded"] == 291


def test_full_range_history_uses_one_pass_and_keeps_mode_estimates_separate():
    history = JobHistory()
    history.start({"temporal_mode": "full-range"})
    history.observe("prepare", 0, 250)
    assert history.selected == 250 and history.padded == 257 and not history.chunked
    assert history.key != JobHistory.fingerprint({"temporal_mode": "segmented"})
    assert JobHistory.fingerprint({"temporal_mode": "segmented"}) == JobHistory.fingerprint({})
    assert validated_preset({"temporal_mode": "full-range"}) == {"temporal_mode": "full-range"}
    assert validated_preset({"temporal_mode": "unknown"}) == {}


def test_history_scales_workloads_limits_storage_and_discards_malformed_data():
    options = {}
    key = JobHistory.fingerprint(options)
    history = JobHistory({key: {"selected": 5, "padded": 9,
                                "stages": {"models": 10, "prepare": 5, "decode": 18, "export": 5}}})
    now = [0.0]
    timer = StageTimer(lambda: now[0])
    timer.start()
    timer.observe("prepare", 0, 10)
    history.start(options)
    history.observe("prepare", 0, 10)
    assert history.remaining(timer) == 10 + 10 + 34 + 10  # 10selected→17padded.
    assert not JobHistory({"bad": {"selected": "bad", "padded": 9, "stages": {}}}).records
    for number in range(35):
        history.start({"generation_width": 960 + number})
        history.observe("prepare", 0, 1)
        timer.start()
        timer.observe("prepare", 1, 1)
        now[0] += 1
        timer.finish()
        assert history.record_success(timer)
    assert len(history.records) == 30


@pytest.mark.parametrize(("line", "expected"), [
    ("Matplotlib is not available: plotting disabled", "optional"),
    ("NATTEN not installed; using fallback decoder", "optional"),
    ("RuntimeError: NATTEN failed to allocate", "error"),
    ("warning: nanobind leaked 10 instances", "warning"),
    ("Loading model", "info"),
])
def test_diagnostic_categories_keep_errors_distinct(line, expected):
    assert log_level(line) == expected
