from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
import os
import re
import shutil
import subprocess

from app_version import APP_VERSION, UPDATE_BRANCH, UPDATE_REMOTE, format_version_display


REPO_ROOT = Path(__file__).resolve().parent
VERSION_FILE = "app_version.py"
CHANGELOG_FILE = "CHANGELOG.md"

_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_CHANGELOG_ENTRY_RE = re.compile(r"^##\s+(.+?)(?:\r?\n)(.*?)(?=^##\s+|\Z)", re.MULTILINE | re.DOTALL)
_IGNORED_UNTRACKED_RE = re.compile(r"(^|/)[^/]+_settings\.yaml$")


@dataclass
class UpdateStatus:
    supported: bool
    can_check: bool
    can_update: bool
    has_update: bool
    is_dirty: bool
    branch: str
    current_branch: Optional[str]
    current_version: str
    current_commit: Optional[str]
    current_display: str
    status_message: str
    remote_version: Optional[str] = None
    remote_commit: Optional[str] = None
    remote_display: Optional[str] = None
    changelog_preview: str = "No changelog preview loaded yet."


def parse_version_from_source(source: str) -> Optional[str]:
    match = _VERSION_RE.search(source or "")
    return match.group(1).strip() if match else None


def parse_latest_changelog_preview(changelog_text: str) -> str:
    match = _CHANGELOG_ENTRY_RE.search(changelog_text or "")
    if not match:
        return "No changelog entries found."

    heading = match.group(1).strip()
    body_lines = []
    for raw_line in match.group(2).splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        body_lines.append(line)
        if len(body_lines) >= 6:
            break

    preview_lines = [heading]
    preview_lines.extend(body_lines)
    return "\n".join(preview_lines)


def _default_status(message: str) -> UpdateStatus:
    return UpdateStatus(
        supported=False,
        can_check=False,
        can_update=False,
        has_update=False,
        is_dirty=False,
        branch=UPDATE_BRANCH,
        current_branch=None,
        current_version=APP_VERSION,
        current_commit=None,
        current_display=format_version_display(APP_VERSION, None),
        status_message=message,
        changelog_preview="Manual update required.",
    )


def _run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=check,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=str(exc),
        )


def _git_available() -> bool:
    return shutil.which("git") is not None


def _has_git_checkout() -> bool:
    return (REPO_ROOT / ".git").exists()


def _has_origin_remote() -> bool:
    result = _run_git("remote", check=False)
    if result.returncode != 0:
        return False
    remotes = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    return UPDATE_REMOTE in remotes


def _get_current_branch() -> Optional[str]:
    result = _run_git("branch", "--show-current", check=False)
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def _get_head_commit() -> Optional[str]:
    result = _run_git("rev-parse", "HEAD", check=False)
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _is_dirty() -> bool:
    return bool(_get_blocking_git_status_lines())


def _should_ignore_git_status_line(status_line: str) -> bool:
    line = (status_line or "").rstrip()
    if not line.startswith("?? "):
        return False

    path = line[3:].strip().replace("\\", "/")
    if not path:
        return False

    return _IGNORED_UNTRACKED_RE.search(path) is not None


def _get_blocking_git_status_lines() -> list[str]:
    result = _run_git("status", "--porcelain", check=False)
    if result.returncode != 0:
        return []
    return [
        line
        for line in result.stdout.splitlines()
        if line.strip() and not _should_ignore_git_status_line(line)
    ]


