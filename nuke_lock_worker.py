"""Maintain Nuke locks independently of the dashboard's GUI thread."""

from __future__ import annotations

import logging
import posixpath
import time

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal, pyqtSlot

import http_help
import nuke_detector
import path_mapping
from nuke_lock_utils import parse_lock_info

logger = logging.getLogger(__name__)


def normalize_script_path(value: str | None) -> str:
    if not value:
        return ""
    return path_mapping.comparison_path(value)


class NukeLockWorker(QObject):
    lock_updated = pyqtSignal(int, object)
    claim_finished = pyqtSignal(dict)
    stopped = pyqtSignal()
    clear_preview_ready = pyqtSignal(dict)
    clear_finished = pyqtSignal(dict)

    def __init__(self, *, api=None, interval_ms=45_000, release_grace_sec=90, auto_release=True, debug=False):
        super().__init__()
        self.api = api if api is not None else http_help.DjangoAPI(skip_activity_logging=True)
        self.owned_locks = {}
        self.pending_launches = {}
        self._reported_locks = {}
        self._last_detection_warning = None
        self.debug = bool(debug)
        self.configure_release(release_grace_sec, auto_release)
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.tick)

    @pyqtSlot(int)
    def configure_interval(self, seconds):
        interval_ms = max(1, min(60, seconds)) * 1000
        if self.timer.interval() != interval_ms:
            self.timer.setInterval(interval_ms)

    @pyqtSlot(int, bool)
    def configure_release(self, grace_seconds, enabled):
        self.release_grace_sec = max(1, min(3600, grace_seconds))
        self.auto_release = enabled

    @pyqtSlot(bool)
    def configure_debug(self, enabled):
        changed = self.debug != bool(enabled)
        self.debug = bool(enabled)
        if changed and self.debug:
            self._scan()

    @pyqtSlot(bool)
    def preview_clear(self, stale_only):
        result = {"stale_only": stale_only, "shots": [], "error": None}
        try:
            shots = self.api.get_shots()
            if not isinstance(shots, list):
                raise ValueError("The server returned an invalid shots list.")
            owners = self.api.get_lock_owner_ids()
            seen = set()
            for shot in shots:
                if QThread.currentThread().isInterruptionRequested():
                    raise RuntimeError("Lock cleanup was cancelled.")
                info = parse_lock_info(shot.get("nuke_in_use"))
                if info is None or info.normalized_owner not in owners or (stale_only and not info.is_stale()):
                    continue
                shot_id = int(shot["id"])
                if shot_id in seen or shot_id in self.pending_launches:
                    continue
                seen.add(shot_id)
                result["shots"].append({
                    "shot_id": shot_id, "title": shot.get("title") or f"Shot {shot_id}",
                    "owner_id": info.normalized_owner,
                })
        except Exception as exc:
            result["error"] = str(exc)
            result["shots"] = []
        self.clear_preview_ready.emit(result)

    @pyqtSlot(dict)
    def clear_locks(self, request):
        stale_only = bool(request.get("stale_only", True))
        result = {"stale_only": stale_only, "cleared": [], "skipped": [], "failed": []}
        try:
            owners = self.api.get_lock_owner_ids()
            seen = set()
            for candidate in request.get("shots", []):
                entry = dict(candidate)
                try:
                    shot_id = int(candidate["shot_id"])
                    if shot_id in seen:
                        continue
                    seen.add(shot_id)
                    if QThread.currentThread().isInterruptionRequested():
                        raise RuntimeError("Lock cleanup was cancelled.")
                    if shot_id in self.pending_launches:
                        entry["detail"] = "Nuke is being opened for this shot."
                        result["skipped"].append(entry)
                        continue
                    # The confirmation may have been open for some time. Never
                    # clear a new owner, or a stale lock that has resumed renewal.
                    shot = self.api.get_shot(shot_id)
                    info = parse_lock_info(shot.get("nuke_in_use"))
                    if (info is None or info.normalized_owner not in owners
                            or info.normalized_owner != candidate.get("owner_id")
                            or (stale_only and not info.is_stale())):
                        entry["detail"] = "The lock was released, changed owner, or became fresh."
                        result["skipped"].append(entry)
                        self._publish_lock_value(shot_id, shot.get("nuke_in_use"), report_empty=True)
                        continue
                    response = self.api.update_shot_lock(shot_id, release=True, owner_id=info.normalized_owner)
                    self._publish_result(shot_id, response)
                    if response.get("ok"):
                        self.owned_locks.pop(shot_id, None)
                        self._publish_lock_value(shot_id, "None", report_empty=True)
                        result["cleared"].append(entry)
                    else:
                        if response.get("conflict") or response.get("status_code") in (403, 404):
                            self.owned_locks.pop(shot_id, None)
                        entry["detail"] = response.get("detail") or "The server could not clear this lock."
                        result["failed"].append(entry)
                except Exception as exc:
                    entry["detail"] = str(exc)
                    result["failed"].append(entry)
        except Exception as exc:
            result["error"] = str(exc)
        self.clear_finished.emit(result)

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
            "process_sessions": set(),
        }

    def _publish_lock_value(self, shot_id, value, *, report_empty=False):
        info = parse_lock_info(value)
        if not report_empty and shot_id not in self._reported_locks and info is None:
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
                    if posixpath.commonpath((base_path, path)) == base_path:
                        matching.add(path)
                except ValueError:
                    pass  # Different Windows drives cannot belong to this shot.
        return matching

    def _scan(self):
        try:
            snapshot = nuke_detector.scan_open_nuke_scripts(
                current_user_only=True,
                tracked_process_ids={pid for state in self.owned_locks.values() for pid, _ in state["process_sessions"]},
            )
        except Exception as exc:
            snapshot = nuke_detector.NukeProcessSnapshot(errors=[exc], enumeration_complete=False)
        warning = "; ".join(str(exc) for exc in snapshot.errors) or None
        if warning != self._last_detection_warning:
            if warning and not self.debug:
                logger.warning("Nuke lock check incomplete: %s", warning)
            self._last_detection_warning = warning
        if self.debug:
            nuke_detector.print_snapshot_debug(snapshot)
        return snapshot

    def maintain(self, shot_ids=None, *, snapshot=None):
        targets = [
            (shot_id, state) for shot_id, state in self.owned_locks.items()
            if shot_ids is None or shot_id in shot_ids
        ]
        if not targets and not self.debug:
            return snapshot
        if snapshot is None:
            snapshot = self._scan()
        detected_paths = {
            normalize_script_path(item.script_path) for item in snapshot.scripts if item.script_path
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
            matching = self._matching_paths(state, detected_paths)
            if matching:
                state["script_paths"].update(matching)
                # Keep the actual process identity, rather than letting an
                # unrelated unidentified Nuke session block release forever.
                still_running = {
                    session for session in state["process_sessions"]
                    if not snapshot.sessions_closed({session})
                }
                state["process_sessions"] = still_running | {
                    (item.pid, getattr(item, "process_started_at", None))
                    for item in snapshot.scripts
                    if normalize_script_path(item.script_path) in matching and hasattr(item, "pid")
                }
                state["missing_since"] = None
                state["confirmed_open"] = True
            elif not snapshot.can_release_unmatched and not snapshot.sessions_closed(state["process_sessions"]):
                # Uncertain detection breaks the run of confirmed absence.
                state["missing_since"] = None
            else:
                if state["missing_since"] is None:
                    state["missing_since"] = now
                elif now - state["missing_since"] >= self.release_grace_sec:
                    if self.auto_release:
                        self._write_lock(shot_id, release=True)
                    else:
                        state["confirmed_open"] = False
                    # A confirmed closed script must not keep sending heartbeats
                    # when the user has chosen to retain its lock.
                    continue
            # Stop renewing unmatched locks once absence can be evaluated, so
            # they age naturally during the grace period rather than appearing
            # active until the instant of release.
            if state["confirmed_open"] and state["missing_since"] is None:
                self._write_lock(shot_id)
        return snapshot

    @pyqtSlot()
    def tick(self):
        if QThread.currentThread().isInterruptionRequested():
            return
        # Renew known sessions before fetching the potentially large job snapshot.
        snapshot = self.maintain()
        if not QThread.currentThread().isInterruptionRequested():
            recovered = self.sync()
            if recovered:
                self.maintain(recovered, snapshot=snapshot)
