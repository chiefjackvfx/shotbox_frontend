from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QApplication,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app_update import parse_version_from_source
from app_version import UPDATE_BRANCH, UPDATE_REMOTE


REPO_ROOT = Path(__file__).resolve().parent
VERSION_PATH = REPO_ROOT / "app_version.py"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

SEMVER_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)\s*$")
APP_VERSION_RE = re.compile(r'^(APP_VERSION\s*=\s*["\'])([^"\']+)(["\'])', re.MULTILINE)
RELEASE_COMMIT_RE = re.compile(r"^Release (\d+\.\d+\.\d+)$")


@dataclass
class ReleaseDraft:
    current_version: str
    next_version: str
    release_date: str
    changelog_entry: str
    commit_message: str
    tag_name: str


@dataclass
class ReleaseRepoStatus:
    supported: bool
    can_commit: bool
    can_push: bool
    current_version: str
    current_branch: Optional[str]
    remote_state: str
    remote_detail: str
    is_dirty: bool
    status_message: str
    head_commit_subject: Optional[str] = None
    head_release_version: Optional[str] = None
    head_release_tag: Optional[str] = None
    remote_tag_exists: Optional[bool] = None
    main_needs_push: bool = False
    tag_needs_push: bool = False


def parse_semver(version: str) -> Optional[tuple[int, int, int]]:
    match = SEMVER_RE.match(version or "")
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def bump_version(version: str, bump_kind: str) -> str:
    parts = parse_semver(version)
    if not parts:
        raise ValueError(f"Invalid semantic version: {version!r}")

    major, minor, patch = parts
    if bump_kind == "major":
        return f"{major + 1}.0.0"
    if bump_kind == "minor":
        return f"{major}.{minor + 1}.0"
    if bump_kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unsupported bump kind: {bump_kind!r}")


def normalize_note_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def build_changelog_entry(
    version: str,
    release_date: str,
    added_notes: list[str],
    changed_notes: list[str],
    fixed_notes: list[str],
) -> str:
    sections = [
        ("Added", added_notes),
        ("Changed", changed_notes),
        ("Fixed", fixed_notes),
    ]

    if not any(sections_notes for _, sections_notes in sections):
        raise ValueError("At least one release note is required.")

    lines = [f"## {version} - {release_date}", ""]
    for section_name, notes in sections:
        if not notes:
            continue
        lines.append(f"### {section_name}")
        lines.append("")
        lines.extend(f"- {note}" for note in notes)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def prepend_changelog_entry(changelog_text: str, new_entry: str) -> str:
    source = changelog_text or "# Changelog\n"
    entry = (new_entry or "").strip()
    if not entry:
        raise ValueError("Changelog entry cannot be empty.")

    match = re.search(r"^##\s", source, re.MULTILINE)
    if match:
        head = source[: match.start()].rstrip()
        tail = source[match.start() :].lstrip()
        return f"{head}\n\n{entry}\n\n{tail}".rstrip() + "\n"

    return f"{source.rstrip()}\n\n{entry}\n".rstrip() + "\n"


def replace_app_version_source(source: str, new_version: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}{new_version}{match.group(3)}"

    updated, count = APP_VERSION_RE.subn(_replace, source, count=1)
    if count != 1:
        raise ValueError("Could not update APP_VERSION in app_version.py.")
    return updated


def make_release_commit_message(version: str) -> str:
    return f"Release {version}"


def make_release_tag(version: str) -> str:
    return f"v{version}"


def parse_release_commit_version(subject: str) -> Optional[str]:
    match = RELEASE_COMMIT_RE.match((subject or "").strip())
    return match.group(1) if match else None


def build_release_draft(
    current_version: str,
    bump_kind: str,
    release_date: str,
    added_text: str,
    changed_text: str,
    fixed_text: str,
) -> ReleaseDraft:
    next_version = bump_version(current_version, bump_kind)
    changelog_entry = build_changelog_entry(
        next_version,
        release_date,
        normalize_note_lines(added_text),
        normalize_note_lines(changed_text),
        normalize_note_lines(fixed_text),
    )
    return ReleaseDraft(
        current_version=current_version,
        next_version=next_version,
        release_date=release_date,
        changelog_entry=changelog_entry,
        commit_message=make_release_commit_message(next_version),
        tag_name=make_release_tag(next_version),
    )


