"""Qualification must never replace production or imply release acceptance."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import app_cache
import runtime_qualification as qualification
import runtime_setup as setup


@pytest.fixture
def frontend(tmp_path, monkeypatch):
    root = tmp_path / "Shotbox with spaces"
    (root / "venv").mkdir(parents=True)
    (root / "venv/production").write_text("Python 3.12")
    (root / "requirements.txt").write_text("existing-pins\n")
    (root / "runtime_python.txt").write_text("3.12\n")
    monkeypatch.setattr(setup, "FRONTEND_ROOT", root)
    monkeypatch.setattr(app_cache, "CACHE_ROOT", root / ".cache")
    monkeypatch.setattr(setup, "cuda_platform", lambda: False)
    return root


@pytest.mark.parametrize("failure", [None, "installation_and_imports", "dependency_consistency", "environment_metadata"])
def test_qualification_records_outcome_and_preserves_production(frontend, monkeypatch, failure):
    calls = []

    def prepare(root, **kwargs):
        calls.append((root, kwargs))
        if failure == "installation_and_imports":
            raise RuntimeError("wheel import failure")
        return Path(sys.executable)

    def check(*args, **kwargs):
        if failure == "dependency_consistency":
            raise subprocess.CalledProcessError(1, args[0])

    def metadata(*args, **kwargs):
        if failure == "environment_metadata":
            raise subprocess.TimeoutExpired(args[0], 180)
        return subprocess.CompletedProcess(args[0], 0, json.dumps({"python": "3.13", "packages": []}), "")

    monkeypatch.setattr(setup, "prepare_runtime", prepare)
    monkeypatch.setattr(setup, "run_setup_command", check)
    monkeypatch.setattr(qualification.subprocess, "run", metadata)
    if failure:
        with pytest.raises((RuntimeError, subprocess.SubprocessError)):
            qualification.qualify_runtime(uv="uv")
    else:
        qualification.qualify_runtime(uv="uv")
    root = frontend / ".runtime/qualification/python313"
    assert calls == [(root, {"uv": "uv", "required_python": (3, 13),
                             "requirements": frontend / "requirements.txt"})]
    report = json.loads((root / "report.json").read_text())
    assert report["promotion_ready"] is False
    assert report["status"] == ("failed" if failure else "installed_and_import_verified")
    assert report["phase"] == (failure or "complete")
    assert bool(report.get("error")) == bool(failure)
    assert (frontend / "venv/production").read_text() == "Python 3.12"
    assert (frontend / "runtime_python.txt").read_text() == "3.12\n"
    assert not list((frontend / ".cache/temp/.active-tasks").glob("*.json"))


@pytest.mark.parametrize("part", [".runtime", ".runtime/qualification", ".runtime/qualification/python313"])
def test_qualification_rejects_redirected_directory(frontend, tmp_path, part):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = frontend / part
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(RuntimeError, match="redirected qualification path"):
        qualification.qualify_runtime(uv="uv")
    assert not list(outside.iterdir())
    assert (frontend / "venv/production").read_text() == "Python 3.12"
