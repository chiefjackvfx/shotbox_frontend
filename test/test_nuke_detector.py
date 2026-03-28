from __future__ import annotations

import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pyqt_frontend.nuke_detector as nuke_detector


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