def _run_git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(
            args=command,
            returncode=1,
            stdout="",
            stderr=str(exc),
        )


def _command_output(result: subprocess.CompletedProcess) -> str:
    parts = []
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    return "\n".join(parts).strip()


def _git_available() -> bool:
    return shutil.which("git") is not None


def _has_git_checkout() -> bool:
    return (REPO_ROOT / ".git").exists()


def _has_origin_remote() -> bool:
    result = _run_git("remote")
    if result.returncode != 0:
        return False
    return UPDATE_REMOTE in {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _read_current_version() -> str:
    try:
        source = VERSION_PATH.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    return parse_version_from_source(source) or "unknown"


def _get_current_branch() -> Optional[str]:
    result = _run_git("branch", "--show-current")
    if result.returncode != 0:
        return None
    branch = result.stdout.strip()
    return branch or None


def _is_dirty() -> bool:
    result = _run_git("status", "--porcelain")
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def _get_ahead_behind(remote_ref: str) -> tuple[int, int]:
    result = _run_git("rev-list", "--left-right", "--count", f"HEAD...{remote_ref}")
    if result.returncode != 0:
        return (0, 0)
    parts = result.stdout.strip().split()
    if len(parts) != 2:
        return (0, 0)
    try:
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return (0, 0)


def _get_head_commit_subject() -> Optional[str]:
    result = _run_git("log", "-1", "--pretty=%s")
    if result.returncode != 0:
        return None
    subject = result.stdout.strip()
    return subject or None


def _tags_pointing_to_head() -> list[str]:
    result = _run_git("tag", "--points-at", "HEAD")
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _remote_tag_exists(tag_name: str) -> Optional[bool]:
    result = _run_git("ls-remote", "--tags", UPDATE_REMOTE, f"refs/tags/{tag_name}")
    if result.returncode != 0:
        return None
    return bool(result.stdout.strip())


def _describe_remote_state(remote_state: str, ahead: int, behind: int) -> str:
    if remote_state == "up_to_date":
        return "up to date with origin/main"
    if remote_state == "ahead":
        return f"ahead of origin/main by {ahead} commit(s)"
    if remote_state == "behind":
        return f"behind origin/main by {behind} commit(s)"
    if remote_state == "diverged":
        return f"diverged from origin/main (ahead {ahead}, behind {behind})"
    return "unknown"


def inspect_repo_status(fetch_remote: bool = True) -> ReleaseRepoStatus:
    current_version = _read_current_version()
    semver_valid = parse_semver(current_version) is not None

    if not _git_available():
        return ReleaseRepoStatus(
            supported=False,
            can_commit=False,
            can_push=False,
            current_version=current_version,
            current_branch=None,
            remote_state="unknown",
            remote_detail="git not available",
            is_dirty=False,
            status_message="Git is not installed or not on PATH.",
        )

    if not _has_git_checkout():
        return ReleaseRepoStatus(
            supported=False,
            can_commit=False,
            can_push=False,
            current_version=current_version,
            current_branch=None,
            remote_state="unknown",
            remote_detail="not a git checkout",
            is_dirty=False,
            status_message="This tool only works inside the frontend git checkout.",
        )

    if not _has_origin_remote():
        return ReleaseRepoStatus(
            supported=False,
            can_commit=False,
            can_push=False,
            current_version=current_version,
            current_branch=None,
            remote_state="unknown",
            remote_detail="origin remote missing",
            is_dirty=False,
            status_message="No origin remote is configured for this repo.",
        )

    current_branch = _get_current_branch()
    is_dirty = _is_dirty()

    fetch_error = None
    if fetch_remote:
        fetch_result = _run_git("fetch", UPDATE_REMOTE, UPDATE_BRANCH)
        if fetch_result.returncode != 0:
            fetch_error = _command_output(fetch_result) or "git fetch failed."

    ahead = 0
    behind = 0
    remote_state = "unknown"
    remote_detail = "unknown"
    if fetch_error is None:
        ahead, behind = _get_ahead_behind(f"{UPDATE_REMOTE}/{UPDATE_BRANCH}")
        if ahead == 0 and behind == 0:
            remote_state = "up_to_date"
        elif ahead > 0 and behind == 0:
            remote_state = "ahead"
        elif behind > 0 and ahead == 0:
            remote_state = "behind"
        else:
            remote_state = "diverged"
        remote_detail = _describe_remote_state(remote_state, ahead, behind)
    else:
        remote_detail = fetch_error

    head_commit_subject = _get_head_commit_subject()
    head_release_version = parse_release_commit_version(head_commit_subject or "")
    head_release_tag = None
    remote_tag_exists = None
    tag_needs_push = False
    if head_release_version:
        expected_tag = make_release_tag(head_release_version)
        if expected_tag in _tags_pointing_to_head():
            head_release_tag = expected_tag
            remote_tag_exists = _remote_tag_exists(expected_tag)
            tag_needs_push = remote_tag_exists is False

    main_needs_push = remote_state == "ahead"

    can_commit = (
        semver_valid
        and current_branch == UPDATE_BRANCH
        and not is_dirty
        and fetch_error is None
        and remote_state in {"up_to_date", "ahead"}
    )

    can_push = (
        current_branch == UPDATE_BRANCH
        and not is_dirty
        and head_release_tag is not None
        and (main_needs_push or tag_needs_push)
    )

    if not semver_valid:
        status_message = "APP_VERSION is not a valid semantic version."
    elif current_branch != UPDATE_BRANCH:
        status_message = (
            f"Current branch is {current_branch or 'unknown'}. Releases only support {UPDATE_BRANCH}."
        )
    elif is_dirty:
        status_message = "Working tree is dirty. Commit or discard local changes before releasing."
    elif fetch_error is not None:
        status_message = f"Could not verify origin/{UPDATE_BRANCH}: {fetch_error}"
    elif remote_state == "behind":
        status_message = f"Local {UPDATE_BRANCH} is behind origin/{UPDATE_BRANCH}. Pull first."
    elif remote_state == "diverged":
        status_message = f"Local {UPDATE_BRANCH} diverged from origin/{UPDATE_BRANCH}. Resolve it manually."
    elif head_release_version and head_release_tag and main_needs_push and tag_needs_push:
        status_message = f"{head_commit_subject} is ready to push to origin/{UPDATE_BRANCH} with tag {head_release_tag}."
    elif head_release_version and head_release_tag and main_needs_push:
        status_message = f"{head_commit_subject} is ready to push to origin/{UPDATE_BRANCH}."
    elif head_release_version and head_release_tag and tag_needs_push:
        status_message = f"{head_release_tag} still needs to be pushed to origin."
    elif head_release_version and not head_release_tag:
        status_message = f"{head_commit_subject} exists, but local tag {make_release_tag(head_release_version)} is missing."
    elif remote_state == "ahead":
        status_message = f"Local {UPDATE_BRANCH} is ahead of origin/{UPDATE_BRANCH}. A new release can include those commits."
    else:
        status_message = "Repo is ready for a new release."

    return ReleaseRepoStatus(
        supported=True,
        can_commit=can_commit,
        can_push=can_push,
        current_version=current_version,
        current_branch=current_branch,
        remote_state=remote_state,
        remote_detail=remote_detail,
        is_dirty=is_dirty,
        status_message=status_message,
        head_commit_subject=head_commit_subject,
        head_release_version=head_release_version,
        head_release_tag=head_release_tag,
        remote_tag_exists=remote_tag_exists,
        main_needs_push=main_needs_push,
        tag_needs_push=tag_needs_push,
    )


def format_repo_status_text(status: ReleaseRepoStatus) -> str:
    lines = [
        f"Version: {status.current_version}",
        f"Branch: {status.current_branch or 'unknown'}",
        f"Remote: {status.remote_detail}",
        f"Working tree: {'dirty' if status.is_dirty else 'clean'}",
    ]

    if status.head_commit_subject:
        lines.append(f"HEAD: {status.head_commit_subject}")
    if status.head_release_tag:
        lines.append(f"Local tag: {status.head_release_tag}")
        if status.remote_tag_exists is True:
            lines.append("Remote tag: present on origin")
        elif status.remote_tag_exists is False:
            lines.append("Remote tag: missing on origin")
    lines.append("")
    lines.append(status.status_message)
    return "\n".join(lines)


class ReleaseManagerWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.repo_status = inspect_repo_status(fetch_remote=True)
        self.selected_bump: Optional[str] = None
        self._build_ui()
        self._apply_repo_status()
        self._update_draft_preview()

    def _build_ui(self) -> None:
        self.setWindowTitle("ShotBox Frontend Release Manager")
        self.resize(960, 900)

        root_layout = QVBoxLayout(self)

        title = QLabel("ShotBox Frontend Release Manager")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        root_layout.addWidget(title)

        subtitle = QLabel(f"Publishes release commits and tags to {UPDATE_REMOTE}/{UPDATE_BRANCH}.")
        subtitle.setWordWrap(True)
        root_layout.addWidget(subtitle)

        repo_group = QGroupBox("Repo Status")
        repo_layout = QFormLayout(repo_group)
        self.current_version_label = QLabel("-")
        self.branch_label = QLabel("-")
        self.remote_state_label = QLabel("-")
        self.working_tree_label = QLabel("-")
        self.head_release_label = QLabel("-")
        repo_layout.addRow("Current Version:", self.current_version_label)
        repo_layout.addRow("Current Branch:", self.branch_label)
        repo_layout.addRow("Remote State:", self.remote_state_label)
        repo_layout.addRow("Working Tree:", self.working_tree_label)
        repo_layout.addRow("HEAD Release:", self.head_release_label)

        self.repo_message_box = QPlainTextEdit()
        self.repo_message_box.setReadOnly(True)
        self.repo_message_box.setMaximumBlockCount(100)
        self.repo_message_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.repo_message_box.setFixedHeight(140)
        repo_layout.addRow("Summary:", self.repo_message_box)
        root_layout.addWidget(repo_group)

        draft_group = QGroupBox("Release Draft")
        draft_layout = QVBoxLayout(draft_group)

        draft_meta_layout = QFormLayout()
        self.release_date_label = QLabel(date.today().isoformat())
        self.next_version_label = QLabel("Choose a bump type")
        draft_meta_layout.addRow("Release Date:", self.release_date_label)
        draft_meta_layout.addRow("Next Version:", self.next_version_label)
        draft_layout.addLayout(draft_meta_layout)

        bump_layout = QHBoxLayout()
        bump_layout.addWidget(QLabel("Version Bump:"))
        self.bump_buttons: dict[str, QPushButton] = {}
        for bump_kind, label in (("major", "Major"), ("minor", "Minor"), ("patch", "Patch")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.clicked.connect(lambda checked, kind=bump_kind: self._select_bump(kind, checked))
            self.bump_buttons[bump_kind] = button
            bump_layout.addWidget(button)
        bump_layout.addStretch(1)
        draft_layout.addLayout(bump_layout)

        notes_layout = QFormLayout()
        self.added_notes = QPlainTextEdit()
        self.changed_notes = QPlainTextEdit()
        self.fixed_notes = QPlainTextEdit()
        self.added_notes.setPlaceholderText("One line per Added note")
        self.changed_notes.setPlaceholderText("One line per Changed note")
        self.fixed_notes.setPlaceholderText("One line per Fixed note")
        for widget in (self.added_notes, self.changed_notes, self.fixed_notes):
            widget.setFixedHeight(90)
            widget.textChanged.connect(self._update_draft_preview)
        notes_layout.addRow("Added:", self.added_notes)
        notes_layout.addRow("Changed:", self.changed_notes)
        notes_layout.addRow("Fixed:", self.fixed_notes)
        draft_layout.addLayout(notes_layout)
        root_layout.addWidget(draft_group)

        preview_group = QGroupBox("Generated Preview")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_box = QPlainTextEdit()
        self.preview_box.setReadOnly(True)
        self.preview_box.setMaximumBlockCount(400)
        preview_layout.addWidget(self.preview_box)
        root_layout.addWidget(preview_group, 1)

        button_layout = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh Repo Status")
        self.commit_button = QPushButton("Commit Release")
        self.push_button = QPushButton("Push Release")
        self.refresh_button.clicked.connect(self.refresh_repo_status)
        self.commit_button.clicked.connect(self.commit_release)
        self.push_button.clicked.connect(self.push_release)
        button_layout.addWidget(self.refresh_button)
        button_layout.addStretch(1)
        button_layout.addWidget(self.commit_button)
        button_layout.addWidget(self.push_button)
        root_layout.addLayout(button_layout)

        log_group = QGroupBox("Command Log")
        log_layout = QVBoxLayout(log_group)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(800)
        log_layout.addWidget(self.log_box)
        root_layout.addWidget(log_group, 1)

    def _select_bump(self, bump_kind: str, checked: bool) -> None:
        if checked:
            self.selected_bump = bump_kind
        elif self.selected_bump == bump_kind:
            self.selected_bump = None

        for kind, button in self.bump_buttons.items():
            button.blockSignals(True)
            button.setChecked(kind == self.selected_bump)
            button.blockSignals(False)

        self._update_draft_preview()

    def _current_draft(self) -> Optional[ReleaseDraft]:
        if not self.selected_bump:
            return None

        return build_release_draft(
            current_version=self.repo_status.current_version,
            bump_kind=self.selected_bump,
            release_date=self.release_date_label.text().strip(),
            added_text=self.added_notes.toPlainText(),
            changed_text=self.changed_notes.toPlainText(),
            fixed_text=self.fixed_notes.toPlainText(),
        )

    def _update_draft_preview(self) -> None:
        preview_text = "Choose a bump type and enter at least one release note."
        next_version_text = "Choose a bump type"
        draft = None

        try:
            draft = self._current_draft()
        except ValueError as exc:
            preview_text = str(exc)
        else:
            if draft is not None:
                next_version_text = draft.next_version
                preview_text = (
                    f"Commit: {draft.commit_message}\n"
                    f"Tag: {draft.tag_name}\n\n"
                    f"{draft.changelog_entry}"
                )

        self.next_version_label.setText(next_version_text)
        self.preview_box.setPlainText(preview_text)
        self.commit_button.setEnabled(self.repo_status.can_commit and draft is not None)
        self.push_button.setEnabled(self.repo_status.can_push)

    def _apply_repo_status(self) -> None:
        self.current_version_label.setText(self.repo_status.current_version)
        self.branch_label.setText(self.repo_status.current_branch or "unknown")
        self.remote_state_label.setText(self.repo_status.remote_detail)
        self.working_tree_label.setText("dirty" if self.repo_status.is_dirty else "clean")
        if self.repo_status.head_release_version and self.repo_status.head_release_tag:
            head_release = (
                f"{self.repo_status.head_release_version} / {self.repo_status.head_release_tag}"
            )
        elif self.repo_status.head_release_version:
            head_release = f"{self.repo_status.head_release_version} / tag missing"
        else:
            head_release = "-"
        self.head_release_label.setText(head_release)
        self.repo_message_box.setPlainText(format_repo_status_text(self.repo_status))
        self._update_draft_preview()

    def refresh_repo_status(self) -> None:
        self._append_log("Refreshing repo status")
        self.repo_status = inspect_repo_status(fetch_remote=True)
        self._apply_repo_status()

    def _append_log(self, message: str, result: Optional[subprocess.CompletedProcess] = None) -> None:
        chunks = [f"> {message}"]
        if result is not None:
            output = _command_output(result)
            if output:
                chunks.append(output)
            else:
                chunks.append("(no output)")
        self.log_box.appendPlainText("\n".join(chunks))
        self.log_box.appendPlainText("")

    def _run_logged_git(self, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        command_text = "git " + " ".join(args)
        result = _run_git(*args, timeout=timeout)
        self._append_log(command_text, result)
        return result

    def commit_release(self) -> None:
        try:
            draft = self._current_draft()
        except ValueError as exc:
            QMessageBox.warning(self, "Release Draft Invalid", str(exc))
            return

        if draft is None:
            QMessageBox.warning(self, "Release Draft Missing", "Choose a bump type and enter release notes.")
            return

        self.repo_status = inspect_repo_status(fetch_remote=True)
        self._apply_repo_status()
        if not self.repo_status.can_commit:
            QMessageBox.warning(self, "Release Blocked", self.repo_status.status_message)
            return

        confirm = QMessageBox.question(
            self,
            "Commit Release",
            f"Commit {draft.commit_message} and create tag {draft.tag_name}?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        try:
            version_source = VERSION_PATH.read_text(encoding="utf-8")
            changelog_source = CHANGELOG_PATH.read_text(encoding="utf-8")
            updated_version_source = replace_app_version_source(version_source, draft.next_version)
            updated_changelog = prepend_changelog_entry(changelog_source, draft.changelog_entry)
            VERSION_PATH.write_text(updated_version_source, encoding="utf-8")
            CHANGELOG_PATH.write_text(updated_changelog, encoding="utf-8")
            self._append_log(f"Updated {VERSION_PATH.name} and {CHANGELOG_PATH.name}")
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Release Edit Failed", str(exc))
            return

        add_result = self._run_logged_git("add", VERSION_PATH.name, CHANGELOG_PATH.name)
        if add_result.returncode != 0:
            QMessageBox.critical(self, "Git Add Failed", _command_output(add_result) or "git add failed.")
            self.refresh_repo_status()
            return

        commit_result = self._run_logged_git("commit", "-m", draft.commit_message)
        if commit_result.returncode != 0:
            QMessageBox.critical(
                self,
                "Git Commit Failed",
                _command_output(commit_result) or "git commit failed.",
            )
            self.refresh_repo_status()
            return

        tag_result = self._run_logged_git("tag", draft.tag_name)
        if tag_result.returncode != 0:
            QMessageBox.warning(
                self,
                "Tag Creation Failed",
                (
                    f"{draft.commit_message} was committed, but {draft.tag_name} could not be created.\n\n"
                    f"{_command_output(tag_result) or 'git tag failed.'}"
                ),
            )
            self.refresh_repo_status()
            return

        self._clear_release_form()
        self.refresh_repo_status()
        QMessageBox.information(
            self,
            "Release Commit Created",
            f"Created {draft.commit_message} with tag {draft.tag_name}.",
        )

    def push_release(self) -> None:
        self.repo_status = inspect_repo_status(fetch_remote=True)
        self._apply_repo_status()

        if not self.repo_status.can_push or not self.repo_status.head_release_tag:
            QMessageBox.warning(self, "Push Blocked", self.repo_status.status_message)
            return

        confirm = QMessageBox.question(
            self,
            "Push Release",
            (
                f"Push {self.repo_status.current_branch or UPDATE_BRANCH} "
                f"and {self.repo_status.head_release_tag} to {UPDATE_REMOTE}?"
            ),
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        if self.repo_status.main_needs_push:
            main_push_result = self._run_logged_git("push", UPDATE_REMOTE, UPDATE_BRANCH, timeout=180)
            if main_push_result.returncode != 0:
                QMessageBox.critical(
                    self,
                    "Main Push Failed",
                    _command_output(main_push_result) or "git push origin main failed.",
                )
                self.refresh_repo_status()
                return

        if self.repo_status.tag_needs_push:
            tag_push_result = self._run_logged_git(
                "push",
                UPDATE_REMOTE,
                self.repo_status.head_release_tag,
                timeout=180,
            )
            if tag_push_result.returncode != 0:
                QMessageBox.warning(
                    self,
                    "Tag Push Failed",
                    (
                        f"{UPDATE_BRANCH} may already be pushed, but {self.repo_status.head_release_tag} failed.\n\n"
                        f"{_command_output(tag_push_result) or 'git push tag failed.'}"
                    ),
                )
                self.refresh_repo_status()
                return

        self.refresh_repo_status()
        QMessageBox.information(self, "Release Pushed", "Release commit and tag were pushed successfully.")

    def _clear_release_form(self) -> None:
        self.selected_bump = None
        for button in self.bump_buttons.values():
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
        self.added_notes.clear()
        self.changed_notes.clear()
        self.fixed_notes.clear()
        self.release_date_label.setText(date.today().isoformat())


def main() -> int:
    app = QApplication(sys.argv)
    window = ReleaseManagerWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
