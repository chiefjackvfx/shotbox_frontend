import ntpath
import os
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plugins_install


class PluginInstallTests(unittest.TestCase):
    def test_houdini_windows_preferences_paths(self):
        class WindowsPath(PureWindowsPath):
            def resolve(self):
                return self
            def is_dir(self):
                return True
        for folder in (r"C:\Users\Artist\Documents\houdini22.0", r"\\server\profiles\Artist Name\houdini21.0"):
            with mock.patch.object(plugins_install, "Path", WindowsPath), mock.patch.object(
                plugins_install, "os", types.SimpleNamespace(path=ntpath)
            ):
                self.assertEqual(plugins_install.resolve_houdini_preferences(folder), WindowsPath(folder))

    def test_houdini_preference_suggestions(self):
        (self.root / "houdini22.0").mkdir()
        (self.root / "Documents" / "houdini21.0").mkdir(parents=True)
        (self.root / "houdini_launch.log").touch()
        with mock.patch.object(plugins_install.Path, "home", return_value=self.root), mock.patch.dict(os.environ, {"HOUDINI_USER_PREF_DIR": ""}):
            candidates = plugins_install.houdini_preferences_candidates()
        self.assertEqual(set(candidates), {str(self.root / "houdini22.0"), str(self.root / "Documents/houdini21.0")})

    def test_houdini_install_update_and_preserve_unrelated_files(self):
        self.patch.stop()
        preferences = self.root / "houdini22.0"
        preferences.mkdir()
        (preferences / "houdini.env").write_text("keep")
        first = plugins_install.install_houdini_plugin(str(preferences))
        self.assertEqual(len(first.installed), 3)
        self.assertFalse(first.failures)
        package = preferences / "packages" / "shotbox_3de_import.json"
        self.assertIn("$HOUDINI_USER_PREF_DIR/shotbox_3de_import", package.read_text())
        second = plugins_install.install_houdini_plugin(str(preferences))
        self.assertEqual(len(second.unchanged), 3)
        runtime = preferences / "shotbox_3de_import/python/shotbox_3de_import.py"
        runtime.write_text("old")
        third = plugins_install.install_houdini_plugin(str(preferences))
        self.assertEqual(third.updated, ["shotbox_3de_import/python/shotbox_3de_import.py"])
        self.assertEqual((preferences / "houdini.env").read_text(), "keep")

    def test_houdini_does_not_register_failed_install_and_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            plugins_install.install_houdini_plugin(str(self.root))
        self.patch.stop()
        with mock.patch.object(plugins_install.os, "replace", side_effect=PermissionError("denied")):
            result = plugins_install.install_houdini_plugin(str(self.root))
        self.assertEqual(len(result.failures), 2)
        self.assertFalse((self.root / "packages/shotbox_3de_import.json").exists())
        self.assertFalse(list(self.root.rglob(".shotbox-*")))
        for folder in ("", str(self.root / "missing")):
            with self.assertRaises((ValueError, FileNotFoundError)):
                plugins_install.resolve_houdini_preferences(folder)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.install = self.root / "3DE installation"
        self.destination = self.install / "sys_data" / "py_scripts"
        self.destination.mkdir(parents=True)
        self.exe = self.install / "bin" / "3DE4"
        self.exe.parent.mkdir()
        self.exe.touch()
        self.source = self.root / "bundle" / "plugins" / "3de"
        self.source.mkdir(parents=True)
        self.module_path = self.source.parents[1] / "plugins_install.py"
        self.patch = mock.patch.object(plugins_install, "__file__", str(self.module_path))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.source / "one.py").write_text("new")

    def test_install_update_unchanged_and_exclusions(self):
        (self.source / "tests").mkdir()
        (self.source / "tests" / "test_one.py").touch()
        (self.source / "__pycache__").mkdir()
        (self.source / "readme.txt").touch()
        (self.source / "fake.py").mkdir()
        (self.destination / "unrelated.py").write_text("keep")
        result = plugins_install.install_3de_plugins(str(self.exe))
        self.assertEqual(result.installed, ["one.py"])
        result = plugins_install.install_3de_plugins(str(self.exe))
        self.assertEqual(result.unchanged, ["one.py"])
        (self.source / "one.py").write_text("updated")
        result = plugins_install.install_3de_plugins(str(self.exe))
        self.assertEqual(result.updated, ["one.py"])
        self.assertEqual((self.destination / "one.py").read_text(), "updated")
        self.assertEqual((self.destination / "unrelated.py").read_text(), "keep")
        self.assertEqual(sorted(p.name for p in self.destination.iterdir()), ["one.py", "unrelated.py"])

    def test_resolution_root_bin_symlink_env_home_path_and_cwd(self):
        root_exe = self.install / "3DE4"
        root_exe.touch()
        link = self.root / "launcher"
        link.symlink_to(self.exe)
        for executable in (self.exe, root_exe, link):
            self.assertEqual(plugins_install.resolve_destination(str(executable)), self.destination)
        with mock.patch.dict(os.environ, {"SHOTBOX_TEST_EXE": str(self.exe)}):
            self.assertEqual(plugins_install.resolve_destination("$SHOTBOX_TEST_EXE"), self.destination)
        with mock.patch.object(plugins_install.os.path, "expanduser", return_value=str(self.exe)):
            self.assertEqual(plugins_install.resolve_destination("~/3DE4"), self.destination)
        with mock.patch.object(plugins_install.shutil, "which", return_value=str(self.exe)):
            self.assertEqual(plugins_install.resolve_destination("3DE4"), self.destination)
        previous = os.getcwd()
        try:
            os.chdir(self.root)
            self.assertEqual(plugins_install.bundled_scripts(), [self.source / "one.py"])
        finally:
            os.chdir(previous)

    def test_windows_drive_and_unc(self):
        for root in (r"C:\Program Files\3DE4", r"\\server\apps\3DE4"):
            expected = PureWindowsPath(root) / "sys_data" / "py_scripts"
            class WindowsPath(PureWindowsPath):
                def is_file(self):
                    return self.name == "3DE4.exe"
                def is_dir(self):
                    return self == expected
                def resolve(self):
                    return self
            with mock.patch.object(plugins_install, "Path", WindowsPath), mock.patch.object(
                plugins_install, "os", types.SimpleNamespace(path=ntpath)
            ):
                self.assertEqual(plugins_install.resolve_destination(str(PureWindowsPath(root) / "bin" / "3DE4.exe")), expected)

    def test_invalid_paths_and_missing_bundle(self):
        for value in ("", str(self.root / "missing.exe"), str(self.root)):
            with self.assertRaises((ValueError, FileNotFoundError)):
                plugins_install.resolve_destination(value)
        orphan = self.root / "orphan.exe"
        orphan.touch()
        with self.assertRaisesRegex(FileNotFoundError, "sys_data"):
            plugins_install.resolve_destination(str(orphan))
        (self.source / "one.py").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "No bundled"):
            plugins_install.bundled_scripts()
        self.source.rmdir()
        with self.assertRaisesRegex(FileNotFoundError, "missing"):
            plugins_install.bundled_scripts()

    def test_replace_failure_preserves_old_file_and_reports_partial_success(self):
        (self.destination / "one.py").write_text("old")
        (self.source / "two.py").write_text("second")
        replace = os.replace
        def fail_one(source, target):
            if target.name == "one.py":
                raise PermissionError("access denied")
            replace(source, target)
        with mock.patch.object(plugins_install.os, "replace", side_effect=fail_one):
            result = plugins_install.install_3de_plugins(str(self.exe))
        self.assertEqual(result.installed, ["two.py"])
        self.assertIn("access denied", result.failures["one.py"])
        self.assertEqual((self.destination / "one.py").read_text(), "old")
        self.assertFalse(list(self.destination.glob(".shotbox-*")))

    def test_tempfile_permission_failure(self):
        with mock.patch.object(plugins_install.tempfile, "NamedTemporaryFile", side_effect=PermissionError("read-only")):
            result = plugins_install.install_3de_plugins(str(self.exe))
        self.assertIn("read-only", result.failures["one.py"])
        self.assertFalse(list(self.destination.iterdir()))


if __name__ == "__main__":
    unittest.main()
