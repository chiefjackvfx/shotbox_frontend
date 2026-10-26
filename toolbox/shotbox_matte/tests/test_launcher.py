"""Environment setup boundaries and real shell-launch argument handling."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import launch_ui
import launcher


ROOT = Path(__file__).resolve().parents[1]
READY = {"python_ok": True, "errors": []}


@pytest.mark.parametrize("windows", [False, True])
def test_native_environment_is_selected_without_touching_foreign_venv(tmp_path, windows):
    foreign = tmp_path / ".venv" / ("bin/python" if windows else "Scripts/python.exe")
    foreign.parent.mkdir(parents=True)
    foreign.write_text("foreign interpreter")
    expected = tmp_path / (".venv-windows" if windows else ".venv-linux")
    assert launcher.environment_path(tmp_path, windows=windows) == expected
    native = launcher.environment_python(expected, windows=windows)
    native.parent.mkdir(parents=True)
    native.write_text("native interpreter")
    assert launcher.environment_path(tmp_path, windows=windows) == expected
    assert foreign.read_text() == "foreign interpreter"


def test_ready_environment_skips_installation_and_preserves_source_paths(tmp_path, monkeypatch):
    project = tmp_path / "ShotBox copy with spaces"
    project.mkdir()
    caller = tmp_path / "source media"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setattr(launcher, "probe", lambda *args: READY)
    monkeypatch.setattr(launcher, "setup", lambda *args: pytest.fail("Ready environment was reinstalled"))
    calls = []

    def started(command, **kwargs):
        calls.append((command, kwargs))
        return 17

    monkeypatch.setattr(launcher.subprocess, "call", started)
    assert launcher.main(["plate name;$(literal).mxf"], project=project) == 17
    command, kwargs = calls[0]
    assert command[-1] == str(caller / "plate name;$(literal).mxf")
    assert kwargs["cwd"] == project
    assert kwargs["env"]["SHOTBOX_PORTABLE_DIR"] == str(project)
    assert kwargs["env"]["SHOTBOX_JOB_DIR"] == str(project / "logs" / "jobs")
    assert not (project / ".launcher").exists()


def test_fresh_setup_creates_local_venv_and_installs_cuda_and_native_dependencies(tmp_path, monkeypatch):
    uv = tmp_path / "tools with spaces" / "uv"
    uv.parent.mkdir()
    uv.touch()
    reports = iter([{"python_ok": False, "errors": ["missing Python"]}, READY])
    monkeypatch.setattr(launcher, "probe", lambda *args: next(reports))
    commands = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda command, **kwargs: commands.append((command, kwargs)))
    monkeypatch.setattr(launcher.subprocess, "call", lambda *args, **kwargs: pytest.fail("Setup-only launched the app"))
    assert launcher.main(["--uv", str(uv), "--setup-only"], project=tmp_path) == 0
    create, install = commands
    assert create[0][-1] == str(launcher.environment_path(tmp_path))
    assert "--allow-existing" in create[0] and "--clear" not in create[0]
    assert install[0][install[0].index("--torch-backend") + 1] == "cu130"
    assert install[0][-1] == str(tmp_path) + "[desktop]"
    assert str(tmp_path / "launcher-requirements.txt") in install[0]
    for _command, options in commands:
        assert options["cwd"] == tmp_path and options["check"] is True
        assert options["env"]["UV_CACHE_DIR"] == str(tmp_path / ".launcher" / "cache")


def test_incomplete_environment_signals_wrapper_without_starting_or_installing(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "probe", lambda *args: {"python_ok": True, "errors": ["missing PyQt6"]})
    monkeypatch.setattr(launcher, "setup", lambda *args: pytest.fail("Fast path installed dependencies"))
    assert launcher.main(["--no-setup"], project=tmp_path) == launcher.SETUP_NEEDED


def test_failed_install_does_not_launch_ui_or_report_success(tmp_path, monkeypatch, capsys):
    uv = tmp_path / "uv"
    uv.touch()
    monkeypatch.setattr(launcher, "probe", lambda *args: {"python_ok": True, "errors": ["missing PyQt6"]})

    def failed(command, **kwargs):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(launcher.subprocess, "run", failed)
    monkeypatch.setattr(launcher.subprocess, "call", lambda *args, **kwargs: pytest.fail("Failed setup launched UI"))
    assert launcher.main(["--uv", str(uv)], project=tmp_path) == 1
    assert "setup failed" in capsys.readouterr().err


def test_ui_entrypoint_keeps_the_active_native_environment(tmp_path, monkeypatch):
    from types import SimpleNamespace

    for name in (".venv", ".venv-linux"):
        python = tmp_path / name / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.touch()
    monkeypatch.setattr(launch_ui, "__file__", str(tmp_path / "launch_ui.py"))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / ".venv-linux"))
    monkeypatch.setattr(sys, "argv", ["launch_ui.py"])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launch_ui.subprocess, "call", lambda *args, **kwargs: pytest.fail("Switched out of active venv"))
    monkeypatch.setitem(sys.modules, "shotbox_matte.gui", SimpleNamespace(main=lambda source: 0))
    assert launch_ui.main() == 0


@pytest.mark.skipif(sys.platform != "linux", reason="Real Linux shell launch")
@pytest.mark.parametrize("exit_code", [0, 17])
def test_shell_reuses_existing_python_from_another_directory_and_propagates_exit(tmp_path, exit_code):
    project = tmp_path / "ShotBox copy 日本語"
    project.mkdir()
    shutil.copyfile(ROOT / "launch.sh", project / "launch.sh")
    python = project / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    (project / "launcher.py").write_text(
        "import json, pathlib, sys\n"
        "pathlib.Path(__file__).with_suffix('.json').write_text(json.dumps(sys.argv[1:]))\n"
        f"raise SystemExit({exit_code})\n", encoding="utf-8",
    )
    result = subprocess.run(["sh", str(project / "launch.sh"), "clip with spaces;$(literal).mov"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == exit_code, result.stderr
    assert json.loads((project / "launcher.json").read_text()) == ["--no-setup", "clip with spaces;$(literal).mov"]
    assert not (project / ".launcher").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Real Linux shell launch")
def test_fresh_shell_uses_local_uv_and_passes_setup_options(tmp_path):
    project = tmp_path / "Fresh ShotBox"
    project.mkdir()
    shutil.copyfile(ROOT / "launch.sh", project / "launch.sh")
    uv = project / ".launcher" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = python ] && [ \"$2\" = install ]; then exit 0; fi\n"
        "if [ \"$1\" = python ] && [ \"$2\" = find ]; then\n"
        f"  printf '%s\\n' '{sys.executable}'\n"
        "  exit 0\nfi\nexit 90\n",
    )
    uv.chmod(0o755)
    (project / "launcher.py").write_text(
        "import json, os, pathlib, sys\n"
        "pathlib.Path(__file__).with_suffix('.json').write_text(json.dumps(\n"
        "dict(arguments=sys.argv[1:], python_dir=os.environ['UV_PYTHON_INSTALL_DIR'])))\n",
    )
    result = subprocess.run(["sh", str(project / "launch.sh"), "--setup-only"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    recorded = json.loads((project / "launcher.json").read_text())
    assert recorded["arguments"] == ["--uv", str(uv), "--setup-only"]
    assert recorded["python_dir"] == str(project / ".launcher" / "python")
