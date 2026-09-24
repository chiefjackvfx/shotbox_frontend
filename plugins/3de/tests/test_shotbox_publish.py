import ntpath
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "shotbox_publish.py"


class PublishDiscoveryTests(unittest.TestCase):
    def test_houdini_history_order_retention_and_atomic_failure(self):
        module, _ = self.load_publisher()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"SHOTBOX_HOUDINI_HISTORY": str(Path(tmp) / "history.log")}):
            log = Path(tmp) / "history.log"
            for number in range(12):
                module.record_houdini_export(str(Path(tmp) / f"café_{number}.py"))
            module.record_houdini_export(str(Path(tmp) / "café_8.py"))
            lines = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 10)
            self.assertEqual(len(set(lines)), 10)
            self.assertTrue(lines[0].endswith("café_8.py"))
            self.assertTrue(lines[1].endswith("café_11.py"))
            before = log.read_bytes()
            with mock.patch.object(module.os, "replace", side_effect=PermissionError("locked")):
                with self.assertRaises(PermissionError):
                    module.record_houdini_export(str(Path(tmp) / "new.py"))
            self.assertEqual(log.read_bytes(), before)
            self.assertEqual(len(list(Path(tmp).iterdir())), 1)

    def test_houdini_history_requires_fresh_successful_output(self):
        module, _ = self.load_publisher()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(module, "record_houdini_export") as record:
            output = Path(tmp) / "camera.py"
            for existing in (None, "", "old export"):
                if existing is not None:
                    output.write_text(existing)
                with mock.patch.object(module, "silent_exec", return_value=(True, None)):
                    self.assertFalse(module.export_houdini(str(output), 1001)[0])
            record.assert_not_called()
            def write(*args):
                output.write_text("new export with data")
                return True, None
            with mock.patch.object(module, "silent_exec", side_effect=write):
                self.assertEqual(module.export_houdini(str(output), 1001), (True, None))
            record.assert_called_once_with(str(output))
            record.reset_mock()
            with mock.patch.object(module, "silent_exec", return_value=(False, "export error")):
                self.assertEqual(module.export_houdini(str(output), 1001), (False, "export error"))
            record.assert_not_called()
            output.unlink()
            record.side_effect = PermissionError("history read-only")
            with mock.patch.object(module, "silent_exec", side_effect=write):
                ok, warning = module.export_houdini(str(output), 1001)
            self.assertTrue(ok)
            self.assertIn("history read-only", warning)

    def load_publisher(self, filename=None, compiled_filename="<string>"):
        tde = mock.Mock()
        module = types.ModuleType("shotbox_publish_test")
        if filename is not None:
            module.__file__ = filename
        with mock.patch.dict(sys.modules, {"tde4": tde}):
            exec(compile(SOURCE.read_text(), compiled_filename, "exec"), module.__dict__)
        tde.get3DEInstallPath.assert_not_called()
        return module, tde

    def test_launch_directory_and_script_namespace_do_not_affect_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frontend = root / "shotbox_frontend"
            frontend.mkdir()
            install = root / "3DE installation"
            scripts = install / "sys_data" / "py_scripts"
            scripts.mkdir(parents=True)
            library = install / "sys_data" / "py311_inst" / "lib" / "python3.11" / "site-packages"
            library.mkdir(parents=True)
            # A decoy in the inherited cwd must never be executed.
            (frontend / "export_nuke.py").write_text("raise RuntimeError('wrong directory')")
            (scripts / "export_nuke.py").write_text("tde4.export_marker()")
            previous = os.getcwd()
            try:
                os.chdir(frontend)
                for filename in (None, "shotbox_publish.py", "<string>", str(frontend / "launcher.py")):
                    with self.subTest(filename=filename):
                        module, tde = self.load_publisher(filename)
                        tde.get3DEInstallPath.return_value = str(install)
                        module.resolve_dependency_paths()
                        self.assertEqual(module.SCRIPTS_DIR, str(scripts))
                        self.assertEqual(module.ALEMBIC_LIB, str(library))
                        self.assertEqual(module.export_nuke_camera("camera.nk", 1001), (True, None))
                        tde.export_marker.assert_called_once()
            finally:
                os.chdir(previous)

    def windows_os(self, exists=True):
        path = types.SimpleNamespace(
            normpath=ntpath.normpath, join=ntpath.join, isabs=ntpath.isabs,
            splitdrive=ntpath.splitdrive, isdir=mock.Mock(return_value=exists),
        )
        return types.SimpleNamespace(name="nt", path=path)

    def test_windows_drive_spaces_unc_and_alembic_layout(self):
        for install in ("C:/Program Files/3DE4/", r"\\server\apps\3DE4", r"D:\Apps\old\..\3DE4"):
            with self.subTest(install=install):
                module, tde = self.load_publisher()
                tde.get3DEInstallPath.return_value = install
                module.os = self.windows_os()
                expected_data = ntpath.join(ntpath.normpath(install), "sys_data")
                py_inst = ntpath.join(expected_data, "py311_inst")
                module.glob = types.SimpleNamespace(glob=mock.Mock(return_value=[py_inst]))
                module.resolve_dependency_paths()
                self.assertEqual(module.SCRIPTS_DIR, ntpath.join(expected_data, "py_scripts"))
                self.assertEqual(module.ALEMBIC_LIB, ntpath.join(py_inst, "Lib", "site-packages"))
                module.glob.glob.assert_called_once_with(ntpath.join(expected_data, "py*_inst"))

    def test_invalid_installation_stops_publish_with_diagnostic(self):
        for install in (None, "", "relative/3DE4", "C:3DE4", r"\3DE4"):
            with self.subTest(install=install):
                module, tde = self.load_publisher()
                module.os = self.windows_os()
                tde.get3DEInstallPath.return_value = install
                module.shotbox_publish()
                message = tde.postQuestionRequester.call_args.args[1]
                self.assertIn(repr(install), message)
                self.assertIn("Expected scripts directory:", message)
                tde.getProjectPath.assert_not_called()
                self.assertIsNone(module.SCRIPTS_DIR)

    def test_missing_scripts_directory_and_api_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, tde = self.load_publisher()
            tde.get3DEInstallPath.return_value = tmp
            module.shotbox_publish()
            message = tde.postQuestionRequester.call_args.args[1]
            self.assertIn(str(Path(tmp) / "sys_data" / "py_scripts"), message)
            self.assertIn("does not exist", message)
            tde.getProjectPath.assert_not_called()
            tde.get3DEInstallPath.side_effect = RuntimeError("API unavailable")
            module.shotbox_publish()
            self.assertIn("API unavailable", tde.postQuestionRequester.call_args.args[1])

    def test_missing_exporters_include_full_paths_and_blender_aliases_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            scripts = Path(tmp) / "sys_data" / "py_scripts"
            scripts.mkdir(parents=True)
            module, tde = self.load_publisher()
            tde.get3DEInstallPath.return_value = tmp
            module.resolve_dependency_paths()
            self.assertIsNone(module.ALEMBIC_LIB)
            ok, error = module.export_nuke_camera("camera.nk", 1001)
            self.assertFalse(ok)
            self.assertIn(str(scripts / "export_nuke.py"), error)
            ok, error = module.export_blender("camera.py", 1001)
            self.assertFalse(ok)
            for name in ("exportBlender.py", "export_blender.py"):
                self.assertIn(str(scripts / name), error)
            for name in ("exportBlender.py", "export_blender.py"):
                exporter = scripts / name
                exporter.write_text("tde4.export_marker()")
                try:
                    self.assertEqual(module.export_blender("camera.py", 1001), (True, None))
                finally:
                    exporter.unlink()
            self.assertEqual(tde.export_marker.call_count, 2)


if __name__ == "__main__":
    unittest.main()
