"""Active Windows environment startup checks without executing Windows binaries."""

from __future__ import annotations

import gc
import json
from pathlib import Path
import subprocess
import sys
import weakref

import pytest

from shotbox_matte import _windows


def runtime_directories(root: Path) -> tuple[Path, ...]:
    runtime = root
    site = runtime / "Lib" / "site-packages"
    directories = (
        runtime, runtime / "Scripts", site / "torch" / "lib", site / "PyQt6" / "Qt6" / "bin",
        site / "OpenImageIO" / "bin", site / "numpy.libs", site / "av.libs",
    )
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    return directories


@pytest.fixture
def dll_registration(monkeypatch):
    calls = []
    handles = []

    class Handle:
        def close(self):
            raise AssertionError("DLL handles must remain open for the process lifetime")

    def register(path):
        calls.append(path)
        handle = Handle()
        handles.append(weakref.ref(handle))
        return handle

    monkeypatch.setattr(_windows, "_DLL_HANDLES", {})
    monkeypatch.setattr(_windows.os, "add_dll_directory", register, raising=False)
    return calls, handles


def test_registers_runtime_wheel_directories_and_retains_handles(tmp_path, dll_registration):
    expected = runtime_directories(tmp_path)
    calls, handles = dll_registration
    registered = _windows.configure_dlls(tmp_path)
    gc.collect()
    assert set(registered) == set(expected)
    assert set(calls) == {str(path) for path in expected}
    assert len(_windows._DLL_HANDLES) == len(expected)
    assert all(reference() is not None for reference in handles)


def test_registration_is_idempotent_and_adds_new_wheel_directories(tmp_path, dll_registration):
    expected = runtime_directories(tmp_path)
    calls, _handles = dll_registration
    assert len(_windows.configure_dlls(tmp_path)) == len(expected)
    assert _windows.configure_dlls(tmp_path / ".") == ()
    assert len(calls) == len(expected)
    late_wheel = tmp_path / "Lib" / "site-packages" / "later.libs"
    late_wheel.mkdir()
    assert _windows.configure_dlls(tmp_path) == (late_wheel,)
    assert calls[-1] == str(late_wheel)


def test_missing_optional_directories_and_non_windows_are_noops(tmp_path, dll_registration, monkeypatch):
    calls, _handles = dll_registration
    assert _windows.configure_dlls(tmp_path / "absent-venv") == ()
    assert not calls
    runtime_directories(tmp_path)
    monkeypatch.delattr(_windows.os, "add_dll_directory")
    assert _windows.configure_dlls(tmp_path) == ()
    assert not calls


def test_failed_registration_can_be_retried(tmp_path, dll_registration, monkeypatch):
    runtime = tmp_path
    register = _windows.os.add_dll_directory

    def denied(path):
        raise OSError("directory registration denied")

    monkeypatch.setattr(_windows.os, "add_dll_directory", denied)
    with pytest.raises(OSError, match="registration denied"):
        _windows.configure_dlls(tmp_path)
    assert not _windows._DLL_HANDLES
    monkeypatch.setattr(_windows.os, "add_dll_directory", register)
    assert _windows.configure_dlls(tmp_path) == (runtime,)


def test_windows_worker_registers_active_environment_before_cli_without_portable_flag(tmp_path):
    """Run real package/__main__ startup; stub only the CLI's execution boundary."""
    expected = runtime_directories(tmp_path)
    script = r'''
import importlib.abc, json, os, pathlib, runpy, sys, types
root = sys.argv[1]
calls = []
class NoNativeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('PyQt6', 'torch', 'OpenImageIO', 'shotbox_matte.gui')):
            raise AssertionError('DLL setup imported a native runtime: ' + fullname)
sys.meta_path.insert(0, NoNativeImports())
# Keep pathlib's real host OS object while simulating the package's Windows
# platform gate and DLL API. No Windows binaries are loaded by this unit test.
windows_os = types.ModuleType('os')
windows_os.__dict__.update(os.__dict__)
windows_os.name = 'nt'
windows_os.environ = dict(os.environ)
windows_os.environ.pop('SHOTBOX_PORTABLE_DIR', None)
windows_os.add_dll_directory = lambda path: calls.append(path) or object()
sys.modules['os'] = windows_os
sys.prefix = root
cli = types.ModuleType('shotbox_matte.cli')
def main():
    assert sys.argv[1:] == ['--worker', 'job.json']
    assert calls, 'worker CLI started before DLL setup'
    from shotbox_matte._windows import configure_dlls
    assert configure_dlls() == (), 'startup repeated registrations'
    print(json.dumps(calls))
    return 0
cli.main = main
sys.modules['shotbox_matte.cli'] = cli
sys.argv = ['shotbox_matte', '--worker', 'job.json']
runpy.run_module('shotbox_matte', run_name='__main__')
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert set(json.loads(result.stdout)) == {str(path) for path in expected}
