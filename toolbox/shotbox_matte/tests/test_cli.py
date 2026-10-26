from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import signal

import numpy as np
import pytest

from shotbox_matte import cli


@pytest.fixture
def source(tmp_path):
    oiio = pytest.importorskip("OpenImageIO")
    folder = tmp_path / "bam190_DN_v001"
    folder.mkdir()
    for number in range(1001, 1021):
        output = oiio.ImageOutput.create(str(folder / f"beauty.{number}.exr"))
        assert output.open(str(folder / f"beauty.{number}.exr"), oiio.ImageSpec(80, 48, 3, oiio.FLOAT))
        assert output.write_image(np.full((48, 80, 3), 0.18, dtype=np.float32))
        output.close()
    return folder


def test_parser_exposes_processing_and_export_options():
    args = cli.build_parser().parse_args([
        "frames", "--resolution", "1920x1080", "--frame-skip", "2", "--frame-cap", "20",
        "--suffix", "_alpha", "--first-frame", "2001", "--padding", "5", "--precision", "float",
        "--existing", "overwrite", "--source-color", "acescg", "--seed", "9",
    ])
    assert args.resolution == (1920, 1080)
    assert args.frame_skip == 2
    assert args.frame_cap == 20
    assert args.first_frame == 2001
    assert args.existing == "overwrite"


def test_prompt_option_is_removed_from_cli_and_serialized_jobs():
    from dataclasses import asdict
    from shotbox_matte.core import JobOptions

    parser = cli.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["frames", "--prompt", "Foreground person"])
    assert exc.value.code == 2
    assert "--prompt" not in parser.format_help()
    args = parser.parse_args(["frames"])
    options = cli.options_from_args(args)
    payload = json.loads(json.dumps(asdict(options)))
    assert "prompt" not in payload
    assert not hasattr(JobOptions(**payload), "prompt")


def test_source_range_cli_survives_worker_serialization():
    from dataclasses import asdict
    from shotbox_matte.core import JobOptions

    args = cli.build_parser().parse_args(["frames", "--source-in", "1040", "--source-out", "1099", "--frame-skip", "2", "--frame-cap", "5"])
    job = JobOptions(**json.loads(json.dumps(asdict(cli.options_from_args(args)))))
    assert (job.source_start, job.source_end, job.frame_skip, job.frame_cap) == (1040, 1099, 2, 5)
    defaults = cli.options_from_args(cli.build_parser().parse_args(["frames"]))
    assert defaults.source_start is None and defaults.source_end is None


def test_processing_mode_survives_worker_serialization():
    from dataclasses import asdict
    from shotbox_matte.core import JobOptions

    parser = cli.build_parser()
    assert cli.options_from_args(parser.parse_args(["frames"])).temporal_mode == "segmented"
    args = parser.parse_args(["frames", "--temporal-mode", "full-range", "--frame-cap", "250"])
    options = JobOptions(**json.loads(json.dumps(asdict(cli.options_from_args(args)))))
    assert options.temporal_mode == "full-range" and options.frame_cap == 250
    with pytest.raises(SystemExit):
        parser.parse_args(["frames", "--temporal-mode", "unknown"])


@pytest.mark.parametrize("argv,expected", [
    (["--gui", "C:/plates/shot"], "C:/plates/shot"),
    (["--gui", "--input", "C:/plates/shot.mov"], "C:/plates/shot.mov"),
    (["--gui"], None),
])
def test_cli_gui_forwards_selected_input_without_loading_qt(monkeypatch, argv, expected):
    from types import SimpleNamespace

    received = []
    monkeypatch.setitem(sys.modules, "shotbox_matte.gui", SimpleNamespace(main=lambda input_path=None: received.append(input_path) or 0))
    assert cli.main(argv) == 0
    assert received == [expected]


