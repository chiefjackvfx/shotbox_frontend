from __future__ import annotations

import unittest
from unittest import mock

from app_update import (
    UpdateStatus,
    _get_blocking_git_status_lines,
    _should_ignore_git_status_line,
    check_for_updates,
    parse_latest_changelog_preview,
    parse_version_from_source,
)
from app_version import format_version_display


class AppUpdateParsingTests(unittest.TestCase):
    def _status(self, *, branch="main", dirty=False):
        return UpdateStatus(
            supported=True,
            can_check=True,
            can_update=False,
            has_update=False,
            is_dirty=dirty,
            branch="main",
            current_branch=branch,
            current_version="1.0.0",
            current_commit="current",
            current_display="1.0.0 (current)",
            status_message="Ready to check for updates.",
        )

    def _check_with_graph(self, *, ahead, behind, branch="main", dirty=False):
        status = self._status(branch=branch, dirty=dirty)
        with (
            mock.patch("app_update.inspect_install", return_value=status),
            mock.patch("app_update._get_remote_commit", return_value="remotecommit"),
            mock.patch(
                "app_update._read_git_file",
                side_effect=['APP_VERSION = "2.0.0"\n', "## 2.0.0\n- Update"],
            ),
            mock.patch(
                "app_update._get_ahead_behind", return_value=(ahead, behind)
            ),
            mock.patch("app_update._run_git") as run_git,
        ):
            result = check_for_updates(fetch_remote=False)
        run_git.assert_not_called()
        return result

    def test_parse_version_from_source(self):
        source = 'APP_VERSION = "1.2.3"\nUPDATE_BRANCH = "main"\n'
        self.assertEqual(parse_version_from_source(source), "1.2.3")

    def test_parse_latest_changelog_preview(self):
        changelog = """# Changelog

## 1.2.3 - 2026-03-28

### Added
- New updater

### Fixed
- Safer checks

## 1.2.2 - 2026-03-20
- Older entry
"""
        preview = parse_latest_changelog_preview(changelog)
        self.assertIn("1.2.3 - 2026-03-28", preview)
        self.assertIn("### Added", preview)
        self.assertIn("- New updater", preview)

    def test_format_version_display(self):
        self.assertEqual(format_version_display("1.2.3", "abcdef123"), "1.2.3 (abcdef1)")
        self.assertEqual(format_version_display("1.2.3", None), "1.2.3 (unknown)")

    def test_should_ignore_untracked_settings_file(self):
        self.assertTrue(_should_ignore_git_status_line("?? Giger_settings.yaml"))
        self.assertTrue(_should_ignore_git_status_line("?? profiles/Giger_settings.yaml"))
        self.assertFalse(_should_ignore_git_status_line(" M Giger_settings.yaml"))
        self.assertFalse(_should_ignore_git_status_line("?? requirements.txt"))

    def test_get_blocking_git_status_lines_filters_untracked_settings(self):
        class Result:
            def __init__(self, stdout: str, returncode: int = 0):
                self.stdout = stdout
                self.returncode = returncode

        import app_update

        original_run_git = app_update._run_git
        try:
            app_update._run_git = lambda *args, **kwargs: Result(
                "?? Giger_settings.yaml\n M settings.py\n?? notes.txt\n"
            )
            self.assertEqual(_get_blocking_git_status_lines(), [" M settings.py", "?? notes.txt"])
        finally:
            app_update._run_git = original_run_git

    def test_clean_main_checkout_strictly_behind_can_update(self):
        status = self._check_with_graph(ahead=0, behind=2)

        self.assertTrue(status.has_update)
        self.assertTrue(status.can_update)

    def test_dirty_main_checkout_reports_blocked_update(self):
        status = self._check_with_graph(ahead=0, behind=2, dirty=True)

        self.assertTrue(status.has_update)
        self.assertFalse(status.can_update)
        self.assertIn("local changes", status.status_message)

    def test_diverged_main_checkout_reports_blocked_update(self):
        status = self._check_with_graph(ahead=1, behind=2)

        self.assertTrue(status.has_update)
        self.assertFalse(status.can_update)
        self.assertIn("diverged", status.status_message)

    def test_non_main_checkout_reports_blocked_update_when_behind(self):
        status = self._check_with_graph(
            ahead=1, behind=2, branch="feature/update-work"
        )

        self.assertTrue(status.has_update)
        self.assertFalse(status.can_update)
        self.assertIn("feature/update-work", status.status_message)

    def test_detached_checkout_reports_blocked_update_when_behind(self):
        status = self._check_with_graph(ahead=0, behind=1, branch=None)

        self.assertTrue(status.has_update)
        self.assertFalse(status.can_update)
        self.assertIn("detached HEAD", status.status_message)


if __name__ == "__main__":
    unittest.main()
