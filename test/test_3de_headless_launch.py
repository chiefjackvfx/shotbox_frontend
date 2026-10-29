"""Run generated automation in a child process from an installation with spaces."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import app_cache
import create_3de_project
import matchmove_helpers


@pytest.fixture(params=[matchmove_helpers, create_3de_project], ids=["dashboard", "standalone"])
def runner(request, tmp_path, monkeypatch):
    module = request.param
    cache_root = tmp_path / "Program Files" / "Shotbox Frontend" / ".cache"
    monkeypatch.setattr(app_cache, "CACHE_ROOT", cache_root)
    executable = tmp_path / "Program Files" / "3DE4 win64 r8.1" / "bin" / "3DE4.exe"
    executable.parent.mkdir(parents=True)
    # This adapter models the parser rejecting whitespace in -run_script's value.
    # It runs the real generated script with a lightweight tde4 API replacement.
    adapter = executable.with_suffix(".py")
    adapter.write_text('''import json, os, pathlib, runpy, sys, types
script = sys.argv[sys.argv.index('-run_script') + 1]
print('3DE startup banner', flush=True)
if any(char.isspace() for char in script):
    sys.exit(1)
calls = []
def dispatch(name, *args):
    calls.append([name, args])
    if name in ('getCameraList', 'getLensList'):
        return []
    if name in ('createCamera', 'createLens'):
        return name + '_id'
    if name == 'setCameraPath' and os.environ.get('SHOTBOX_TEST_3DE_SCRIPT_FAILURE'):
        raise ValueError('Plate is unavailable')
    if name == 'saveProject':
        pathlib.Path(args[0]).write_text(json.dumps(calls), encoding='utf-8')
        return 1
tde4 = types.ModuleType('tde4')
tde4.__getattr__ = lambda name: lambda *args: dispatch(name, *args)
sys.modules['tde4'] = tde4
runpy.run_path(script, run_name='__main__')
''', encoding="utf-8")
    real_run = subprocess.run
    launches = []

    def launch(command, **kwargs):
        launches.append((command, kwargs))
        assert command[0] == str(executable)
        return real_run([sys.executable, str(adapter), *command[1:]], **kwargs)

    monkeypatch.setattr(module.subprocess, "run", launch)
    monkeypatch.setattr(matchmove_helpers, "resolve_3de_executable", lambda: str(executable))
    return module, launches, cache_root


def project_request(root):
    sequence = matchmove_helpers.SequenceInfo(
        folder_path=str(root / "Camera Plates"), example_file=str(root / "Camera Plates" / "plate.1001.exr"),
        sequence_path_pattern=str(root / "Camera Plates" / "plate.####.exr"),
        display_pattern="plate.####.exr", prefix="plate.", suffix=".exr", padding=4,
        first_frame=1001, last_frame=1002, frames=(1001, 1002), width=2048, height=1152,
        header_pixel_aspect=1.0)
    clip = matchmove_helpers.MatchmoveClipRequest(
        slot_index=0, clip_name="plate", camera_name="cam_01", lens_name="35mm",
        sequence_info=sequence, focal_length_mm=35, sequence_start_frame=1001, sequence_end_frame=1002)
    matchmove = root / "Job Assets" / "matchmove"
    return matchmove_helpers.MatchmoveProjectRequest(
        project_name="Test Job", shot_name="shot010", clips=(clip,), camera_preset_name="Alexa 35",
        matchmove_dir=str(matchmove), export_dir=str(matchmove / "export"), fps=25,
        project_dir=str(matchmove / "work"), project_path=str(matchmove / "work" / "test_v001.3de"), version=1)


def test_headless_launch_executes_script_from_cache_with_spaces(runner, tmp_path):
    module, launches, cache = runner
    request = project_request(tmp_path)
    logs = []
    result = module.run_headless_3de(request, logs.append)
    command, options = launches[0]
    assert command[1:3] == ["-no_gui", "-run_script"]
    assert command[3] == Path(result.runtime_script_path).name
    assert " " not in command[3] and "\\" not in command[3] and "/" not in command[3]
    assert Path(options["cwd"]) == cache / "temp"
    assert Path(result.runtime_script_path).parent == cache / "temp"
    assert json.loads(Path(result.status_path).read_text())["success"] is True
    calls = json.loads(Path(request.project_path).read_text())
    assert ["setCameraFPS", ["createCamera_id", 25.0]] in calls
    assert ["setCameraPath", ["createCamera_id", request.clips[0].sequence_info.sequence_path_pattern.replace("\\", "/")]] in calls
    assert any("Working directory:" in line for line in logs)
    module.cleanup_headless_artifacts(result)
    assert not Path(result.runtime_script_path).exists()
    assert not Path(result.status_path).exists()
    assert Path(request.project_path).is_file()


def test_script_failure_keeps_actual_error_and_traceback(runner, tmp_path, monkeypatch):
    module, _, _ = runner
    monkeypatch.setenv("SHOTBOX_TEST_3DE_SCRIPT_FAILURE", "1")
    with pytest.raises(RuntimeError, match="Plate is unavailable") as error:
        module.run_headless_3de(project_request(tmp_path), lambda message: None)
    assert "Traceback:" in str(error.value)
    assert not Path(project_request(tmp_path).project_path).exists()


def test_startup_failure_explains_missing_result_and_preserves_output(runner, tmp_path, monkeypatch):
    module, _, _ = runner
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 1, "3DE startup banner", "licence unavailable"))
    with pytest.raises(RuntimeError, match="did not write a status result") as error:
        module.run_headless_3de(project_request(tmp_path), lambda message: None)
    detail = str(error.value)
    assert "Unknown 3DE headless error" not in detail
    assert "licence unavailable" in detail and "3DE startup banner" in detail
    assert "Working directory:" in detail