def test_gui_rejects_ambiguous_input_options(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setitem(sys.modules, "shotbox_matte.gui", SimpleNamespace(main=lambda **_kwargs: pytest.fail("ambiguous inputs opened GUI")))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--gui", "one", "--input", "two"])
    assert exc.value.code == 2


def test_worker_discards_legacy_prompt_and_accepts_current_jobs(tmp_path, monkeypatch):
    from dataclasses import asdict

    received = []
    monkeypatch.setattr(cli, "execute", lambda options, *_args: received.append(asdict(options)) or 0)
    job = tmp_path / "job.json"
    for payload in ({"input_path": "frames", "prompt": "Foreground\nSubject: 片"},
                    {"input_path": "frames", "prompt": None}, {"input_path": "frames"}):
        job.write_text(json.dumps(payload), encoding="utf-8")
        assert cli.main(["--worker", str(job)]) == 0
    assert len(received) == 3
    assert all(options["input_path"] == "frames" and "prompt" not in options for options in received)


def test_legacy_worker_prompt_cannot_reach_processing_or_manifest(source, tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    class Engine:
        def __init__(self, _config, **_kwargs):
            pass

        # Match the production interface: no prompt keyword is accepted.
        def infer(self, frames, *, fps, seed, progress, cancelled):
            yield from frames

    monkeypatch.setitem(sys.modules, "shotbox_matte.native", SimpleNamespace(
        ModelConfig=SimpleNamespace(from_dict=lambda values: values), NativeAlphaEngine=Engine,
    ))
    job = tmp_path / "legacy-worker.json"
    job.write_text(json.dumps({"input_path": str(source), "prompt": "Foreground person",
                               "frame_cap": 1, "generation_width": 32, "generation_height": 32}), encoding="utf-8")
    assert cli.main(["--worker", str(job)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    result = next(event["result"] for event in events if event["type"] == "result")
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert result["frame_count"] == 1
    assert manifest["prompt"] == ""
    assert "Foreground person" not in json.dumps(manifest)


@pytest.mark.parametrize("value", ["0x2", "1x-2", "foo", "10x20x30"])
def test_bad_resolution_is_a_usage_error(value):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["frames", "--resolution", value])
    assert exc.value.code == 2


def test_help_is_headless_without_importing_gui_or_torch():
    script = """
import importlib.abc, sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PyQt', 'torch', 'shotbox_matte.gui')):
            raise AssertionError('CLI loaded desktop or GPU library: ' + fullname)
sys.meta_path.insert(0, NoDesktop())
from shotbox_matte.cli import main
main(['--help'])
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout
    assert "--json-progress" in result.stdout


def test_dry_run_headless_and_does_not_reserve_output(source):
    script = """
import importlib.abc, sys
class NoDesktop(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PyQt', 'torch', 'ltx_', 'shotbox_matte.gui')):
            raise AssertionError('dry run loaded inference/UI: ' + fullname)
sys.meta_path.insert(0, NoDesktop())
from shotbox_matte.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
    before = set(source.parent.iterdir())
    result = subprocess.run([
        sys.executable, "-c", script, str(source), "--dry-run", "--json-progress",
        "--suffix", "_alpha", "--resolution", "960x544",
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    event = json.loads(result.stdout)
    assert event["type"] == "plan"
    assert event["plan"]["selected_count"] == 20
    assert event["plan"]["padded_count"] == 25
    assert event["plan"]["added_copies"] == 5
    assert event["plan"]["prompt"] == ""
    assert Path(event["plan"]["output_preview"]).name == "bam190_DN_alpha_v001"
    assert set(source.parent.iterdir()) == before


def test_cli_dry_run_applies_inclusive_range_before_skip_and_cap(source, capsys):
    before = set(source.parent.iterdir())
    assert cli.main([str(source), "--source-in", "1004", "--source-out", "1017", "--frame-skip", "2",
                     "--frame-cap", "3", "--dry-run", "--json-progress"]) == 0
    event = json.loads(capsys.readouterr().out)
    assert event["type"] == "plan"
    assert event["plan"]["selected_indices"] == [3, 6, 9]
    assert event["plan"]["source_numbers"] == [1004, 1007, 1010]
    assert (event["plan"]["first_frame"], event["plan"]["padded_count"]) == (1004, 9)
    assert set(source.parent.iterdir()) == before


def test_cli_reports_errors_as_json(tmp_path, capsys):
    result = cli.main([str(tmp_path / "missing"), "--dry-run", "--json-progress"])
    event = json.loads(capsys.readouterr().out)
    assert result == 1
    assert event["type"] == "error"
    assert event["message"]


def test_models_config_paths_are_relative_to_config(tmp_path):
    config = tmp_path / "models.json"
    config.write_text(json.dumps({"transformer": "weights/t.safetensors"}))
    args = cli.build_parser().parse_args(["--models-config", str(config), "--model-root", str(tmp_path)])
    assert cli.load_models(args)["transformer"] == str(tmp_path / "weights/t.safetensors")


def test_worker_progress_protocol_keeps_native_stdout_separate(monkeypatch, capsys):
    from shotbox_matte import core

    def fake_run(options, progress, cancelled):
        print("third-party diagnostic")
        progress("inference", 1, 1, "Done")
        return {"output_dir": "/example", "frame_count": 1}

    monkeypatch.setattr(core, "run_job", fake_run)
    assert cli.execute(object(), cli.Reporter(True)) == 0
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert [event["type"] for event in events] == ["progress", "result"]
    assert "third-party diagnostic" in captured.err


def test_cancellation_has_nonzero_exit(monkeypatch, tmp_path, capsys):
    from shotbox_matte import core

    cancel = tmp_path / "job.cancel"
    cancel.touch()

    def fake_run(options, progress, cancelled):
        assert cancelled()
        raise core.CancelledError("Stopped")

    monkeypatch.setattr(core, "run_job", fake_run)
    assert cli.execute(object(), cli.Reporter(True), cancel) == 130
    assert json.loads(capsys.readouterr().out)["type"] == "cancelled"


def test_worker_cancel_file_is_honored_before_loading_models(tmp_path):
    job = tmp_path / "job.json"
    job.write_text(json.dumps({"input_path": str(tmp_path / "does-not-exist")}))
    Path(str(job) + ".cancel").touch()
    result = subprocess.run([
        sys.executable, "-m", "shotbox_matte", "--worker", str(job),
    ], capture_output=True, text=True, timeout=10)
    assert result.returncode == 130
    assert json.loads(result.stdout)["type"] == "cancelled"
    assert not list(tmp_path.glob("*_roto_v*"))


@pytest.mark.skipif(sys.platform == "win32", reason="Windows console signals differ from POSIX SIGINT")
def test_ctrl_c_sets_cooperative_cancellation_in_real_process():
    script = """
import time
from shotbox_matte import core, cli
def job(options, progress, cancelled):
    progress('ready', 0, 1, 'waiting for Ctrl+C')
    while not cancelled():
        time.sleep(.01)
    raise core.CancelledError('cancelled by signal')
core.run_job = job
raise SystemExit(cli.execute(object(), cli.Reporter(True)))
"""
    process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        ready = json.loads(process.stdout.readline())
        assert ready["stage"] == "ready"
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 130, stderr
        assert json.loads(stdout)["type"] == "cancelled"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