def _get_remote_commit(remote_ref: str) -> Optional[str]:
    result = _run_git("rev-parse", remote_ref, check=False)
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _get_ahead_behind(remote_ref: str) -> Tuple[int, int]:
    result = _run_git("rev-list", "--left-right", "--count", f"HEAD...{remote_ref}", check=False)
    if result.returncode != 0:
        return (0, 0)
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _read_git_file(ref: str, path: str) -> Optional[str]:
    result = _run_git("show", f"{ref}:{path}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout


def inspect_install() -> UpdateStatus:
    if not _git_available():
        return _default_status("Git is not installed or not on PATH.")

    if not _has_git_checkout():
        return _default_status("This install is not running from a git checkout.")

    if not _has_origin_remote():
        return _default_status("No origin remote is configured for this install.")

    current_branch = _get_current_branch()
    current_commit = _get_head_commit()
    short_commit = current_commit[:7] if current_commit else None
    dirty = _is_dirty()

    status = UpdateStatus(
        supported=True,
        can_check=True,
        can_update=False,
        has_update=False,
        is_dirty=dirty,
        branch=UPDATE_BRANCH,
        current_branch=current_branch,
        current_version=APP_VERSION,
        current_commit=current_commit,
        current_display=format_version_display(APP_VERSION, short_commit),
        status_message="Ready to check for updates.",
    )

    if current_branch and current_branch != UPDATE_BRANCH:
        status.status_message = (
            f"Current branch is '{current_branch}'. Published self-updates only support '{UPDATE_BRANCH}'."
        )
        status.changelog_preview = "Switch to main for self-update support."
        return status

    if dirty:
        status.status_message = "Local changes detected. Clean working tree required before updating."

    return status


def check_for_updates() -> UpdateStatus:
    status = inspect_install()
    if not status.can_check:
        return status

    fetch_result = _run_git("fetch", UPDATE_REMOTE, UPDATE_BRANCH, check=False)
    if fetch_result.returncode != 0:
        status.status_message = (
            fetch_result.stderr.strip() or "Failed to fetch updates from GitHub."
        )
        status.changelog_preview = "Could not load remote changelog."
        return status

    remote_ref = f"{UPDATE_REMOTE}/{UPDATE_BRANCH}"
    remote_commit = _get_remote_commit(remote_ref)
    remote_short = remote_commit[:7] if remote_commit else None

    remote_version_source = _read_git_file(remote_ref, VERSION_FILE)
    remote_version = parse_version_from_source(remote_version_source or "")
    if remote_version is None:
        remote_version = APP_VERSION

    remote_changelog = _read_git_file(remote_ref, CHANGELOG_FILE)
    status.remote_version = remote_version
    status.remote_commit = remote_commit
    status.remote_display = format_version_display(remote_version, remote_short)
    status.changelog_preview = parse_latest_changelog_preview(remote_changelog or "")

    ahead, behind = _get_ahead_behind(remote_ref)

    if status.current_branch and status.current_branch != UPDATE_BRANCH:
        status.status_message = (
            f"Remote publish info loaded, but this checkout is on '{status.current_branch}', not '{UPDATE_BRANCH}'."
        )
        return status

    if behind == 0 and ahead == 0:
        status.status_message = "You are already on the latest published frontend version."
        return status

    if behind > 0 and ahead == 0:
        status.has_update = True
        if status.is_dirty:
            status.status_message = (
                "A newer published version is available, but local changes must be cleaned up first."
            )
            status.can_update = False
        else:
            status.status_message = f"Update available: {status.remote_display}"
            status.can_update = True
        return status

    if ahead > 0 and behind == 0:
        status.status_message = "This checkout is ahead of origin/main. No published update will be applied."
        return status

    status.status_message = (
        "This checkout has diverged from origin/main. Resolve branch differences manually before updating."
    )
    return status


def launch_update_script(wait_pid: int) -> Tuple[bool, str]:
    if os.name == "nt":
        script_path = REPO_ROOT / "windows_update_shotbox.bat"
        if not script_path.exists():
            return (False, f"Missing updater script: {script_path.name}")
        try:
            subprocess.Popen(
                ["cmd", "/c", str(script_path), str(wait_pid)],
                cwd=REPO_ROOT,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as exc:
            return (False, str(exc))
        return (True, "")

    script_path = REPO_ROOT / "update_shotbox_frontend.sh"
    if not script_path.exists():
        return (False, f"Missing updater script: {script_path.name}")
    try:
        subprocess.Popen(
            ["bash", str(script_path), str(wait_pid)],
            cwd=REPO_ROOT,
            start_new_session=True,
        )
    except OSError as exc:
        return (False, str(exc))
    return (True, "")
