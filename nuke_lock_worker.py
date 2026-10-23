"""Maintain Nuke locks independently of the dashboard's GUI thread."""

from __future__ import annotations

import os
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

import http_help
import nuke_detector
import path_mapping
from nuke_lock_utils import parse_lock_info


def normalize_script_path(value: str | None) -> str:
    if not value:
        return ""
    return os.path.normcase(os.path.abspath(os.path.normpath(path_mapping.convert_path(value))))


class NukeLockWorker(QObject):
    lock_updated = pyqtSignal(int, object)
    claim_finished = pyqtSignal(dict)
    stopped = pyqtSignal()

    def __init__(self, *, api=None, interval_ms=45_000, release_grace_sec=90, auto_release=True):
        super().__init__()
        self.api = api if api is not None else http_help.DjangoAPI(skip_activity_logging=True)
        self.owned_locks = {}
        self.pending_launches = {}
        self._reported_locks = {}
        self.configure_release(release_grace_sec, auto_release)
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.tick)

    @pyqtSlot(int, bool)
    def configure_release(self, grace_seconds, enabled):
        self.release_grace_sec = max(1, min(3600, grace_seconds))
        self.auto_release = enabled

    @pyqtSlot()
    def start(self):
        self.timer.start()
        self.tick()

    @pyqtSlot(dict)
    def claim(self, request):
        if QThread.currentThread().isInterruptionRequested():
            return
        shot_id = request["shot_id"]
        owner_id = request["owner_id"]
        existing = self.owned_locks.get(shot_id)
        already_owned = request["already_owned"] or bool(
            existing and existing["owner_id"] == owner_id and not existing["launch_failed"]
        )
        result = self.api.update_shot_lock(
            shot_id, force=request["force"], owner_id=owner_id,
        )
        if result.get("ok"):
            state = self.owned_locks.get(shot_id)
            if state is None or state["owner_id"] != owner_id:
                state = self._new_state(owner_id, request.get("base_path"))
                self.owned_locks[shot_id] = state
            state["script_paths"].add(normalize_script_path(request["script_path"]))
            state["missing_since"] = None
            state["launch_failed"] = False
            state["confirmed_open"] = True
            self.pending_launches[shot_id] = already_owned
        self._publish_result(shot_id, result)
        self.claim_finished.emit({**request, "result": result})

    @pyqtSlot(int, bool)
    def finish_launch(self, shot_id, success):
        # A failed duplicate launch must not release an existing session's lock.
        already_owned = self.pending_launches.pop(shot_id, True)
        if success or already_owned or shot_id not in self.owned_locks:
            return
        self.owned_locks[shot_id]["launch_failed"] = True
        self._write_lock(shot_id, release=True)

    @pyqtSlot()
    def stop(self):
        self.timer.stop()
        for shot_id in list(self.pending_launches):
            self.finish_launch(shot_id, False)
        self.stopped.emit()

    def _new_state(self, owner_id, base_path):
        return {
            "owner_id": owner_id,
            "base_path": normalize_script_path(base_path),
            "script_paths": set(),
            "missing_since": None,
            "launch_failed": False,
            "confirmed_open": False,
        }

    def _publish_lock_value(self, shot_id, value):
        info = parse_lock_info(value)
        if shot_id not in self._reported_locks and info is None:
            return
        # Freshness can change even when the server's lock string does not.
        state = (value, info.is_stale() if info else False)
        if self._reported_locks.get(shot_id) != state:
            self._reported_locks[shot_id] = state
            self.lock_updated.emit(shot_id, value)

    def _publish_result(self, shot_id, result):
        shot = result.get("shot")
        if isinstance(shot, dict) and "nuke_in_use" in shot:
            self._publish_lock_value(shot_id, shot["nuke_in_use"])
        elif result.get("nuke_in_use") is not None:
            self._publish_lock_value(shot_id, result["nuke_in_use"])

    def _write_lock(self, shot_id, *, release=False):
        state = self.owned_locks[shot_id]
        result = self.api.update_shot_lock(
            shot_id, release=release, owner_id=state["owner_id"],
        )
        if ((release and result.get("ok")) or result.get("conflict")
                or result.get("status_code") in (403, 404)):
            self.owned_locks.pop(shot_id, None)
        self._publish_result(shot_id, result)

    def sync(self):
        recovered = set()
        try:
            shots = self.api.get_shots()
        except Exception:
            return recovered
        if not isinstance(shots, list):
            return recovered
        local_owners = self.api.get_lock_owner_ids()
        for shot in shots:
            try:
                shot_id = int(shot["id"])
            except (KeyError, TypeError, ValueError):
                continue
            value = shot.get("nuke_in_use")
            self._publish_lock_value(shot_id, value)
            info = parse_lock_info(value)
            state = self.owned_locks.get(shot_id)
            if info is None:
                # An older response must not stop an open session's heartbeat.
                continue
            # Existing sessions must stop on takeover, even by a known alias.
            if (state and not info.matches_system(state["owner_id"])) or (
                state is None and info.normalized_owner not in local_owners
            ):
                self.owned_locks.pop(shot_id, None)
                continue
            if state is None:
                self.owned_locks[shot_id] = self._new_state(info.normalized_owner, shot.get("base_path"))
                recovered.add(shot_id)
            elif not state["base_path"]:
                state["base_path"] = normalize_script_path(shot.get("base_path"))
        return recovered

    def _matching_paths(self, state, detected_paths):
        matching = state["script_paths"] & detected_paths
        base_path = state["base_path"]
        if base_path:
            for path in detected_paths:
                try:
                    if os.path.commonpath((base_path, path)) == base_path:
                        matching.add(path)
                except ValueError:
                    pass  # Different Windows drives cannot belong to this shot.
        return matching

    def maintain(self, shot_ids=None):
        targets = [
            (shot_id, state) for shot_id, state in self.owned_locks.items()
            if shot_ids is None or shot_id in shot_ids
        ]
        if not targets:
            return
        try:
            detections = nuke_detector.detect_open_nuke_scripts(current_user_only=True, strict=True)
        except Exception:
            detected_paths = None
        else:
            detected_paths = {
                normalize_script_path(item.script_path) for item in detections if item.script_path
            }
        now = time.monotonic()
        for shot_id, state in targets:
            if QThread.currentThread().isInterruptionRequested():
                return
            if shot_id in self.pending_launches:
                self._write_lock(shot_id)
                continue
            if state["launch_failed"]:
                self._write_lock(shot_id, release=True)
                continue
            if detected_paths is None:
                # Uncertain detection breaks the run of confirmed absence.
                state["missing_since"] = None
            else:
                matching = self._matching_paths(state, detected_paths)
                if matching:
                    state["script_paths"].update(matching)
                    state["missing_since"] = None
                    state["confirmed_open"] = True
                elif detected_paths and not state["script_paths"] and not state["base_path"]:
                    # Recovered locks without a known path cannot be disproved.
                    state["missing_since"] = None
                elif state["missing_since"] is None:
                    state["missing_since"] = now
                elif now - state["missing_since"] >= self.release_grace_sec:
                    if self.auto_release:
                        self._write_lock(shot_id, release=True)
                    else:
                        state["confirmed_open"] = False
                    # A confirmed closed script must not keep sending heartbeats
                    # when the user has chosen to retain its lock.
                    continue
            # Finding an old database lock is not evidence of an open script.
            if state["confirmed_open"]:
                self._write_lock(shot_id)

    @pyqtSlot()
    def tick(self):
        if QThread.currentThread().isInterruptionRequested():
            return
        # Renew known sessions before fetching the potentially large job snapshot.
        self.maintain()
        if not QThread.currentThread().isInterruptionRequested():
            recovered = self.sync()
            if recovered:
                self.maintain(recovered)
