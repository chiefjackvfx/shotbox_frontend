from __future__ import annotations

import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke_detector


class FakeProc:
    def __init__(
        self,
        *,
        pid: int,
        name: str = "Nuke15.0",
        username: str = "artist",
        cmdline: list[str] | None = None,
        open_files: list[str] | None = None,
        cwd: str = "/tmp",
    ):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "username": username}
        self._cmdline = cmdline or []
        self._open_files = open_files or []
        self._cwd = cwd

    def cmdline(self):
        return list(self._cmdline)

    def open_files(self):
        return [SimpleNamespace(path=path) for path in self._open_files]

    def cwd(self):
        return self._cwd


class NukeDetectorTests(unittest.TestCase):
    def test_strict_detection_ignores_frameserver_workers_beside_open_script(self):
        gui = FakeProc(pid=1001, name="Nuke17.1", cmdline=["Nuke17.1", "--nukex", "/show/bed030.nk"])
        worker_script = "/opt/Nuke17.1v2/pythonextensions/site-packages/foundry/frameserver/nuke/workerapplication.py"
        workers = [
            FakeProc(pid=1002, name="Nuke17.1", cmdline=["Nuke17.1", "-ti", worker_script, "--parenturl=tcp://localhost:5640"]),
            FakeProc(pid=1003, name="Nuke17.1", cmdline=["Nuke17.1", "-m", "-t", "-i", "-c", worker_script]),
        ]
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[workers[0], gui, workers[1]]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"), \
                patch.object(workers[0], "open_files", side_effect=nuke_detector.psutil.AccessDenied(1002)) as files:
            result = nuke_detector.detect_open_nuke_scripts(strict=True)
        self.assertEqual([(item.pid, item.script_path) for item in result], [(1001, "/show/bed030.nk")])
        files.assert_not_called()

    def test_frameserver_workers_alone_count_as_no_open_editing_session(self):
        worker_script = "/opt/Nuke17.1v2/pythonextensions/site-packages/foundry/frameserver/nuke/workerapplication.py"
        proc = FakeProc(pid=1002, cmdline=["Nuke17.1", "-ti", worker_script])
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"):
            self.assertEqual(nuke_detector.detect_open_nuke_scripts(strict=True), [])

    def test_frameserver_recognition_handles_windows_paths(self):
        proc = FakeProc(pid=1002, cmdline=[
            "Nuke17.1.exe", "-t", "-i",
            r'"C:\Program Files\Nuke17.1v2\pythonextensions\site-packages\Foundry\FrameServer\Nuke\workerapplication.py"',
        ])
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"):
            self.assertEqual(nuke_detector.detect_open_nuke_scripts(strict=True), [])

    def test_unidentified_sessions_are_not_excluded_by_partial_worker_matches(self):
        worker_script = "/opt/Nuke17.1v2/pythonextensions/site-packages/foundry/frameserver/nuke/workerapplication.py"
        commands = [
            ["Nuke17.1", "-t", "/show/workerapplication.py"],
            ["Nuke17.1", "-ti", "/show/custom_render.py"],
            ["Nuke17.1", worker_script],
            ["Nuke17.1", "-t", worker_script + ".bak"],
        ]
        for command in commands:
            with self.subTest(command=command):
                proc = FakeProc(pid=1002, cmdline=command)
                with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                        patch.object(nuke_detector.getpass, "getuser", return_value="artist"):
                    with self.assertRaisesRegex(RuntimeError, "Could not identify"):
                        nuke_detector.detect_open_nuke_scripts(strict=True)

    def test_inaccessible_worker_command_line_remains_uncertain(self):
        proc = FakeProc(pid=1002, cmdline=["Nuke17.1", "-ti", "/foundry/frameserver/nuke/workerapplication.py"])
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"), \
                patch.object(proc, "cmdline", side_effect=nuke_detector.psutil.AccessDenied(1002)):
            with self.assertRaises(nuke_detector.psutil.AccessDenied):
                nuke_detector.detect_open_nuke_scripts(strict=True)

    def test_strict_detection_rejects_unknown_process_owner(self):
        proc = FakeProc(pid=1001, username="", cmdline=["Nuke15.0", "/show/shot.nk"])
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]):
            with self.assertRaisesRegex(RuntimeError, "owner"):
                nuke_detector.detect_open_nuke_scripts(strict=True)

    def test_strict_detection_cannot_guess_relative_script_directory(self):
        proc = FakeProc(pid=1001, cmdline=["Nuke15.0", "shot.nk"])
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"), \
                patch.object(proc, "cwd", side_effect=nuke_detector.psutil.AccessDenied(1001)):
            with self.assertRaises(nuke_detector.psutil.AccessDenied):
                nuke_detector.detect_open_nuke_scripts(strict=True)

    def test_strict_detection_reports_inaccessible_nuke_process(self):
        proc = FakeProc(pid=1001)
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"), \
                patch.object(proc, "cmdline", side_effect=nuke_detector.psutil.AccessDenied(1001)):
            self.assertEqual(nuke_detector.detect_open_nuke_scripts(), [])
            with self.assertRaises(nuke_detector.psutil.AccessDenied):
                nuke_detector.detect_open_nuke_scripts(strict=True)

    def test_strict_detection_reports_unidentified_running_session(self):
        proc = FakeProc(pid=1001, cmdline=["Nuke15.0"])
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"):
            with self.assertRaisesRegex(RuntimeError, "Could not identify"):
                nuke_detector.detect_open_nuke_scripts(strict=True)

    def test_cmdline_detection_preferred(self):
        proc = FakeProc(
            pid=1001,
            cmdline=["Nuke15.0", "/show/shots/sho010/scripts/sho010_v03.nk"],
            open_files=["/show/shots/sho020/scripts/sho020_v01.nk"],
        )
        with (
            patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]),
            patch.object(nuke_detector.getpass, "getuser", return_value="artist"),
            patch.object(nuke_detector.socket, "gethostname", return_value="ws-01"),
        ):
            result = nuke_detector.detect_open_nuke_scripts()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "cmdline")
        self.assertEqual(result[0].script_name, "sho010_v03.nk")
        self.assertEqual(result[0].machine_name, "ws-01")
        self.assertRegex(result[0].detected_at_utc, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_open_files_fallback_when_no_cmdline_script(self):
        proc = FakeProc(
            pid=1002,
            cmdline=["Nuke15.0", "--safe"],
            open_files=["/show/shots/sho030/scripts/sho030_v07.nk"],
        )
        with (
            patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]),
            patch.object(nuke_detector.getpass, "getuser", return_value="artist"),
        ):
            result = nuke_detector.detect_open_nuke_scripts()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].source, "open_files")
        self.assertEqual(result[0].script_name, "sho030_v07.nk")

    def test_deduplicates_same_pid_and_path(self):
        proc = FakeProc(
            pid=1003,
            cmdline=[
                "Nuke15.0",
                "/show/shots/sho040/scripts/sho040_v01.nk",
                "/show/shots/sho040/scripts/sho040_v01.nk",
            ],
        )
        with (
            patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]),
            patch.object(nuke_detector.getpass, "getuser", return_value="artist"),
        ):
            result = nuke_detector.detect_open_nuke_scripts()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].pid, 1003)

    def test_current_user_filter(self):
        mine = FakeProc(
            pid=1004,
            username="artist",
            cmdline=["Nuke15.0", "/show/shots/sho050/scripts/sho050_v01.nk"],
        )
        other = FakeProc(
            pid=1005,
            username="someone_else",
            cmdline=["Nuke15.0", "/show/shots/sho060/scripts/sho060_v01.nk"],
        )
        with (
            patch.object(nuke_detector.psutil, "process_iter", return_value=[mine, other]),
            patch.object(nuke_detector.getpass, "getuser", return_value="artist"),
        ):
            result = nuke_detector.detect_open_nuke_scripts(current_user_only=True)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].pid, 1004)

    def test_empty_result_and_primary_none(self):
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[]):
            result = nuke_detector.detect_open_nuke_scripts()
            primary = nuke_detector.detect_primary_open_nuke_script()

        self.assertEqual(result, [])
        self.assertIsNone(primary)

    def test_debug_run_exit_codes(self):
        with patch.object(nuke_detector, "detect_open_nuke_scripts", return_value=[]):
            with patch("sys.stdout", new_callable=io.StringIO):
                code = nuke_detector.debug_run()
        self.assertEqual(code, 1)

        one = nuke_detector.NukeScriptDetection(
            script_name="sho010_v01.nk",
            script_path="/show/shots/sho010/scripts/sho010_v01.nk",
            machine_name="ws-01",
            detected_at_utc="2026-03-06T10:00:00Z",
            pid=2222,
            source="cmdline",
        )
        with patch.object(nuke_detector, "detect_open_nuke_scripts", return_value=[one]):
            with patch("sys.stdout", new_callable=io.StringIO):
                code = nuke_detector.debug_run()
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
