from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
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
        self.detector_patcher = mock.patch.object(locks.nuke_detector, "detect_open_nuke_scripts", return_value=[
            SimpleNamespace(script_path=self.script),
        ])
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
        self.detector.return_value = []
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
        self.detector.return_value = [SimpleNamespace(script_path="/show/SH030/nuke/SH030_v001.nk")]
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

    def test_recovered_stale_lock_with_unknown_path_is_kept_while_nuke_is_running(self):
        self.shot["nuke_in_use"] = "chief@desk|20000101T000000Z"
        self.shot["base_path"] = ""
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.tick()
            clock.return_value = 1000
            self.worker.tick()
        self.assertIn(7, self.worker.owned_locks)
        self.api.update_shot_lock.assert_not_called()

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
        self.detector.return_value = [SimpleNamespace(script_path="/show/SH010/nuke/SH010_v005.nk")]
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
        self.detector.return_value = []
        self.api.update_shot_lock.side_effect = [
            {"ok": True}, {"ok": False, "status_code": None}, {"ok": True},
        ]
        with mock.patch.object(locks.time, "monotonic", return_value=100) as clock:
            self.worker.maintain()
            clock.return_value = 190
            self.worker.maintain()
            self.assertIn(7, self.worker.owned_locks)
            clock.return_value = 235
            self.worker.maintain()
        self.assertNotIn(7, self.worker.owned_locks)
        self.assertEqual([call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list], [False, True, True])

    def test_uncertain_scan_restarts_continuous_absence_grace(self):
        self.claim()
        self.detector.side_effect = [[], RuntimeError("Unknown session"), [], []]
        with mock.patch.object(locks.time, "monotonic", side_effect=[100, 150, 240, 300]):
            for _ in range(4):
                self.worker.maintain()
        self.assertIn(7, self.worker.owned_locks)
        self.assertTrue(all(not call.kwargs["release"] for call in self.api.update_shot_lock.call_args_list))

    def test_changed_release_delay_applies_to_existing_closed_session(self):
        self.claim()
        self.detector.return_value = []
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
        self.detector.return_value = []
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
