from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import app_cache
from test import denoise_windows_diagnostic as diagnostic


class VisibleDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.script = self.directory / "template.nk"
        self.script.write_text("test template")
        self.executable = self.directory / "Nuke.exe"
        self.executable.touch()
        self.request = {"workflow": "template", "script": str(self.script), "source": "source.mov",
                        "output_pattern": "production/plate_####.exr", "nuke": str(self.executable),
                        "colourspace": "ARRI LogC3 (EI800)", "fps": 24, "topdown": True}
        self.path = self.directory / "request.json"
        self.path.write_text(json.dumps(self.request))

    def test_copies_exact_setup_and_redirects_all_output_without_touching_original(self):
        target = self.directory / "diagnostic"
        target.mkdir()
        path, request = diagnostic.prepare_request(self.path, target)
        self.assertEqual(json.loads(self.path.read_text()), self.request)
        self.assertEqual(self.script.read_text(), "test template")
        self.assertEqual(Path(request["script"]).read_text(), "test template")
        for key in ("source", "colourspace", "fps", "topdown"):
            self.assertEqual(request[key], self.request[key])
        self.assertEqual(request["output_pattern"], (target / "renders/diagnostic_####.exr").as_posix())
        self.assertTrue(request["diagnostic"])
        self.assertFalse(request["qt_terminal"])
        self.assertEqual(json.loads(path.read_text()), request)

    def test_latest_request_ignores_visible_diagnostic_runs(self):
        with mock.patch.object(app_cache, "CACHE_ROOT", self.directory):
            root = app_cache.cache_directory("temp")
            for name, value, modified in (("denoise-job", self.request, 10),
                                          ("denoise-visible-test", {**self.request, "diagnostic": True}, 20)):
                folder = root / name
                folder.mkdir()
                path = folder / "request.json"
                path.write_text(json.dumps(value))
                os.utime(path, (modified, modified))
            self.assertEqual(diagnostic.latest_request(), root / "denoise-job/request.json")

    def test_launcher_uses_visible_gui_and_sanitized_environment_until_nuke_closes(self):
        process = mock.Mock()
        process.wait.return_value = 0
        process.poll.return_value = 0
        with mock.patch.object(app_cache, "CACHE_ROOT", self.directory / "cache"), \
                mock.patch.object(sys, "platform", "win32"), \
                mock.patch.object(diagnostic.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True), \
                mock.patch.object(diagnostic.subprocess, "Popen", return_value=process) as launch, \
                redirect_stdout(io.StringIO()):
            directory = diagnostic.diagnose(self.path)
        command = launch.call_args.args[0]
        self.assertIn("--nukex", command)
        self.assertIn("--topdown", command)
        self.assertNotIn("--tg", command)
        environment = launch.call_args.kwargs["env"]
        self.assertEqual(environment["QT_QPA_PLATFORM"], "windows")
        self.assertEqual(environment["TEMP"], str(directory / "tmp"))
        self.assertNotIn("SHOTBOX_DENOISE_DESKTOP", environment)
        self.assertNotIn("SHOTBOX_DENOISE_WINDOWS_JOB", environment)
        self.assertIn("start_host", (directory / "startup/menu.py").read_text())
        self.assertTrue((directory / "launch.json").is_file())
        process.wait.assert_called_once()

    def test_host_opens_first_frame_profile_without_rendering_or_exiting(self):
        nuke, noise, root = mock.Mock(), mock.MagicMock(), mock.MagicMock()
        nuke.GUI, nuke.env, nuke.NUKE_VERSION_STRING = True, {"nukex": True}, "17.1v2"
        nuke.toNode.return_value = None
        nuke.root.return_value = root
        root.__getitem__.return_value.value.return_value = 1001
        noise.width.return_value, noise.height.return_value = 512, 256
        qt = SimpleNamespace(QtCore=SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda _, run: run())))
        self.addCleanup(os.chdir, Path.cwd())
        with mock.patch.dict(sys.modules, {"nuke": nuke, "PySide6": qt}), \
                mock.patch("denoise_template.prepare", return_value={"source": "source.mov"}), \
                mock.patch("nuke_denoise_host.find_nodes", return_value=(noise, mock.Mock())), \
                redirect_stdout(io.StringIO()):
            diagnostic.start_host(self.path)
        root.setFrame.assert_called_once_with(1001)
        noise.sample.assert_called_once_with("red", 256, 128, 512, 256)
        noise.__getitem__.return_value.execute.assert_called_once_with()
        nuke.execute.assert_not_called()
        nuke.scriptExit.assert_not_called()
        report = json.loads((self.directory / "diagnostic-status.json").read_text())
        self.assertEqual(report["stage"], "profile_window_closed")
        self.assertFalse(report["render_started"])


if __name__ == "__main__":
    unittest.main()
