#!/usr/bin/env python3
"""
Nuke script detector (Stage 1).

Detects .nk files opened by local Nuke processes and returns structured data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import getpass
import ntpath
import platform
import posixpath
import re
import socket

import psutil


class UnidentifiedNukeScriptError(RuntimeError):
    """Nuke was inspected successfully but exposed no script path."""


@dataclass(frozen=True)
class NukeScriptDetection:
    script_name: str
    script_path: str
    machine_name: str
    detected_at_utc: str
    pid: int
    source: str  # "cmdline" or "open_files"
    process_started_at: float | None = None


@dataclass
class NukeProcessSnapshot:
    scripts: list[NukeScriptDetection] = field(default_factory=list)
    process_ids: set[int] = field(default_factory=set)
    process_started_at: dict[int, float | None] = field(default_factory=dict)
    errors: list[Exception] = field(default_factory=list)
    enumeration_complete: bool = True

    @property
    def complete(self) -> bool:
        return self.enumeration_complete and not self.errors

    @property
    def can_release_unmatched(self) -> bool:
        # Match cleanup to the detected script list. A Nuke session with no
        # exposed .nk path must not retain every other local shot's lock.
        # Permission failures and incomplete process enumeration still cannot
        # establish which local scripts are open.
        return self.enumeration_complete and all(
            isinstance(error, UnidentifiedNukeScriptError) for error in self.errors
        )

    def sessions_closed(self, sessions: set[tuple[int, float | None]]) -> bool:
        """Prove all previously matched processes ended, allowing for PID reuse."""
        if not sessions or not self.enumeration_complete:
            return False
        for pid, started_at in sessions:
            if pid not in self.process_ids:
                continue
            current_start = self.process_started_at.get(pid)
            if started_at is None or current_start is None or current_start == started_at:
                return False
        return True


def _now_utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_path_for_compare(path: str) -> str:
    paths = ntpath if platform.system() == "Windows" else posixpath
    return paths.normcase(paths.normpath(path))


def _normalize_path(path: str, proc: psutil.Process | None = None, *, strict: bool = False) -> str:
    cleaned = str(path or "").strip().strip('"').strip("'")
    if not cleaned:
        return ""

    paths = ntpath if platform.system() == "Windows" else posixpath
    if paths.isabs(cleaned):
        return paths.normpath(cleaned)

    cwd = ""
    if proc is not None:
        try:
            cwd = proc.cwd() or ""
        except Exception:
            if strict:
                raise
            cwd = ""

    if cwd:
        return paths.normpath(paths.abspath(paths.join(cwd, cleaned)))
    return paths.normpath(paths.abspath(cleaned))


def _is_nuke_process_name(name: str) -> bool:
    return re.fullmatch(
        r"nuke(?:studio|assist|x)?(?:\d+(?:\.\d+)*(?:v\d+)?)?(?:\.exe)?",
        name or "", flags=re.IGNORECASE,
    ) is not None


def _looks_like_nk(path: str) -> bool:
    text = str(path or "").strip().strip('"').strip("'")
    return text.lower().endswith(".nk")


def _username_matches(proc_username: str, current_username: str) -> bool:
    if not proc_username:
        return False

    def account(value):
        # Either side may contain DOMAIN\\user, machine/user, or a UPN.
        return value.strip().casefold().replace("\\", "/").rsplit("/", 1)[-1].split("@", 1)[0]
    return account(proc_username) == account(current_username)


def _read_process_cmdline(proc: psutil.Process, *, strict: bool = False) -> list[str]:
    try:
        return proc.cmdline()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return []
    except Exception:
        if strict:
            raise
        return []


def _is_frameserver_cmdline(cmdline: list[str]) -> bool:
    """Recognise Foundry workers without treating unknown sessions as closed."""
    has_terminal_mode = any(arg in {"-t", "-ti", "-it"} for arg in cmdline)
    if not has_terminal_mode:
        return False
    worker_suffix = "/foundry/frameserver/nuke/workerapplication.py"
    return any(
        str(arg).strip().strip('"').strip("'").replace("\\", "/").casefold().endswith(worker_suffix)
        for arg in cmdline
    )


def _extract_nk_paths_from_cmdline(
    proc: psutil.Process, *, strict: bool = False, cmdline: list[str] | None = None,
) -> list[str]:
    if cmdline is None:
        cmdline = _read_process_cmdline(proc, strict=strict)
    out: list[str] = []
    for arg in cmdline:
        if _looks_like_nk(arg):
            normalized = _normalize_path(arg, proc=proc, strict=strict)
            if normalized:
                out.append(normalized)
    return out


def _extract_nk_paths_from_open_files(proc: psutil.Process, *, strict: bool = False) -> list[str]:
    try:
        files = proc.open_files()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return []
    except Exception:
        if strict:
            raise
        return []

    out: list[str] = []
    for item in files:
        file_path = getattr(item, "path", "")
        if _looks_like_nk(file_path):
            normalized = _normalize_path(file_path, proc=proc, strict=strict)
            if normalized:
                out.append(normalized)
    return out


def scan_open_nuke_scripts(
    current_user_only: bool = True, *, tracked_process_ids: set[int] | None = None,
) -> NukeProcessSnapshot:
    machine_name = socket.gethostname() or "unknown-machine"
    detected_at_utc = _now_utc_stamp()

    snapshot = NukeProcessSnapshot()
    seen: set[tuple[int, str]] = set()
    current_user = getpass.getuser()
    tracked_process_ids = tracked_process_ids or set()
    try:
        for proc in psutil.process_iter(["pid", "name", "username"]):
            pid = int(proc.pid)
            snapshot.process_ids.add(pid)
            try:
                name = proc.info.get("name", "") or ""
                is_nuke = _is_nuke_process_name(name)
                if is_nuke or pid in tracked_process_ids:
                    try:
                        snapshot.process_started_at[pid] = proc.create_time()
                    except (psutil.AccessDenied, AttributeError):
                        snapshot.process_started_at[pid] = None
                # Idle/System cannot be Nuke. Other unreadable process names
                # remain uncertain, and their observed PIDs protect live sessions.
                if not name:
                    if platform.system() == "Windows" and pid in (0, 4):
                        continue
                    raise RuntimeError(f"Could not identify running process {pid}")
                if not is_nuke:
                    continue
                started_at = snapshot.process_started_at[pid]
                if current_user_only:
                    username = proc.info.get("username", "") or ""
                    if not username:
                        raise RuntimeError(f"Could not identify the owner of Nuke process {pid}")
                    if not _username_matches(username, current_user):
                        continue
                cmdline = _read_process_cmdline(proc, strict=True)
                if _is_frameserver_cmdline(cmdline):
                    continue
                candidate_paths = _extract_nk_paths_from_cmdline(proc, strict=True, cmdline=cmdline)
                source = "cmdline"
                if not candidate_paths:
                    source = "open_files"
                    candidate_paths = _extract_nk_paths_from_open_files(proc, strict=True)
                if not candidate_paths:
                    raise UnidentifiedNukeScriptError(f"Could not identify the script in Nuke process {pid}")
                for script_path in candidate_paths:
                    key = (pid, _normalize_path_for_compare(script_path))
                    if key in seen:
                        continue
                    seen.add(key)
                    paths = ntpath if platform.system() == "Windows" else posixpath
                    snapshot.scripts.append(NukeScriptDetection(
                        script_name=paths.basename(script_path), script_path=script_path,
                        machine_name=machine_name, detected_at_utc=detected_at_utc,
                        pid=pid, source=source, process_started_at=started_at,
                    ))
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                snapshot.process_ids.discard(pid)
                continue
            except Exception as exc:
                snapshot.errors.append(exc)
    except Exception as exc:
        snapshot.enumeration_complete = False
        snapshot.errors.append(exc)
    snapshot.scripts.sort(key=lambda item: (item.pid, item.script_name.lower(), item.script_path.lower()))
    return snapshot


def detect_open_nuke_scripts(current_user_only: bool = True, *, strict: bool = False) -> list[NukeScriptDetection]:
    snapshot = scan_open_nuke_scripts(current_user_only=current_user_only)
    if strict and snapshot.errors:
        raise snapshot.errors[0]
    return snapshot.scripts


def detect_primary_open_nuke_script(
    current_user_only: bool = True,
) -> NukeScriptDetection | None:
    detections = detect_open_nuke_scripts(current_user_only=current_user_only)
    if not detections:
        return None
    return detections[0]


def print_snapshot_debug(snapshot: NukeProcessSnapshot) -> None:
    """Print the local scan, including partial results and their errors."""
    prefix = "[Nuke Detector]"
    status = "complete" if snapshot.complete else "incomplete"
    lines = [
        f"{prefix} {_now_utc_stamp()} machine={socket.gethostname()} user={getpass.getuser()} "
        f"scripts={len(snapshot.scripts)} scan={status}",
    ]
    for item in snapshot.scripts:
        lines.append(
            f"{prefix} pid={item.pid} source={item.source} "
            f"script={item.script_name} path={item.script_path}"
        )
    if not snapshot.scripts:
        lines.append(
            f"{prefix} No open .nk scripts detected for the current user."
            if snapshot.complete else
            f"{prefix} No scripts identified; detection is incomplete."
        )
    for error in snapshot.errors:
        lines.append(f"{prefix} Detection error: {type(error).__name__}: {error}")
    if snapshot.errors and snapshot.can_release_unmatched:
        lines.append(
            f"{prefix} Unmatched locks owned by this machine can clear after the configured delay."
        )
    print("\n".join(lines), flush=True)


def debug_run() -> int:
    machine_name = socket.gethostname() or "unknown-machine"
    now_utc = _now_utc_stamp()
    detections = detect_open_nuke_scripts(current_user_only=True)

    print("=" * 80)
    print("NUKE DETECTOR DEBUG")
    print(f"Machine: {machine_name}")
    print(f"Detected at (UTC): {now_utc}")
    print("=" * 80)

    if not detections:
        print("No open .nk scripts detected for current user.")
        print("Summary: 0 script(s) found")
        return 1

    for item in detections:
        print(
            f"pid={item.pid} source={item.source} "
            f"script={item.script_name} path={item.script_path}"
        )

    print(f"Summary: {len(detections)} script(s) found")
    return 0


if __name__ == "__main__":
    raise SystemExit(debug_run())
