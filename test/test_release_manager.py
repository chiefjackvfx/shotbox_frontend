from __future__ import annotations

import unittest

from release_manager import (
    build_release_draft,
    bump_version,
    parse_release_commit_version,
    prepend_changelog_entry,
    replace_app_version_source,
)


class ReleaseManagerCoreTests(unittest.TestCase):
    def test_bump_version_modes(self):
        self.assertEqual(bump_version("1.2.3", "major"), "2.0.0")
        self.assertEqual(bump_version("1.2.3", "minor"), "1.3.0")
        self.assertEqual(bump_version("1.2.3", "patch"), "1.2.4")

    def test_bump_version_rejects_invalid_semver(self):
        with self.assertRaises(ValueError):
            bump_version("1.2", "major")

    def test_build_release_draft_generates_commit_tag_and_changelog(self):
        draft = build_release_draft(
            current_version="0.1.1",
            bump_kind="major",
            release_date="2026-03-28",
            added_text="New release manager\n",
            changed_text="Developer flow is faster",
            fixed_text="",
        )

        self.assertEqual(draft.next_version, "1.0.0")
        self.assertEqual(draft.commit_message, "Release 1.0.0")
        self.assertEqual(draft.tag_name, "v1.0.0")
        self.assertIn("## 1.0.0 - 2026-03-28", draft.changelog_entry)
        self.assertIn("### Added", draft.changelog_entry)
        self.assertIn("- New release manager", draft.changelog_entry)
        self.assertIn("### Changed", draft.changelog_entry)
        self.assertNotIn("### Fixed", draft.changelog_entry)

    def test_build_release_draft_requires_at_least_one_note(self):
        with self.assertRaises(ValueError):
            build_release_draft(
                current_version="0.1.1",
                bump_kind="patch",
                release_date="2026-03-28",
                added_text="",
                changed_text="",
                fixed_text="",
            )

    def test_prepend_changelog_entry_inserts_before_existing_releases(self):
        existing = """# Changelog

All notable frontend publish changes should be documented in this file.

## 0.1.0 - 2026-03-28

### Fixed

- Older note
"""
        new_entry = """## 0.1.1 - 2026-03-29

### Fixed

- New note
"""
        updated = prepend_changelog_entry(existing, new_entry)
        self.assertIn("## 0.1.1 - 2026-03-29", updated)
        self.assertLess(updated.index("## 0.1.1 - 2026-03-29"), updated.index("## 0.1.0 - 2026-03-28"))

    def test_replace_app_version_source(self):
        source = 'APP_VERSION = "0.1.1"\nUPDATE_BRANCH = "main"\n'
        updated = replace_app_version_source(source, "1.0.0")
        self.assertIn('APP_VERSION = "1.0.0"', updated)

    def test_parse_release_commit_version(self):
        self.assertEqual(parse_release_commit_version("Release 1.0.0"), "1.0.0")
        self.assertIsNone(parse_release_commit_version("Fix installer"))


if __name__ == "__main__":
    unittest.main()
