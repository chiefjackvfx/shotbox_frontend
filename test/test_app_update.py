from __future__ import annotations

import unittest

from app_update import parse_latest_changelog_preview, parse_version_from_source
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


if __name__ == "__main__":
    unittest.main()
