"""Exercise the real batch launcher, including signed native failure codes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows cmd.exe")
@pytest.mark.parametrize("exit_code", [0, 7, 0xC0000005])
def test_launcher_preserves_output_and_failure_status(tmp_path, exit_code):
    root = tmp_path / "Shotbox with spaces"
    root.mkdir()
    source = Path(__file__).resolve().parents[1] / "windows_run_shotbox.bat"
    shutil.copy2(source, root / source.name)
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root / "venv")],
                   check=True, capture_output=True, timeout=60)
    (root / "main.py").write_text(f"""
import ctypes
import faulthandler
import sys
assert faulthandler.is_enabled()
print('startup diagnostic output')
if {exit_code} > 255:
    terminate = ctypes.windll.kernel32.ExitProcess
    terminate.argtypes = [ctypes.c_uint32]
    terminate.restype = None
    terminate({exit_code})
sys.exit({exit_code})
""", encoding="utf-8")
    result = subprocess.run([os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", source.name],
                            cwd=root, input="\n", capture_output=True, text=True, timeout=20)
    assert result.returncode & 0xFFFFFFFF == exit_code
    assert "startup diagnostic output" in result.stdout
    if exit_code:
        signed_code = exit_code if exit_code < 0x80000000 else exit_code - 0x100000000
        assert f"ShotBox exited with code {signed_code}." in result.stdout
        assert "Copy the error output above" in result.stdout
    else:
        assert "ShotBox exited with code" not in result.stdout
