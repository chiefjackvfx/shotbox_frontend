from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
from types import MethodType, SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QPushButton

import nuke_lock_worker as locks
import nuke_lock_utils
import widgets


class StopSignal(QObject):
    requested = pyqtSignal()


class NukeLockWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.api = mock.Mock()
        self.api.get_lock_owner_id.return_value = "chief@desk"
        self.api.get_lock_owner_ids.return_value = {"chief@desk"}
        self.api.get_system_id.return_value = "os-user@desk"
        self.api.update_shot_lock.return_value = {"ok": True}
        self.worker = locks.NukeLockWorker(api=self.api)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.shot = {"id": 7, "base_path": "/show/SH010", "nuke_in_use": f"chief@desk|{stamp}"}
        self.api.get_shots.return_value = [self.shot]
        self.script = "/show/SH010/nuke/SH010_v001.nk"
        self.detector_patcher = mock.patch.object(locks.nuke_detector, "scan_open_nuke_scripts", return_value=locks.nuke_detector.NukeProcessSnapshot([
            SimpleNamespace(script_path=self.script),
        ]))
        self.detector = self.detector_patcher.start()
        self.addCleanup(self.detector_patcher.stop)

    def claim(self, *, already_owned=False, script=None):
        self.worker.claim({
            "shot_id": 7, "script_path": script or self.script,
            "base_path": "/show/SH010", "owner_id": "chief@desk",
            "force": False, "already_owned": already_owned,
        })
        self.worker.finish_launch(7, True)
        self.api.update_shot_lock.reset_mock()

    def test_manual_previews_include_only_own_matching_locks_across_jobs(self):
        self.api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        self.api.get_shots.return_value = [
            {**self.shot, "title": "SH010"},
            {"id": 8, "title": "Hidden", "hidden": True, "nuke_in_use": "os-user@desk|20000101T000000Z"},
            {"id": 9, "nuke_in_use": "chief@desk"},
            {"id": 10, "nuke_in_use": "other@desk|20000101T000000Z"},
            {"id": 11, "nuke_in_use": "chief@other-machine|20000101T000000Z"},
            {"id": 12, "nuke_in_use": "None"},
            {"id": 13, "nuke_in_use": "chief@desk|20000101T000000Z"},
        ]
        self.worker.pending_launches[13] = False
        previews = []
        self.worker.clear_preview_ready.connect(previews.append)
        self.worker.preview_clear(True)
        self.worker.preview_clear(False)
        self.assertEqual([entry["shot_id"] for entry in previews[0]["shots"]], [8, 9])
        self.assertEqual([entry["shot_id"] for entry in previews[1]["shots"]], [7, 8, 9])
        self.assertEqual(previews[0]["shots"][0]["owner_id"], "os-user@desk")
        self.api.update_shot_lock.assert_not_called()

    def test_manual_preview_error_returns_no_candidates(self):
        self.api.get_shots.side_effect = RuntimeError("Server unavailable")
        previews = []
        self.worker.clear_preview_ready.connect(previews.append)
        self.worker.preview_clear(False)
        self.assertEqual(previews[0]["error"], "Server unavailable")
        self.assertEqual(previews[0]["shots"], [])
        self.api.update_shot_lock.assert_not_called()

    def test_manual_stale_clear_rechecks_freshness_owner_and_pending_launch(self):
        # These were local stale locks at preview time. Only the unchanged
        # stale lock may be cleared after the user confirms the preview.
        shots = {
            7: dict(self.shot),
            8: {"id": 8, "nuke_in_use": "other@desk|20000101T000000Z"},
            9: {"id": 9, "nuke_in_use": "None"},
            10: {"id": 10, "nuke_in_use": "chief@desk|20000101T000000Z"},
        }
        self.api.get_shot.side_effect = shots.__getitem__
        self.worker.pending_launches[11] = False
        finished, updates = [], []
        self.worker.clear_finished.connect(finished.append)
        self.worker.lock_updated.connect(lambda *args: updates.append(args))
        self.worker.clear_locks({"stale_only": True, "shots": [
            {"shot_id": shot_id, "owner_id": "chief@desk"} for shot_id in range(7, 12)
        ]})
        self.api.update_shot_lock.assert_called_once_with(10, release=True, owner_id="chief@desk")
        self.assertEqual([entry["shot_id"] for entry in finished[0]["cleared"]], [10])
        self.assertEqual([entry["shot_id"] for entry in finished[0]["skipped"]], [7, 8, 9, 11])
        self.assertEqual(finished[0]["failed"], [])
        self.assertIn((9, "None"), updates)
        self.assertIn((10, "None"), updates)

    def test_manual_all_clear_cannot_release_foreign_locks_even_with_foreign_candidates(self):
        self.api.get_shot.return_value = {"id": 8, "nuke_in_use": "other@desk|20000101T000000Z"}
        finished = []
        self.worker.clear_finished.connect(finished.append)
        self.worker.clear_locks({"stale_only": False, "shots": [
            {"shot_id": 8, "owner_id": "other@desk"},
        ]})
        self.api.update_shot_lock.assert_not_called()
        self.assertEqual(len(finished[0]["skipped"]), 1)

    def test_manual_all_clear_releases_active_alias_and_stops_heartbeats(self):
        self.claim()
        self.worker.owned_locks[7]["owner_id"] = "os-user@desk"
        self.api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        self.shot["nuke_in_use"] = self.shot["nuke_in_use"].replace("chief@desk", "os-user@desk")
        self.api.get_shot.return_value = dict(self.shot)
        finished = []
        self.worker.clear_finished.connect(finished.append)
        self.worker.clear_locks({"stale_only": False, "shots": [
            {"shot_id": 7, "owner_id": "os-user@desk"},
        ]})
        self.api.update_shot_lock.assert_called_once_with(7, release=True, owner_id="os-user@desk")
        self.assertNotIn(7, self.worker.owned_locks)
        self.assertEqual(len(finished[0]["cleared"]), 1)
        # A successful release is reflected by the server. Even though the
        # script remains detectable, the next tick must not reclaim the lock.
        self.shot["nuke_in_use"] = "None"
        self.worker.tick()
        self.api.update_shot_lock.assert_called_once()

    def test_manual_clear_continues_after_read_and_write_failures(self):
        for shot_id in (7, 8, 9, 10):
            self.worker.owned_locks[shot_id] = self.worker._new_state("chief@desk", "/show")
        def get_shot(shot_id):
            if shot_id == 7:
                raise RuntimeError("Read unavailable")
            return {"id": shot_id, "nuke_in_use": "chief@desk|20000101T000000Z"}
        self.api.get_shot.side_effect = get_shot
        self.api.update_shot_lock.side_effect = [
            {"ok": False, "status_code": 503, "detail": "Write unavailable"},
            {"ok": False, "status_code": 409, "conflict": True, "detail": "Changed owner"},
            {"ok": True},
        ]
        finished = []
        self.worker.clear_finished.connect(finished.append)
        self.worker.clear_locks({"stale_only": True, "shots": [
            {"shot_id": shot_id, "owner_id": "chief@desk"} for shot_id in (7, 8, 9, 10, 10)
        ]})
        self.assertEqual([entry["shot_id"] for entry in finished[0]["cleared"]], [10])
        self.assertEqual([entry["detail"] for entry in finished[0]["failed"]], [
            "Read unavailable", "Write unavailable", "Changed owner",
        ])
        self.assertEqual(set(self.worker.owned_locks), {7, 8})
        self.assertEqual(self.api.update_shot_lock.call_count, 3)

    def test_debug_scans_without_owned_locks_and_stops_when_disabled(self):
        self.api.get_shots.return_value = []
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot()
        with mock.patch("builtins.print") as output:
            self.worker.tick()
            self.detector.assert_not_called()
            output.assert_not_called()
            self.worker.configure_debug(True)
            self.assertEqual(output.call_count, 1)
            self.assertIn("No open .nk scripts detected", output.call_args.args[0])
            self.worker.tick()
            self.assertEqual(output.call_count, 2)
            self.worker.configure_debug(False)
            self.worker.tick()
            self.assertEqual(output.call_count, 2)
        self.assertEqual(self.detector.call_count, 2)
        self.api.update_shot_lock.assert_not_called()

    def test_debug_prints_one_scan_when_a_tick_recovers_new_locks(self):
        self.worker.debug = True
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot()
        with mock.patch("builtins.print") as output:
            self.worker.tick()
        self.detector.assert_called_once()
        self.assertEqual(output.call_count, 1)
        self.assertIn(7, self.worker.owned_locks)

    def test_sync_recovers_selected_user_lock_and_renews_it(self):
        self.worker.sync()
        self.worker.maintain()
        self.api.get_system_id.assert_not_called()
        self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="chief@desk")
        self.api.get_job.assert_not_called()

    def test_restart_renews_stale_alias_with_its_original_owner(self):
        self.api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        self.shot["nuke_in_use"] = "os-user@desk|20000101T000000Z"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.api.update_shot_lock.return_value = {
            "ok": True, "shot": {**self.shot, "nuke_in_use": f"os-user@desk|{stamp}"},
        }
        updates = []
        self.worker.lock_updated.connect(lambda shot_id, value: updates.append(value))
        self.worker.tick()
        self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="os-user@desk")
        self.assertFalse(nuke_lock_utils.parse_lock_info(updates[-1]).is_stale())

    def test_restart_releases_closed_stale_alias_after_grace(self):
        self.api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        self.shot["nuke_in_use"] = "os-user@desk|20000101T000000Z"
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot()
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.tick()
            self.api.update_shot_lock.assert_not_called()
            clock.return_value = 189
            self.worker.tick()
            self.api.update_shot_lock.assert_not_called()
            clock.return_value = 190
            # Simulate the server reflecting the successful release.
            self.api.update_shot_lock.side_effect = lambda *args, **kwargs: (
                self.shot.update(nuke_in_use="None") or {"ok": True, "shot": dict(self.shot)}
            )
            self.worker.tick()
        self.api.update_shot_lock.assert_called_once_with(7, release=True, owner_id="os-user@desk")
        self.assertNotIn(7, self.worker.owned_locks)

    def test_alias_recovery_does_not_renew_unconfirmed_stale_lock(self):
        self.api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        self.shot["nuke_in_use"] = "os-user@desk|20000101T000000Z"
        self.detector.side_effect = RuntimeError("Cannot inspect Nuke")
        self.worker.tick()
        self.api.update_shot_lock.assert_not_called()
        self.assertIn(7, self.worker.owned_locks)

    def test_takeover_by_another_known_alias_stops_the_existing_session(self):
        self.claim()
        self.api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        self.shot["nuke_in_use"] = "os-user@desk|20000101T000000Z"
        self.worker.sync()
        self.worker.maintain()
        self.api.update_shot_lock.assert_not_called()
        self.assertNotIn(7, self.worker.owned_locks)

    def test_startup_cleans_all_closed_stale_locks_and_keeps_open_and_foreign_locks(self):
        stale = "chief@desk|20000101T000000Z"
        shots = [
            {"id": 7, "timeline": 1, "base_path": "/show/SH010", "nuke_in_use": stale},
            {"id": 8, "timeline": 2, "base_path": "/other-job/SH020", "nuke_in_use": stale, "hidden": True},
            {"id": 9, "timeline": 3, "base_path": "/show/SH030", "nuke_in_use": stale},
            {"id": 10, "base_path": "/show/SH040", "nuke_in_use": "chief@other-desk|20000101T000000Z"},
            {"id": 11, "base_path": "/show/SH050", "nuke_in_use": "other@desk|20000101T000000Z"},
        ]
        self.api.get_shots.return_value = shots
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot([
            SimpleNamespace(script_path="/show/SH030/nuke/SH030_v001.nk"),
        ])
        def update(shot_id, *, release=False, **kwargs):
            shot = next(shot for shot in shots if shot["id"] == shot_id)
            shot["nuke_in_use"] = "None" if release else "chief@desk|20990101T000000Z"
            return {"ok": True, "shot": dict(shot)}
        self.api.update_shot_lock.side_effect = update
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.start()
            self.addCleanup(self.worker.timer.stop)
            self.assertEqual(shots[0]["nuke_in_use"], stale)
            self.assertEqual(shots[1]["nuke_in_use"], stale)
            self.api.update_shot_lock.assert_called_once_with(9, release=False, owner_id="chief@desk")
            clock.return_value = 190
            self.worker.tick()
        self.assertEqual([shot["nuke_in_use"] for shot in shots[:2]], ["None", "None"])
        self.assertEqual(set(self.worker.owned_locks), {9})
        self.assertEqual(shots[3]["nuke_in_use"], "chief@other-desk|20000101T000000Z")
        self.assertEqual(shots[4]["nuke_in_use"], "other@desk|20000101T000000Z")
        self.assertTrue(all(call.args[0] in (7, 8, 9) for call in self.api.update_shot_lock.call_args_list))

    def test_frameservers_do_not_block_closed_bed_shots_releasing_while_bed030_is_open(self):
        from test.test_nuke_detector import FakeProc

        self.detector_patcher.stop()  # Exercise the actual detector and worker together.
        stale = "chief@desk|20000101T000000Z"
        shots = [
            {"id": 7, "title": "bed010", "base_path": "/show/bed010", "nuke_in_use": stale},
            {"id": 8, "title": "bed020", "base_path": "/show/bed020", "nuke_in_use": stale},
            {"id": 9, "title": "bed030", "base_path": "/show/bed030", "nuke_in_use": stale},
        ]
        self.api.get_shots.return_value = shots
        gui = FakeProc(pid=123, name="Nuke17.1", cmdline=[
            "Nuke17.1", "--nukex", "/show/bed030/scripts/bed030_v010.nk",
        ])
        worker_script = "/opt/Nuke17.1v2/pythonextensions/site-packages/foundry/frameserver/nuke/workerapplication.py"
        workers = [
            FakeProc(pid=124, name="Nuke17.1", cmdline=["Nuke17.1", "-ti", worker_script]),
            FakeProc(pid=125, name="Nuke17.1", cmdline=["Nuke17.1", "-m", "-t", "-i", "-c", worker_script]),
        ]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        def update(shot_id, *, release=False, **kwargs):
            shot = next(shot for shot in shots if shot["id"] == shot_id)
            shot["nuke_in_use"] = "None" if release else f"chief@desk|{stamp}"
            return {"ok": True, "shot": dict(shot)}
        self.api.update_shot_lock.side_effect = update
        with mock.patch.object(locks.nuke_detector.psutil, "process_iter", return_value=[workers[0], gui, workers[1]]), \
                mock.patch.object(locks.nuke_detector.getpass, "getuser", return_value="artist"), \
                mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.tick()
            self.api.update_shot_lock.assert_called_once_with(9, release=False, owner_id="chief@desk")
            clock.return_value = 190
            self.worker.tick()
        self.assertEqual([shot["nuke_in_use"] for shot in shots[:2]], ["None", "None"])
        self.assertFalse(nuke_lock_utils.parse_lock_info(shots[2]["nuke_in_use"]).is_stale())
        self.assertEqual(set(self.worker.owned_locks), {9})
        released = {call.args[0] for call in self.api.update_shot_lock.call_args_list if call.kwargs.get("release")}
        self.assertEqual(released, {7, 8})

    def test_recovered_stale_lock_stays_stale_when_detection_is_uncertain(self):
        self.shot["nuke_in_use"] = "chief@desk|20000101T000000Z"
        self.detector.side_effect = RuntimeError("Cannot inspect Nuke")
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.tick()
            clock.return_value = 1000
            self.worker.tick()
        self.assertIn(7, self.worker.owned_locks)
        self.api.update_shot_lock.assert_not_called()

    def test_unidentified_nuke_session_does_not_hold_other_local_bamboo_locks(self):
        from test.test_nuke_detector import FakeProc
        self.detector_patcher.stop()
        root = "/Volumes/projects/PROJECTS/C2255_Bamboo_HR/VFX/BAMBOO_HR"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        shots = [
            {"id": 7, "base_path": f"{root}/bam030", "nuke_in_use": "chief@desk|20000101T000000Z"},
            {"id": 8, "base_path": f"{root}/bam070", "nuke_in_use": f"chief@desk|{stamp}"},
            {"id": 9, "base_path": f"{root}/bam010", "nuke_in_use": "chief@desk|20000101T000000Z"},
            {"id": 10, "base_path": f"{root}/bam040", "nuke_in_use": "kieran@cerebro|20000101T000000Z"},
            {"id": 11, "base_path": f"{root}/bam050", "nuke_in_use": f"kieran@cerebro|{stamp}"},
            {"id": 12, "base_path": f"{root}/bam060", "nuke_in_use": "other@desk|20000101T000000Z"},
            {"id": 13, "base_path": f"{root}/bam160", "nuke_in_use": "os-user@desk|20000101T000000Z", "hidden": True},
        ]
        self.api.get_shots.return_value = shots
        foreign_before = [dict(shot) for shot in shots[3:6]]
        def update(shot_id, *, release=False, owner_id):
            shot = next(shot for shot in shots if shot["id"] == shot_id)
            shot["nuke_in_use"] = "None" if release else f"{owner_id}|{stamp}"
            return {"ok": True, "shot": dict(shot)}
        self.api.update_shot_lock.side_effect = update
        processes = [
            FakeProc(pid=101, cmdline=["Nuke17.1", f"{root}/bam030/scripts/bam030_v004.nk"]),
            FakeProc(pid=102, cmdline=["Nuke17.1", f"{root}/bam070/scripts/bam070_v010.nk"]),
            FakeProc(pid=103, cmdline=["Nuke17.1", "--nukex"]),
        ]
        with mock.patch.object(locks.nuke_detector.psutil, "process_iter", return_value=processes), \
                mock.patch.object(locks.nuke_detector.getpass, "getuser", return_value="artist"), \
                mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.tick()
            self.assertFalse(any(call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))
            clock.return_value = 189
            self.worker.tick()
            self.assertFalse(any(call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))
            clock.return_value = 190
            self.worker.tick()
        released = {call.args[0] for call in self.api.update_shot_lock.call_args_list if call.kwargs["release"]}
        self.assertEqual(released, {9, 13})
        self.assertEqual([shots[2]["nuke_in_use"], shots[6]["nuke_in_use"]], ["None", "None"])
        self.assertEqual([dict(shot) for shot in shots[3:6]], foreign_before)
        self.assertTrue(all(call.args[0] in {7, 8, 9, 13} for call in self.api.update_shot_lock.call_args_list))
        self.assertFalse(nuke_lock_utils.parse_lock_info(shots[0]["nuke_in_use"]).is_stale())
        self.assertFalse(nuke_lock_utils.parse_lock_info(shots[1]["nuke_in_use"]).is_stale())

    def test_unmatched_local_lock_is_not_renewed_during_release_grace(self):
        self.claim()
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot(errors=[
            locks.nuke_detector.UnidentifiedNukeScriptError("No script path"),
        ])
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.maintain()
            clock.return_value = 189
            self.worker.maintain()
        self.api.update_shot_lock.assert_not_called()

    def test_windows_and_linux_release_closed_bamboo_session_beside_unknown_session(self):
        from test.test_nuke_detector import FakeProc
        self.detector_patcher.stop()
        base = "/Volumes/projects/PROJECTS/C2255_Bamboo_HR/VFX/BAMBOO_HR/bam050"
        for system, username, script in (
            ("Windows", r"CEREBRO\artist", r"Z:\PROJECTS\C2255_Bamboo_HR\VFX\BAMBOO_HR\bam050\scripts\bam050_v001.nk"),
            ("Linux", "artist", "/mnt/projects/PROJECTS/C2255_Bamboo_HR/VFX/BAMBOO_HR/bam050/scripts/bam050_v001.nk"),
        ):
            with self.subTest(system=system):
                worker = locks.NukeLockWorker(api=self.api)
                self.shot.update(base_path=base, nuke_in_use="chief@desk|20000101T000000Z")
                known = FakeProc(pid=101, name="Nuke17.1.exe" if system == "Windows" else "Nuke17.1",
                                 cmdline=["Nuke17.1", script])
                unknown = FakeProc(pid=102)
                system_proc = FakeProc(pid=4, name="", username="")
                with mock.patch.object(locks.nuke_detector.platform, "system", return_value=system), \
                        mock.patch.object(locks.nuke_detector.getpass, "getuser", return_value=username), \
                        mock.patch.object(locks.nuke_detector.psutil, "process_iter", return_value=[known, unknown, system_proc]) as processes, \
                        mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
                    self.api.update_shot_lock.reset_mock()
                    worker.tick()
                    # A positively identified open script recovers even when
                    # another Nuke session cannot be identified.
                    self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="chief@desk")
                    self.assertEqual(worker.owned_locks[7]["process_sessions"], {(101, 1000.0)})
                    processes.return_value = [unknown, system_proc]
                    clock.return_value = 150
                    worker.maintain()
                    self.assertIn(7, worker.owned_locks)
                    clock.return_value = 239
                    worker.maintain()
                    self.assertIn(7, worker.owned_locks)
                    clock.return_value = 240
                    worker.maintain()
                self.api.update_shot_lock.assert_called_with(7, release=True, owner_id="chief@desk")
                self.assertNotIn(7, worker.owned_locks)

    def test_unreadable_live_tracked_process_does_not_release(self):
        self.claim()
        self.worker.owned_locks[7]["process_sessions"] = {(101, 1000.0)}
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot(
            process_ids={101}, errors=[RuntimeError("Cannot read live process")],
        )
        with mock.patch.object(locks.time, "monotonic", side_effect=[100, 1000]):
            self.worker.maintain()
            self.worker.maintain()
        self.assertIn(7, self.worker.owned_locks)
        self.assertFalse(any(call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))

    def test_unreadable_duplicate_session_stays_tracked_when_other_session_closes(self):
        self.claim()
        self.worker.owned_locks[7]["process_sessions"] = {(101, 1000.0), (102, 1000.0)}
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot(
            scripts=[SimpleNamespace(script_path=self.script, pid=101, process_started_at=1000.0)],
            process_ids={101, 102}, errors=[RuntimeError("Cannot read process 102")],
        )
        self.worker.maintain()
        self.assertIn((102, 1000.0), self.worker.owned_locks[7]["process_sessions"])
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot(
            process_ids={102}, errors=[RuntimeError("Cannot read process 102")],
        )
        with mock.patch.object(locks.time, "monotonic", side_effect=[100, 1000]):
            self.worker.maintain()
            self.worker.maintain()
        self.assertIn(7, self.worker.owned_locks)
        self.assertFalse(any(call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))

    @unittest.skipUnless(sys.platform.startswith("linux"), "Uses native Linux process naming")
    def test_native_linux_process_exit_releases_lock_beside_unidentified_session(self):
        self.detector_patcher.stop()
        child_code = (
            "import ctypes,time; "
            "ctypes.CDLL(None).prctl(15,b'Nuke17.1',0,0,0); "
            "print('ready',flush=True); time.sleep(30)"
        )
        def cleanup(process):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)
            process.stdout.close()

        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "SH010.nk"
            script.touch()
            known = subprocess.Popen([sys.executable, "-c", child_code, str(script)], stdout=subprocess.PIPE, text=True)
            self.addCleanup(cleanup, known)
            unknown = subprocess.Popen([sys.executable, "-c", child_code], stdout=subprocess.PIPE, text=True)
            self.addCleanup(cleanup, unknown)
            self.assertEqual(known.stdout.readline().strip(), "ready")
            self.assertEqual(unknown.stdout.readline().strip(), "ready")
            self.shot["base_path"] = temporary
            real_process_iter = locks.nuke_detector.psutil.process_iter
            def children_only(attrs):
                return (process for process in real_process_iter(attrs) if process.pid in {known.pid, unknown.pid})
            with mock.patch.object(locks.nuke_detector.psutil, "process_iter", side_effect=children_only), \
                    mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
                self.worker.tick()
                self.assertTrue(self.worker.owned_locks[7]["process_sessions"])
                known.terminate()
                known.wait(timeout=5)
                clock.return_value = 150
                self.worker.maintain()
                clock.return_value = 240
                self.worker.maintain()
            self.api.update_shot_lock.assert_called_with(7, release=True, owner_id="chief@desk")
            self.assertNotIn(7, self.worker.owned_locks)
            self.assertIsNone(unknown.poll())

    def test_recovered_stale_lock_with_unknown_path_clears_when_unmatched(self):
        self.shot["nuke_in_use"] = "chief@desk|20000101T000000Z"
        self.shot["base_path"] = ""
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.tick()
            clock.return_value = 1000
            self.worker.maintain()
        self.assertNotIn(7, self.worker.owned_locks)
        self.api.update_shot_lock.assert_called_once_with(7, release=True, owner_id="chief@desk")

    def test_discovery_failure_does_not_stop_known_session_heartbeat(self):
        self.claim()
        self.api.get_shots.side_effect = RuntimeError("Server unavailable")
        self.worker.tick()
        self.assertIn(7, self.worker.owned_locks)
        self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="chief@desk")

    def test_sync_reports_foreign_lock_release_without_repeating_unchanged_updates(self):
        self.shot["nuke_in_use"] = "another@desk|20000101T000000Z"
        updates = []
        self.worker.lock_updated.connect(lambda *args: updates.append(args))
        self.worker.sync()
        self.worker.sync()
        self.shot["nuke_in_use"] = "None"
        self.worker.sync()
        self.assertEqual(updates, [(7, "another@desk|20000101T000000Z"), (7, "None")])

    def test_foreign_lock_becomes_visibly_stale_without_server_value_changing(self):
        timestamp = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
        self.shot["nuke_in_use"] = f"another@desk|{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
        updates = []
        self.worker.lock_updated.connect(lambda shot_id, value: updates.append(
            (shot_id, nuke_lock_utils.parse_lock_info(value).is_stale()),
        ))
        with mock.patch.object(nuke_lock_utils, "datetime", wraps=datetime) as clock:
            clock.now.return_value = timestamp
            self.worker.sync()
            clock.now.return_value = timestamp + timedelta(seconds=181)
            self.worker.sync()
            self.worker.sync()
        self.assertEqual(updates, [(7, False), (7, True)])

    def test_sync_preserves_opened_version_and_duplicate_sessions(self):
        self.claim()
        self.claim(already_owned=True, script="/show/SH010/nuke/SH010_v002.nk")
        self.worker.sync()
        with mock.patch.object(locks.time, "monotonic", return_value=1000):
            self.worker.maintain()
        self.assertIn(7, self.worker.owned_locks)
        self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="chief@desk")

    def test_restart_matches_open_version_within_shot_directory(self):
        self.worker.sync()
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot([
            SimpleNamespace(script_path="/show/SH010/nuke/SH010_v005.nk"),
        ])
        with mock.patch.object(locks.time, "monotonic", return_value=1000):
            self.worker.maintain()
            self.worker.maintain()
        self.assertTrue(all(not call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))

    def test_uncertain_detection_preserves_lock_and_continues_heartbeat(self):
        self.claim()
        self.detector.side_effect = RuntimeError("Cannot inspect Nuke")
        self.worker.maintain()
        self.assertIn(7, self.worker.owned_locks)
        self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="chief@desk")

    def test_confirmed_absence_releases_after_grace_and_retries_failure(self):
        self.claim()
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot()
        self.api.update_shot_lock.side_effect = [
            {"ok": False, "status_code": None}, {"ok": True},
        ]
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.maintain()
            clock.return_value = 190
            self.worker.maintain()
            self.assertIn(7, self.worker.owned_locks)
            clock.return_value = 235
            self.worker.maintain()
        self.assertNotIn(7, self.worker.owned_locks)
        self.assertEqual([call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list], [True, True])

    def test_uncertain_scan_restarts_continuous_absence_grace(self):
        self.claim()
        self.detector.side_effect = [
            locks.nuke_detector.NukeProcessSnapshot(), RuntimeError("Unknown session"),
            locks.nuke_detector.NukeProcessSnapshot(), locks.nuke_detector.NukeProcessSnapshot(),
        ]
        with mock.patch.object(locks.time, "monotonic", side_effect=[100, 150, 240, 300]):
            for _ in range(4):
                self.worker.maintain()
        self.assertIn(7, self.worker.owned_locks)
        self.assertTrue(all(not call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))

    def test_changed_release_delay_applies_to_existing_closed_session(self):
        self.claim()
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot()
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.maintain()
            self.worker.configure_release(180, True)
            clock.return_value = 190
            self.worker.maintain()
            self.assertIn(7, self.worker.owned_locks)
            self.assertFalse(any(call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))
            clock.return_value = 280
            self.worker.maintain()
        self.assertNotIn(7, self.worker.owned_locks)
        self.assertTrue(self.api.update_shot_lock.call_args.kwargs["release"])

    def test_disabled_release_retains_closed_lock_without_heartbeats_until_reenabled(self):
        self.claim()
        self.worker.configure_release(90, False)
        self.detector.return_value = locks.nuke_detector.NukeProcessSnapshot()
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.maintain()
            self.api.update_shot_lock.reset_mock()
            clock.return_value = 190
            self.worker.maintain()
            self.assertIn(7, self.worker.owned_locks)
            self.api.update_shot_lock.assert_not_called()
            # Subsequent uncertainty must not revive a confirmed closed script.
            self.detector.side_effect = RuntimeError("Cannot inspect Nuke")
            self.worker.maintain()
            self.api.update_shot_lock.assert_not_called()
            self.detector.side_effect = None
            self.worker.maintain()
            self.worker.configure_release(90, True)
            clock.return_value = 280
            self.worker.maintain()
        self.api.update_shot_lock.assert_called_once_with(7, release=True, owner_id="chief@desk")
        self.assertNotIn(7, self.worker.owned_locks)

    def test_transient_heartbeat_failure_retries_but_conflict_stops_tracking(self):
        self.claim()
        self.api.update_shot_lock.side_effect = [
            {"ok": False, "status_code": None}, {"ok": False, "conflict": True},
        ]
        self.worker.maintain()
        self.assertIn(7, self.worker.owned_locks)
        self.worker.maintain()
        self.assertNotIn(7, self.worker.owned_locks)

    def test_selected_user_change_keeps_existing_lock_owner_for_writes(self):
        self.claim()
        self.api.get_lock_owner_id.return_value = "replacement@desk"
        self.worker.sync()
        self.worker.maintain()
        self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="chief@desk")

    def test_empty_snapshot_does_not_stop_open_session_heartbeat(self):
        self.claim()
        self.shot["nuke_in_use"] = "None"
        self.worker.sync()
        self.worker.maintain()
        self.api.update_shot_lock.assert_called_once_with(7, release=False, owner_id="chief@desk")

    def test_foreign_takeover_stops_heartbeats(self):
        self.claim()
        self.shot["nuke_in_use"] = "another@machine|20000101T000000Z"
        self.worker.sync()
        self.worker.maintain()
        self.api.update_shot_lock.assert_not_called()

    def test_failed_duplicate_launch_keeps_existing_lock(self):
        self.claim()
        self.worker.claim({
            "shot_id": 7, "script_path": self.script, "base_path": "/show/SH010",
            "owner_id": "chief@desk", "force": False, "already_owned": True,
        })
        self.api.update_shot_lock.reset_mock()
        self.worker.finish_launch(7, False)
        self.assertIn(7, self.worker.owned_locks)
        self.api.update_shot_lock.assert_not_called()

    def test_failed_new_launch_retries_cleanup_until_release_succeeds(self):
        self.worker.claim({
            "shot_id": 7, "script_path": self.script, "base_path": "/show/SH010",
            "owner_id": "chief@desk", "force": False, "already_owned": False,
        })
        self.api.update_shot_lock.reset_mock()
        self.api.update_shot_lock.side_effect = [{"ok": False}, {"ok": True}]
        self.worker.finish_launch(7, False)
        self.assertIn(7, self.worker.owned_locks)
        self.worker.maintain()
        self.assertNotIn(7, self.worker.owned_locks)
        self.assertTrue(all(call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))

    def test_shutdown_cleans_unlaunched_claims_but_keeps_running_sessions(self):
        self.claim()
        self.worker.claim({
            "shot_id": 8, "script_path": "/show/SH020.nk", "owner_id": "chief@desk",
            "force": False, "already_owned": False,
        })
        self.api.update_shot_lock.reset_mock()
        self.worker.stop()
        self.assertIn(7, self.worker.owned_locks)
        self.assertNotIn(8, self.worker.owned_locks)
        self.api.update_shot_lock.assert_called_once_with(8, release=True, owner_id="chief@desk")

    def test_worker_heartbeats_while_gui_thread_is_blocked(self):
        self.claim()
        self.api.get_shots.return_value = []
        heartbeat = threading.Event()
        thread_ids = []
        def update(*args, **kwargs):
            thread_ids.append(threading.get_ident())
            heartbeat.set()
            return {"ok": True}
        self.api.update_shot_lock.side_effect = update
        self.worker.timer.setInterval(20)
        thread = QThread()
        stop = StopSignal()
        stop.requested.connect(self.worker.stop)
        self.worker.stopped.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        self.worker.moveToThread(thread)
        thread.started.connect(self.worker.start)
        thread.finished.connect(self.worker.deleteLater)
        thread.start()
        try:
            self.assertTrue(heartbeat.wait(1), "Heartbeat depended on GUI event processing")
            self.assertTrue(all(ident != threading.get_ident() for ident in thread_ids))
        finally:
            thread.requestInterruption()
            stop.requested.emit()
            self.assertTrue(thread.wait(1000))


class NukeLockDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def card(self, timestamp):
        api = mock.Mock()
        api.get_lock_owner_id.return_value = "chief@desk"
        api.get_lock_owner_ids.return_value = {"chief@desk", "os-user@desk"}
        api.get_system_id.return_value = "os-user@desk"
        card = SimpleNamespace(
            data={"nuke_in_use": f"chief@desk|{timestamp}"}, _api=api,
            _get_nuke_base_label=lambda: "SH010_v001.nk", btn_open_nuke=QPushButton(),
            _compact_mode=False, _v02_grid=True,
        )
        for name in ("set_lock_state", "_set_nuke_button_label", "_set_nuke_button_tooltip"):
            setattr(card, name, MethodType(getattr(widgets.ShotCard, name), card))
        return card

    def test_selected_user_lock_is_shown_as_mine(self):
        card = self.card(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        widgets.ShotCard.refresh_lock_state_from_data(card)
        self.assertEqual(card.btn_open_nuke.property("lock_state"), "mine")
        self.assertIn("(You)", card.btn_open_nuke.text())

    def test_recovered_alias_is_shown_as_mine_after_heartbeat(self):
        card = self.card(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        card.data["nuke_in_use"] = card.data["nuke_in_use"].replace("chief@desk", "os-user@desk")
        widgets.ShotCard.refresh_lock_state_from_data(card)
        self.assertEqual(card.btn_open_nuke.property("lock_state"), "mine")

    def test_stale_lock_keeps_owner_and_explains_overdue_heartbeat(self):
        card = self.card("20000101T000000Z")
        widgets.ShotCard.refresh_lock_state_from_data(card)
        self.assertEqual(card.btn_open_nuke.property("lock_state"), "stale")
        self.assertIn("chief", card.btn_open_nuke.text())
        self.assertIn("stale", card.btn_open_nuke.text())
        self.assertIn("may still be open", card.btn_open_nuke.toolTip())
        card._compact_mode = True
        card._set_nuke_button_label()
        self.assertEqual(card.btn_open_nuke.text(), "Stale lock: chief")


if __name__ == "__main__":
    unittest.main()
