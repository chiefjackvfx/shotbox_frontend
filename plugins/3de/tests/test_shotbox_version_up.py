import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, Mock, patch


FRONTEND = Path(__file__).resolve().parents[3]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


app_cache = load_module("version_up_test_cache", FRONTEND / "app_cache.py")
with patch.dict(sys.modules, {"tde4": types.ModuleType("tde4")}):
    version_up = load_module(
        "shotbox_version_up", FRONTEND / "plugins/3de/shotbox_version_up.py"
    )


class VersionUpTests(unittest.TestCase):
    def setUp(self):
        lease = app_cache.temporary_cache_lease()
        lease.__enter__()
        self.addCleanup(lease.__exit__, None, None, None)
        temporary = tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp"))
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.current = self.directory / "etv020_matchmove_v001.3de"
        self.current.write_bytes(b"previously saved project")
        self.tde = Mock(spec=[
            "getProjectPath", "saveProject", "loadProject", "postQuestionRequester"
        ])
        self.tde.getProjectPath.side_effect = lambda: str(self.current)
        self.tde.saveProject.side_effect = self.save_project
        self.tde.loadProject.return_value = 1
        patcher = patch.object(version_up, "tde4", self.tde)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save_project(self, path):
        Path(path).write_bytes(b"current edits saved by 3DE")
        return 1

    def assert_no_save_or_load(self):
        self.tde.saveProject.assert_not_called()
        self.tde.loadProject.assert_not_called()

    def test_screenshot_changes_only_project_version_and_copies_saved_edits(self):
        for shot in ("etv021", "etv022"):
            (self.directory / (shot + "_matchmove_v001.3de")).write_bytes(b"other shot")
        destination = self.directory / "etv020_matchmove_v002.3de"

        def load_project(path):
            self.assertEqual(Path(path), destination)
            self.assertEqual(destination.read_bytes(), b"current edits saved by 3DE")
            return 1

        self.tde.loadProject.side_effect = load_project
        version_up.version_up_project()
        self.tde.saveProject.assert_called_once_with(str(self.current))
        self.tde.loadProject.assert_called_once_with(str(destination))
        self.assertEqual(self.current.read_bytes(), destination.read_bytes())
        self.assertFalse((self.directory / "etv023_matchmove_v001.3de").exists())

    def test_only_matching_project_files_determine_highest_version(self):
        for name in ("etv020_matchmove_v005.3de", "etv021_matchmove_v999.3de",
                     "etv020_matchmove_v099.3de.bak", "etv020_layout_v999.3de"):
            (self.directory / name).write_bytes(b"existing")
        (self.directory / "etv020_matchmove_v900.3de").mkdir()
        version_up.version_up_project()
        self.tde.loadProject.assert_called_once_with(
            str(self.directory / "etv020_matchmove_v006.3de")
        )

    def test_case_padding_and_digit_growth(self):
        for original, expected in (("V009", "V010"), ("v999", "v1000"), ("v9", "v10")):
            with self.subTest(original=original):
                self.current = self.directory / ("unique_" + original + ".3DE")
                self.current.write_bytes(b"original")
                version_up.version_up_project()
                self.assertEqual(
                    Path(self.tde.loadProject.call_args.args[0]).name,
                    "unique_" + expected + ".3DE"
                )
                self.current.unlink()
                (self.directory / ("unique_" + expected + ".3DE")).unlink()

    def test_unrecognised_names_cancel_before_saving(self):
        for name in ("etv020_matchmove.3de", "etv020_matchmove_v001_backup.3de",
                     "etv020_matchmove_v001.txt", "etv020_matchmove_v001\n.3de"):
            with self.subTest(name=name):
                self.current = self.directory / name
                version_up.version_up_project()
                self.assert_no_save_or_load()

    def test_no_open_project(self):
        self.tde.getProjectPath.side_effect = None
        self.tde.getProjectPath.return_value = None
        version_up.version_up_project()
        self.assert_no_save_or_load()

    def test_scan_error_cancels_before_saving(self):
        with patch.object(version_up.os, "scandir", side_effect=OSError("unreadable")):
            version_up.version_up_project()
        self.assert_no_save_or_load()

    def test_error_mid_scan_cancels_before_saving(self):
        def entries():
            yield types.SimpleNamespace(name=self.current.name, is_file=lambda: True)
            raise OSError("connection lost during scan")

        context_manager = MagicMock()
        context_manager.__enter__.return_value = entries()
        with patch.object(version_up.os, "scandir", return_value=context_manager):
            version_up.version_up_project()
        self.assert_no_save_or_load()

    def test_current_version_is_floor_even_when_scan_misses_it(self):
        self.current = self.directory / "etv020_matchmove_v010.3de"
        self.current.write_bytes(b"original")
        with patch.object(version_up, "highest_version_on_disk", return_value=5):
            version_up.version_up_project()
        self.tde.loadProject.assert_called_once_with(
            str(self.directory / "etv020_matchmove_v011.3de")
        )

    def test_destination_directory_collision_cancels_before_saving(self):
        (self.directory / "etv020_matchmove_v002.3de").mkdir()
        version_up.version_up_project()
        self.assert_no_save_or_load()

    def test_failed_save_never_creates_or_opens_new_version(self):
        for failure in (0, OSError("save failed")):
            with self.subTest(failure=failure):
                self.tde.saveProject.side_effect = (
                    failure if isinstance(failure, Exception) else lambda path: 0
                )
                version_up.version_up_project()
                self.tde.loadProject.assert_not_called()
                self.assertFalse((self.directory / "etv020_matchmove_v002.3de").exists())

    def test_file_created_after_scan_is_not_overwritten(self):
        destination = self.directory / "etv020_matchmove_v002.3de"

        def save_with_collision(path):
            destination.write_bytes(b"another artist's project")
            return self.save_project(path)

        self.tde.saveProject.side_effect = save_with_collision
        version_up.version_up_project()
        self.assertEqual(destination.read_bytes(), b"another artist's project")
        self.tde.loadProject.assert_not_called()
        self.assertIn("not overwritten", self.tde.postQuestionRequester.call_args.args[1])

    def test_partial_copy_is_removed_and_never_opened(self):
        def fail_copy(source, destination):
            destination.write(b"partial")
            raise OSError("disk full")

        with patch.object(version_up.shutil, "copyfileobj", side_effect=fail_copy):
            version_up.version_up_project()
        self.assertFalse((self.directory / "etv020_matchmove_v002.3de").exists())
        self.assertEqual(self.current.read_bytes(), b"current edits saved by 3DE")
        self.tde.loadProject.assert_not_called()

    def test_flush_failure_removes_new_file(self):
        with patch.object(version_up.os, "fsync", side_effect=OSError("flush failed")):
            version_up.version_up_project()
        self.assertFalse((self.directory / "etv020_matchmove_v002.3de").exists())
        self.tde.loadProject.assert_not_called()

    def test_cleanup_failure_reports_incomplete_path(self):
        with patch.object(version_up.shutil, "copyfileobj", side_effect=OSError("disk full")), \
                patch.object(version_up.os, "remove", side_effect=OSError("access denied")):
            version_up.version_up_project()
        self.tde.loadProject.assert_not_called()
        message = self.tde.postQuestionRequester.call_args.args[1]
        self.assertIn("incomplete file", message)
        self.assertIn(str(self.directory / "etv020_matchmove_v002.3de"), message)

    def test_load_failure_keeps_completed_copy_and_reports_its_path(self):
        for failure in (0, RuntimeError("load failed")):
            with self.subTest(failure=failure):
                self.tde.loadProject.side_effect = (
                    failure if isinstance(failure, Exception) else lambda path: 0
                )
                version_up.version_up_project()
                destination = Path(self.tde.loadProject.call_args.args[0])
                self.assertEqual(destination.read_bytes(), b"current edits saved by 3DE")
                message = self.tde.postQuestionRequester.call_args.args[1]
                self.assertIn("could not open", message)
                self.assertIn(str(destination), message)


if __name__ == "__main__":
    unittest.main()
