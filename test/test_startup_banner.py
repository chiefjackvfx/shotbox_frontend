"""Terminal startup diagnostics without loading GUI or inference libraries."""
import io
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import startup_banner


def test_banner_reports_actual_runtime_and_dependency_metadata():
    from app_version import APP_VERSION
    from startup_banner import render_banner
    from importlib.metadata import version

    text = render_banner(color=False, width=100)
    for value in (APP_VERSION, platform.python_version(), sys.executable,
                  version('PyQt6'), version('numpy')):
        assert value in text
    for label in ('SHOTBOX', 'Python', 'PyQt6', 'Qt', 'NumPy', 'OpenImageIO',
                  'OpenColorIO', 'OpenEXR', 'PyAV', 'PyTorch', 'Transformers'):
        assert label in text
    assert '\x1b[' not in text


def test_missing_and_broken_package_metadata(monkeypatch):
    def version(package):
        if package == "torch":
            raise startup_banner.metadata.PackageNotFoundError(package)
        if package == "OpenEXR":
            raise ValueError("invalid metadata")
        if package == "av":
            return ""
        return "1.2.3"

    monkeypatch.setattr(startup_banner.metadata, "version", version)
    text = startup_banner.render_banner()
    assert "PyTorch       not installed" in text
    assert "OpenEXR       unknown" in text
    assert "PyAV          unknown" in text
    assert "NumPy         1.2.3" in text


@pytest.mark.parametrize("width,has_logo", [(40, False), (79, False), (80, True), (100, True)])
def test_layout_and_complete_executable_path(width, has_logo, monkeypatch):
    executable = "/a very long path/" + "environment/" * 12 + "bin/python"
    monkeypatch.setattr(sys, "executable", executable)
    text = startup_banner.render_banner(width=width)
    assert ("@" in text) is has_logo
    assert ("#" in text) is has_logo
    assert f"Python executable: {executable}" in text
    assert "Transformers" in text


@pytest.mark.parametrize("width", [40, 100])
def test_colored_and_plain_layouts_match(width):
    plain = startup_banner.render_banner(width=width)
    colored = startup_banner.render_banner(color=True, width=width)
    assert "\x1b[38;2;240;90;32m" in colored
    if width >= 80:
        assert "\x1b[37m" in colored
    assert "\x1b[1mSHOTBOX" in colored
    assert re.sub(r"\x1b\[[0-9;]*m", "", colored).rstrip() == plain.rstrip()
    palette = colored.splitlines()[-1]
    for background in range(40, 48):
        assert f"\x1b[{background}m   " in palette
    assert palette.endswith("\x1b[0m")
    assert len(re.sub(r"\x1b\[[0-9;]*m", "", palette)) == (56 if width >= 80 else 24)


class TerminalStream(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def clean_terminal(monkeypatch):
    for key in ("NO_COLOR", "TERM", "WT_SESSION", "ANSICON", "ConEmuANSI"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(startup_banner.sys, "platform", "linux")
    monkeypatch.setattr(startup_banner.shutil, "get_terminal_size",
                        lambda **kwargs: os.terminal_size((100, 24)))


def test_redirected_output_is_plain_and_flushed(clean_terminal):
    class RedirectedStream(io.StringIO):
        flushed = False

        def flush(self):
            self.flushed = True

    stream = RedirectedStream()
    startup_banner.print_banner(stream)
    assert "SHOTBOX" in stream.getvalue()
    assert "\x1b[" not in stream.getvalue()
    assert stream.flushed


def test_supported_terminal_uses_color(clean_terminal):
    stream = TerminalStream()
    startup_banner.print_banner(stream)
    assert "\x1b[" in stream.getvalue()


@pytest.mark.parametrize("key,value", [("NO_COLOR", ""), ("TERM", "dumb"), ("TERM", "unknown")])
def test_terminal_color_suppression(clean_terminal, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    stream = TerminalStream()
    startup_banner.print_banner(stream)
    assert "SHOTBOX" in stream.getvalue()
    assert "\x1b[" not in stream.getvalue()


def test_windows_color_requires_supported_terminal(clean_terminal, monkeypatch):
    monkeypatch.setattr(startup_banner.sys, "platform", "win32")
    plain = TerminalStream()
    startup_banner.print_banner(plain)
    assert "\x1b[" not in plain.getvalue()
    monkeypatch.setenv("WT_SESSION", "windows-terminal")
    colored = TerminalStream()
    startup_banner.print_banner(colored)
    assert "\x1b[" in colored.getvalue()


def test_stream_terminal_width_controls_layout(clean_terminal, monkeypatch):
    class NarrowTerminal(TerminalStream):
        def fileno(self):
            return 42

    monkeypatch.setattr(startup_banner.os, "get_terminal_size",
                        lambda fd: os.terminal_size((60, 24)))
    stream = NarrowTerminal()
    startup_banner.print_banner(stream)
    assert "SHOTBOX" in stream.getvalue()
    assert "@" not in stream.getvalue()


def test_unavailable_stdout_is_skipped(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    startup_banner.print_banner()


@pytest.mark.parametrize("failure", [BrokenPipeError(), UnicodeEncodeError("ascii", "x", 0, 1, "test")])
def test_output_failure_is_harmless(clean_terminal, failure):
    class BrokenStream(io.StringIO):
        def write(self, value):
            raise failure

    startup_banner.print_banner(BrokenStream())


def test_closed_stream_is_harmless():
    stream = io.StringIO()
    stream.close()
    startup_banner.print_banner(stream)


def test_collection_failure_is_harmless(monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("unexpected platform failure")

    monkeypatch.setattr(startup_banner, "render_banner", fail)
    startup_banner.print_banner(io.StringIO())


def test_banner_does_not_import_native_libraries():
    script = """
import importlib.abc
import sys

blocked = ('PyQt6', 'numpy', 'OpenImageIO', 'PyOpenColorIO', 'OpenEXR',
           'av', 'torch', 'transformers', 'PIL')
class BlockNativeImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise AssertionError('Imported native dependency: ' + fullname)
sys.meta_path.insert(0, BlockNativeImports())
from startup_banner import render_banner
assert 'SHOTBOX' in render_banner()
assert not any(name.split('.')[0] in blocked for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-B", "-c", script],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("run_as_main", [True, False])
@pytest.mark.parametrize("broken_banner", [False, True])
def test_entrypoint_banner_precedes_gui_imports(run_as_main, broken_banner):
    # Stop at the first GUI import to exercise real entrypoint ordering without
    # opening a window, contacting the server or importing the rest of the app.
    script = f"""
import importlib.abc
import runpy
import sys

class ReachedGUI(Exception):
    pass
class StopAtGUI(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'PyQt6':
            raise ReachedGUI()
sys.meta_path.insert(0, StopAtGUI())
if {broken_banner!r}:
    import startup_banner
    def fail():
        raise RuntimeError('banner failure')
    startup_banner.print_banner = fail
try:
    runpy.run_path('main.py', run_name={'__main__' if run_as_main else 'imported_main'!r})
except ReachedGUI:
    pass
else:
    raise AssertionError('GUI import was not reached')
"""
    result = subprocess.run([sys.executable, "-B", "-c", script],
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("SHOTBOX") == int(run_as_main and not broken_banner)
