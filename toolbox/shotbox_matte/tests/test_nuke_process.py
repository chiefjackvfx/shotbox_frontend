"""Pure environment policy tests; these do not exercise Nuke/QProcess."""
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke import process


def test_linux_child_environment_removes_host_injections_and_preserves_runtime_paths(tmp_path):
    inherited = {"PYTHONHOME": "/opt/Nuke17.1v2", "PYTHONPATH": "/home/artist/.nuke",
                 "QT_PLUGIN_PATH": "/opt/Nuke17.1v2/plugins", "QT_QPA_PLATFORM_PLUGIN_PATH": "/host/platforms",
                 "QML2_IMPORT_PATH": "/host/qml2", "QML_IMPORT_PATH": "/host/qml",
                 "PATH": "/usr/local/bin:/opt/Nuke17.1v2:/usr/bin:/studio/license/bin",
                 "LD_LIBRARY_PATH": "/usr/lib/nvidia:/opt/Nuke17.1v2/lib:/usr/local/cuda/lib64:/studio/license/lib",
                 "DYLD_LIBRARY_PATH": "/opt/Nuke17.1v2/lib:/studio/license/lib",
                 "LD_PRELOAD": "/usr/lib/libdriver.so /opt/Nuke17.1v2/lib/libQt.so /studio/license/liblicense.so",
                 "RLM_LICENSE": "4101@licensehost", "FOUNDRY_LICENSE_FILE": "4101@licensehost",
                 "CUDA_VISIBLE_DEVICES": "2", "HOME": "/home/artist", "SHOTBOX_JOB_DIR": "/old/jobs"}
    original = dict(inherited)
    result = process.child_environment(tmp_path / "attempt with 空格", inherited)
    assert inherited == original
    for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH"):
        assert key not in result
    assert result["PATH"] == "/usr/local/bin:/usr/bin:/studio/license/bin"
    assert result["LD_LIBRARY_PATH"] == "/usr/lib/nvidia:/usr/local/cuda/lib64:/studio/license/lib"
    assert result["DYLD_LIBRARY_PATH"] == "/studio/license/lib"
    assert result["LD_PRELOAD"] == "/usr/lib/libdriver.so /studio/license/liblicense.so"
    for key in ("RLM_LICENSE", "FOUNDRY_LICENSE_FILE", "CUDA_VISIBLE_DEVICES", "HOME"):
        assert result[key] == original[key]
    assert result["SHOTBOX_JOB_DIR"] == str(tmp_path / "attempt with 空格" / "backend_jobs")
    assert (result["PYTHONUTF8"], result["PYTHONIOENCODING"], result["PYTHONUNBUFFERED"]) == ("1", "utf-8", "1")


@pytest.mark.parametrize("preload", ["/opt/Nuke17.1v2/lib/libQt.so:/usr/lib/libdriver.so:/studio/license/lib.so",
                                    "/usr/lib/libdriver.so /opt/Nuke17.1v2/lib/libQt.so:/studio/license/lib.so"])
def test_preload_accepts_both_loader_delimiters_without_dropping_license_or_driver(preload, tmp_path):
    result = process.child_environment(tmp_path, {"LD_PRELOAD": preload})
    assert result["LD_PRELOAD"].split() == ["/usr/lib/libdriver.so", "/studio/license/lib.so"]


def test_windows_path_policy_with_native_separator_fixture(tmp_path, monkeypatch):
    # This verifies string policy only; Windows execution remains a host gate.
    monkeypatch.setattr(process.os.path, "sep", "\\")
    monkeypatch.setattr(process.os, "pathsep", ";")
    inherited = {"PATH": r"C:\Windows\System32;C:\Program Files\Nuke17.1v2;D:\CUDA\bin;\\server\licenses\bin",
                 "QT_PLUGIN_PATH": r"C:\Program Files\Nuke17.1v2\plugins",
                 "RLM_LICENSE": "4101@licensehost"}
    result = process.child_environment(tmp_path, inherited)
    assert result["PATH"] == r"C:\Windows\System32;D:\CUDA\bin;\\server\licenses\bin"
    assert result["RLM_LICENSE"] == "4101@licensehost"
    assert "QT_PLUGIN_PATH" not in result


def test_process_import_does_not_load_host_gui_or_backend():
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import shotbox_nuke.process
assert not any(name.startswith(('nuke', 'PySide', 'PyQt', 'torch', 'shotbox_matte')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-c", script, str(root.parents[1] / "plugins/nuke")], check=True)
