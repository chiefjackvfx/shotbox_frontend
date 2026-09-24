from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication, QMessageBox
import release_manager as rm
from app_update import parse_latest_changelog_preview

STAMP = "[2026-09-24T14:30:00+01:00]"
HISTORY = "## 1.0.0 - 2026-09-01\n\n### Fixed\n\n- Historical fix\n"
SOURCE = f"# Changelog\n\n## Unreleased\n\n### Added\n\n- {STAMP} New feature\n\n### Changed\n\n### Fixed\n\n- {STAMP} Bug fix\n\n" + HISTORY


def result(*, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class PendingCoreTests(unittest.TestCase):
    def test_parse_and_finalize_preserve_timestamps_and_history(self):
        notes = rm.parse_pending_notes(SOURCE)
        self.assertEqual(notes["Added"], [f"{STAMP} New feature"])
        self.assertEqual(notes["Changed"], [])
        entry = rm.build_changelog_entry("1.1.0", "2026-09-24", **{
            "added_notes": notes["Added"], "changed_notes": [], "fixed_notes": notes["Fixed"]})
        final = rm.finalize_pending_release(SOURCE, entry)
        self.assertTrue(final.endswith(HISTORY))
        self.assertEqual(final.count(f"{STAMP} New feature"), 1)
        self.assertEqual(rm.parse_pending_notes(final), dict.fromkeys(rm.NOTE_SECTIONS, []))
        self.assertLess(final.index("## Unreleased"), final.index("## 1.1.0"))

    def test_legacy_and_empty_pending(self):
        self.assertFalse(any(rm.parse_pending_notes(HISTORY).values()))
        self.assertFalse(any(rm.parse_pending_notes(rm.EMPTY_PENDING).values()))
        final = rm.finalize_pending_release(HISTORY, "## 1.0.1\n\n### Fixed\n\n- Manual fix\n")
        self.assertIn("Manual fix", final)
        self.assertTrue(final.endswith(HISTORY))

    def test_malformed_pending_is_rejected(self):
        for source in (SOURCE + "\n## Unreleased\n", SOURCE.replace("### Added", "### Unknown"),
                       SOURCE.replace("### Changed", "### Added"),
                       SOURCE.replace(f"- {STAMP} New feature", "unstructured text")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                rm.finalize_pending_release(source, "## 1.1.0\n")

    def test_preview_skips_pending(self):
        self.assertEqual(parse_latest_changelog_preview(SOURCE), parse_latest_changelog_preview(HISTORY))
        self.assertEqual(parse_latest_changelog_preview(rm.EMPTY_PENDING), "No changelog entries found.")


class ReleaseTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.version = self.root / "app_version.py"
        self.changelog = self.root / "CHANGELOG.md"
        self.index = self.root / "index"
        self.version.write_bytes(b'APP_VERSION = "1.0.0"\n')
        self.changelog.write_text(SOURCE)
        self.index.write_bytes(b"original staging state")
        self.original = [p.read_bytes() for p in (self.version, self.changelog, self.index)]
        for name, value in (("REPO_ROOT", self.root), ("VERSION_PATH", self.version), ("CHANGELOG_PATH", self.changelog)):
            ctx = patch.object(rm, name, value)
            ctx.start()
            self.addCleanup(ctx.stop)
        self.draft = rm.build_release_draft("1.0.0", "minor", "2026-09-24", f"{STAMP} New feature", "", "")

    def test_git_failures_restore_files_and_index(self):
        for failure in ("add", "commit"):
            def git(*args):
                if args[0] == "rev-parse":
                    return result(stdout="index\n")
                self.index.write_bytes(b"new staging state")
                return result(code=1 if args[0] == failure else 0, stderr="failure")
            with self.subTest(failure=failure), self.assertRaises(ValueError):
                rm.commit_release_files(self.draft, SOURCE, git)
            self.assertEqual([p.read_bytes() for p in (self.version, self.changelog, self.index)], self.original)

    def test_write_failure_restores_originals(self):
        write = Path.write_text
        def fail_changelog(path, *args, **kwargs):
            if path == self.changelog:
                raise OSError("write failed")
            return write(path, *args, **kwargs)
        with patch.object(Path, "write_text", fail_changelog), self.assertRaises(OSError):
            rm.commit_release_files(self.draft, SOURCE, lambda *args: result(stdout="index"))
        self.assertEqual([p.read_bytes() for p in (self.version, self.changelog, self.index)], self.original)

    def test_stale_changelog_or_version_blocks_before_git(self):
        for stale_file, text in ((self.changelog, SOURCE + "\n"), (self.version, 'APP_VERSION = "2.0.0"\n')):
            stale_file.write_text(text)
            with patch.object(rm, "_run_git") as git, self.assertRaisesRegex(ValueError, "changed"):
                rm.commit_release_files(self.draft, SOURCE, git)
            git.assert_not_called()
            self.changelog.write_bytes(self.original[1])
            self.version.write_bytes(self.original[0])

    def test_success_converts_pending_without_tagging_or_pushing(self):
        calls = []
        def git(*args):
            calls.append(args)
            return result(stdout="index" if args[0] == "rev-parse" else "")
        rm.commit_release_files(self.draft, SOURCE, git)
        self.assertIn('"1.1.0"', self.version.read_text())
        self.assertIn("## 1.1.0", self.changelog.read_text())
        self.assertFalse(any(rm.parse_pending_notes(self.changelog.read_text()).values()))
        self.assertEqual([c[0] for c in calls], ["rev-parse", "add", "commit"])


class PendingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.changelog = Path(self.temp.name) / "CHANGELOG.md"
        self.changelog.write_text(SOURCE)
        self.status = rm.ReleaseRepoStatus(True, True, False, "1.0.0", "main", "up_to_date", "Ready", True, "Ready")
        for ctx in (patch.object(rm, "CHANGELOG_PATH", self.changelog),
                    patch.object(rm, "inspect_repo_status", return_value=self.status)):
            ctx.start()
            self.addCleanup(ctx.stop)
        with patch.object(rm, "_run_git") as git:
            self.window = rm.ReleaseManagerWindow()
            git.assert_not_called()
        self.addCleanup(self.window.close)

    def test_prefill_and_reload_protect_edits(self):
        self.assertEqual(self.window.added_notes.toPlainText(), f"{STAMP} New feature")
        self.assertIsNone(self.window.selected_bump)
        self.window.added_notes.setPlainText("Manual edit")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.window.load_pending_notes()
        self.assertEqual(self.window.added_notes.toPlainText(), "Manual edit")
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window.load_pending_notes()
        self.assertEqual(self.window.added_notes.toPlainText(), f"{STAMP} New feature")

    def test_windows_line_endings_are_kept_in_loaded_snapshot(self):
        raw = SOURCE.replace("\n", "\r\n").encode("utf-8")
        self.changelog.write_bytes(raw)
        self.window.load_pending_notes()
        self.assertEqual(self.window._loaded_changelog.encode("utf-8"), raw)
        self.assertEqual(self.window.fixed_notes.toPlainText(), f"{STAMP} Bug fix")

    def test_malformed_reload_preserves_edited_notes(self):
        self.changelog.write_text(SOURCE.replace("### Added", "### Unknown"))
        with patch.object(QMessageBox, "warning") as warning:
            self.window.load_pending_notes()
        warning.assert_called_once()
        self.assertEqual(self.window.added_notes.toPlainText(), f"{STAMP} New feature")
        self.assertEqual(self.window._loaded_changelog, SOURCE)

    def test_tag_failure_keeps_committed_release(self):
        self.window._select_bump("minor", True)
        def commit(draft, source, run_git):
            self.changelog.write_text(rm.finalize_pending_release(source, draft.changelog_entry))
        with (patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes),
              patch.object(QMessageBox, "warning") as warning,
              patch.object(rm, "commit_release_files", side_effect=commit) as commit_mock,
              patch.object(self.window, "_run_logged_git", return_value=result(code=1, stderr="tag failed")) as git):
            self.window.commit_release()
        commit_mock.assert_called_once()
        git.assert_called_once_with("tag", "v1.1.0")
        warning.assert_called_once()
        self.assertIn("## 1.1.0", self.changelog.read_text())
        self.assertFalse(any(rm.parse_pending_notes(self.changelog.read_text()).values()))
        self.assertEqual(self.window.added_notes.toPlainText(), "")


if __name__ == "__main__":
    unittest.main()
