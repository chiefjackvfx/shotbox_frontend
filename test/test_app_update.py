from __future__ import annotations

import unittest

from app_update import (
    _get_blocking_git_status_lines,
    _should_ignore_git_status_line,
    parse_latest_changelog_preview,
    parse_version_from_source,
)
from app_version import format_version_display


class AppUpdateParsingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
