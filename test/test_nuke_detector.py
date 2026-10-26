from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke_detector
import app_cache


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
        started_at: float = 1000.0,
        environment: dict | None = None,
    ):
        self.pid = pid
        self.info = {"pid": pid, "name": name, "username": username}
        self._cmdline = cmdline or []
        self._open_files = open_files or []
        self._cwd = cwd
        self._started_at = started_at
        self._environment = environment or {}

    def create_time(self):
        return self._started_at

    def cmdline(self):
        return list(self._cmdline)

    def open_files(self):
        return [SimpleNamespace(path=path) for path in self._open_files]

    def cwd(self):
        return self._cwd

    def environ(self):
        return dict(self._environment)


class NukeDetectorTests(unittest.TestCase):
    def test_verified_denoise_is_excluded_while_artist_sessions_remain_detected(self):
        with app_cache.temporary_cache_lease(), tempfile.TemporaryDirectory(
            prefix="denoise-detector-", dir=app_cache.cache_directory("temp"),
        ) as folder:
            directory = Path(folder)
            background = FakeProc(pid=102, cmdline=["Nuke17.1", "--nukex", "-q", "-m", "4"],
                                  environment={"SHOTBOX_DENOISE_JOB_DIR": folder,
                                               "SHOTBOX_DENOISE_SUPERVISOR_PID": "100"})
            (directory / "child-102.json").write_text(json.dumps({"pid": 102, "created": 1000.0}))
            artist = FakeProc(pid=101, cmdline=["Nuke17.1", "--nukex", "/show/editing.nk"])
            with patch.object(nuke_detector.psutil, "process_iter", return_value=[background, artist]), \
                    patch.object(nuke_detector.getpass, "getuser", return_value="artist"), \
                    patch.object(background, "open_files", side_effect=AssertionError("Must skip background scripts")):
                snapshot = nuke_detector.scan_open_nuke_scripts()
            self.assertTrue(snapshot.complete, snapshot.errors)
            self.assertEqual([(item.pid, item.script_path) for item in snapshot.scripts], [(101, "/show/editing.nk")])
            self.assertEqual(snapshot.process_ids, {101, 102})
            self.assertFalse(snapshot.sessions_closed({(102, 1000.0)}))

    def test_denoise_environment_alone_or_stale_receipts_do_not_hide_unknown_sessions(self):
        with app_cache.temporary_cache_lease(), tempfile.TemporaryDirectory(
            prefix="denoise-detector-", dir=app_cache.cache_directory("temp"),
        ) as folder:
            receipt = Path(folder) / "child-102.json"
            proc = FakeProc(pid=102, environment={"SHOTBOX_DENOISE_JOB_DIR": folder,
                                                 "SHOTBOX_DENOISE_SUPERVISOR_PID": "100"})
            for record in (None, {"pid": 999, "created": 1000.0}, {"pid": 102, "created": 900.0}, []):
                with self.subTest(record=record):
                    if record is not None:
                        receipt.write_text(json.dumps(record))
                    with patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]), \
                            patch.object(nuke_detector.getpass, "getuser", return_value="artist"):
                        snapshot = nuke_detector.scan_open_nuke_scripts()
                    self.assertFalse(snapshot.complete)
                    self.assertIsInstance(snapshot.errors[0], nuke_detector.UnidentifiedNukeScriptError)

    def test_unreadable_environment_preserves_normal_detection_and_unknown_session_errors(self):
        artist = FakeProc(pid=101, cmdline=["Nuke17.1", "/show/editing.nk"])
        unknown = FakeProc(pid=102)
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[artist, unknown]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"), \
                patch.object(artist, "environ", side_effect=nuke_detector.psutil.AccessDenied(101)), \
                patch.object(unknown, "environ", side_effect=nuke_detector.psutil.AccessDenied(102)):
            snapshot = nuke_detector.scan_open_nuke_scripts()
        self.assertEqual([item.pid for item in snapshot.scripts], [101])
        self.assertIsInstance(snapshot.errors[0], nuke_detector.UnidentifiedNukeScriptError)

    def test_debug_output_shows_windows_script_paths_and_partial_scan_errors(self):
        script = nuke_detector.NukeScriptDetection(
            script_name="bam050.nk", script_path=r"Z:\PROJECTS\Bamboo HR\bam050.nk",
            machine_name="cerebro", detected_at_utc="2026-10-23T15:00:00Z",
            pid=101, source="cmdline",
        )
        snapshot = nuke_detector.NukeProcessSnapshot(
            scripts=[script], errors=[RuntimeError("Could not identify the script in Nuke process 102")],
        )
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            nuke_detector.print_snapshot_debug(snapshot)
        text = output.getvalue()
        self.assertIn("scripts=1 scan=incomplete", text)
        self.assertIn("pid=101 source=cmdline script=bam050.nk", text)
        self.assertIn(script.script_path, text)
        self.assertIn("Detection error: RuntimeError: Could not identify", text)

    def test_debug_output_distinguishes_no_open_scripts_from_failed_detection(self):
        for errors, expected in (
            ([], "No open .nk scripts detected"),
            ([RuntimeError("Cannot inspect Nuke")], "No scripts identified; detection is incomplete"),
        ):
            with self.subTest(errors=errors), patch("sys.stdout", new_callable=io.StringIO) as output:
                nuke_detector.print_snapshot_debug(nuke_detector.NukeProcessSnapshot(errors=errors))
            self.assertIn(expected, output.getvalue())
            if errors:
                self.assertNotIn("No open .nk scripts detected", output.getvalue())

    def test_unreadable_system_process_does_not_block_empty_scan(self):
        system = FakeProc(pid=4, name="", username="")
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[system]), \
                patch.object(nuke_detector.platform, "system", return_value="Windows"):
            self.assertEqual(nuke_detector.detect_open_nuke_scripts(strict=True), [])

    def test_unreadable_non_system_process_remains_uncertain(self):
        process = FakeProc(pid=101, name="", username="")
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[process]):
            snapshot = nuke_detector.scan_open_nuke_scripts()
        self.assertFalse(snapshot.complete)
        self.assertEqual(snapshot.process_ids, {101})
        self.assertFalse(snapshot.sessions_closed({(101, 1000.0)}))

    def test_windows_username_formats_match_on_either_side(self):
        for process_user, current_user in (
            (r"CEREBRO\Kieran", "kieran"), ("Kieran", r"CEREBRO\kieran"),
            ("Kieran@example.com", r"CEREBRO\Kieran"),
        ):
            with self.subTest(process_user=process_user, current_user=current_user):
                self.assertTrue(nuke_detector._username_matches(process_user, current_user))
        self.assertFalse(nuke_detector._username_matches(r"CEREBRO\Other", "Kieran"))

    def test_windows_paths_and_names_are_detected_on_any_test_host(self):
        proc = FakeProc(pid=101, name="Nuke17.1.exe", username=r"CEREBRO\artist", cmdline=[
            "Nuke17.1.exe", r'"Z:\PROJECTS\Job One\shot010\shot010_v001.NK"',
        ])
        with patch.object(nuke_detector.platform, "system", return_value="Windows"), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"), \
                patch.object(nuke_detector.psutil, "process_iter", return_value=[proc]):
            detections = nuke_detector.detect_open_nuke_scripts(strict=True)
        self.assertEqual(detections[0].script_path, r"Z:\PROJECTS\Job One\shot010\shot010_v001.NK")
        self.assertEqual(detections[0].script_name, "shot010_v001.NK")
        self.assertEqual(detections[0].process_started_at, 1000.0)

    def test_partial_scan_keeps_known_script_when_another_session_is_unidentified(self):
        known = FakeProc(pid=101, cmdline=["Nuke17.1", "/show/shot.nk"])
        unknown = FakeProc(pid=102)
        for processes in ([known, unknown], [unknown, known]):
            with self.subTest(order=[p.pid for p in processes]), \
                    patch.object(nuke_detector.psutil, "process_iter", return_value=processes), \
                    patch.object(nuke_detector.getpass, "getuser", return_value="artist"):
                snapshot = nuke_detector.scan_open_nuke_scripts()
            self.assertEqual([item.pid for item in snapshot.scripts], [101])
            self.assertEqual(snapshot.process_ids, {101, 102})
            self.assertFalse(snapshot.complete)
            self.assertTrue(snapshot.enumeration_complete)
            self.assertTrue(snapshot.can_release_unmatched)

    def test_permission_and_enumeration_failures_still_prevent_unmatched_cleanup(self):
        snapshot = nuke_detector.NukeProcessSnapshot(errors=[nuke_detector.psutil.AccessDenied(101)])
        self.assertFalse(snapshot.can_release_unmatched)
        snapshot.errors = [nuke_detector.UnidentifiedNukeScriptError("No script path")]
        snapshot.enumeration_complete = False
        self.assertFalse(snapshot.can_release_unmatched)

    def test_debug_explains_local_cleanup_can_continue_with_unidentified_script(self):
        snapshot = nuke_detector.NukeProcessSnapshot(errors=[
            nuke_detector.UnidentifiedNukeScriptError("No script path"),
        ])
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            nuke_detector.print_snapshot_debug(snapshot)
        self.assertIn("Unmatched locks owned by this machine can clear", output.getvalue())

    def test_crash_reporters_and_python_helpers_are_not_editing_sessions(self):
        processes = [FakeProc(pid=index, name=name) for index, name in enumerate((
            "NukeCrashReporter.exe", "NukeCrashHandler", "nuke-python", "python",
        ))]
        with patch.object(nuke_detector.psutil, "process_iter", return_value=processes):
            self.assertEqual(nuke_detector.detect_open_nuke_scripts(strict=True), [])

    def test_session_absence_uses_process_start_time_and_requires_complete_enumeration(self):
        snapshot = nuke_detector.NukeProcessSnapshot(process_ids={101}, process_started_at={101: 2000.0})
        self.assertTrue(snapshot.sessions_closed({(101, 1000.0)}))
        self.assertFalse(snapshot.sessions_closed({(101, 2000.0)}))
        snapshot.process_started_at[101] = None
        self.assertFalse(snapshot.sessions_closed({(101, 1000.0)}))
        snapshot.process_ids.clear()
        snapshot.enumeration_complete = False
        self.assertFalse(snapshot.sessions_closed({(101, 1000.0)}))

    def test_reused_pid_is_identified_even_when_new_process_is_not_nuke(self):
        reused = FakeProc(pid=101, name="python", started_at=2000.0)
        unknown = FakeProc(pid=102)
        with patch.object(nuke_detector.psutil, "process_iter", return_value=[reused, unknown]), \
                patch.object(nuke_detector.getpass, "getuser", return_value="artist"):
            snapshot = nuke_detector.scan_open_nuke_scripts(tracked_process_ids={101})
        self.assertFalse(snapshot.complete)
        self.assertTrue(snapshot.sessions_closed({(101, 1000.0)}))

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
        self.assertEqual([(item.pid, item.script_path) for item in result], [(1001, os.path.normpath("/show/bed030.nk"))])
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
