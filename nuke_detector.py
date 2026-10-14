#!/usr/bin/env python3
"""
Nuke script detector (Stage 1).

Detects .nk files opened by local Nuke processes and returns structured data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import os
import platform
import socket
from typing import Iterable

import psutil


@dataclass(frozen=True)
class NukeScriptDetection:
    script_name: str
    script_path: str
    machine_name: str
    detected_at_utc: str
    pid: int
    source: str  # "cmdline" or "open_files"


def _now_utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_path_for_compare(path: str) -> str:
    normed = os.path.normpath(path)
    if platform.system() == "Windows":
        return os.path.normcase(normed)
    return normed


def _normalize_path(path: str, proc: psutil.Process | None = None, *, strict: bool = False) -> str:
    cleaned = str(path or "").strip().strip('"').strip("'")
    if not cleaned:
        return ""

    if os.path.isabs(cleaned):
        return os.path.normpath(cleaned)

    cwd = ""
    if proc is not None:
        try:
            cwd = proc.cwd() or ""
        except Exception:
            if strict:
                raise
            cwd = ""

    if cwd:
        return os.path.normpath(os.path.abspath(os.path.join(cwd, cleaned)))
    return os.path.normpath(os.path.abspath(cleaned))


def _is_nuke_process_name(name: str) -> bool:
    text = (name or "").lower()
    return "nuke" in text and "python" not in text


def _looks_like_nk(path: str) -> bool:
    text = str(path or "").strip().strip('"').strip("'")
    return text.lower().endswith(".nk")


def _username_matches(proc_username: str, current_username: str) -> bool:
    if not proc_username:
        return False

    proc_user = proc_username.lower().strip()
    current_user = current_username.lower().strip()
    if proc_user == current_user:
        return True

    # Handles DOMAIN\\user and machine\\user variants.
    if "\\" in proc_user:
        proc_user = proc_user.split("\\")[-1]
    if "/" in proc_user:
        proc_user = proc_user.split("/")[-1]
    return proc_user == current_user


def _iter_nuke_processes(current_user_only: bool = True, *, strict: bool = False) -> Iterable[psutil.Process]:
    current_user = getpass.getuser()
    for proc in psutil.process_iter(["pid", "name", "username"]):
        try:
            name = proc.info.get("name", "") or ""
            if strict and not name:
                raise RuntimeError("Could not identify a running process")
            if not _is_nuke_process_name(name):
                continue

            if current_user_only:
                proc_username = proc.info.get("username", "") or ""
                if strict and not proc_username:
                    raise RuntimeError(f"Could not identify the owner of Nuke process {proc.pid}")
                if not _username_matches(proc_username, current_user):
                    continue
            yield proc
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
        except psutil.AccessDenied:
            if strict:
                raise


def _extract_nk_paths_from_cmdline(proc: psutil.Process, *, strict: bool = False) -> list[str]:
    try:
        cmdline = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return []
    except Exception:
        if strict:
            raise
        return []

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


def detect_open_nuke_scripts(current_user_only: bool = True, *, strict: bool = False) -> list[NukeScriptDetection]:
    machine_name = socket.gethostname() or "unknown-machine"
    detected_at_utc = _now_utc_stamp()

    detections: list[NukeScriptDetection] = []
    seen: set[tuple[int, str]] = set()

    for proc in _iter_nuke_processes(current_user_only=current_user_only, strict=strict):
        pid = int(proc.pid)

        cmdline_paths = _extract_nk_paths_from_cmdline(proc, strict=strict)
        if cmdline_paths:
            source = "cmdline"
            candidate_paths = cmdline_paths
        else:
            source = "open_files"
            candidate_paths = _extract_nk_paths_from_open_files(proc, strict=strict)
        if strict and not candidate_paths:
            # An unidentified running session cannot prove a tracked script closed.
            raise RuntimeError(f"Could not identify the script in Nuke process {pid}")

        for script_path in candidate_paths:
            compare_path = _normalize_path_for_compare(script_path)
            key = (pid, compare_path)
            if key in seen:
                continue
            seen.add(key)

            detections.append(
                NukeScriptDetection(
                    script_name=os.path.basename(script_path),
                    script_path=script_path,
                    machine_name=machine_name,
                    detected_at_utc=detected_at_utc,
                    pid=pid,
                    source=source,
                )
            )

    detections.sort(key=lambda item: (item.pid, item.script_name.lower(), item.script_path.lower()))
    return detections


def detect_primary_open_nuke_script(
    current_user_only: bool = True,
) -> NukeScriptDetection | None:
    detections = detect_open_nuke_scripts(current_user_only=current_user_only)
    if not detections:
        return None
    return detections[0]


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
