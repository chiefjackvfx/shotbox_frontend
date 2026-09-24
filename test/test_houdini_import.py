import contextlib
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('houdini_import_under_test', ROOT / 'plugins/houdini/python/shotbox_3de_import.py')
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class HoudiniImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.hou = mock.Mock()
        self.hou.expandString.side_effect = lambda p: p
        self.hou.undos.group.side_effect = lambda *a: contextlib.nullcontext()
        self.modules = mock.patch.dict(sys.modules, {'hou': self.hou})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.export = self.root / 'camera export.py'
        self.export.write_text('hou.import_marker(__name__, __file__)')

    def test_execution_once_namespace_and_source_encoding(self):
        self.export.write_bytes(b'# coding: latin-1\nhou.import_marker("caf\xe9", __name__, __file__)')
        importer.import_export(str(self.export))
        self.hou.import_marker.assert_called_once_with('café', '__main__', str(self.export))
        self.hou.undos.group.assert_called_once()

    def test_compile_before_mutation_and_no_retry_on_partial_error(self):
        self.export.write_text('this is not valid python!')
        with self.assertRaises(SyntaxError):
            importer.import_export(str(self.export))
        self.hou.undos.group.assert_not_called()
        self.export.write_text('hou.import_marker()\nraise ValueError("broken")')
        with self.assertRaisesRegex(RuntimeError, 'Undo'):
            importer.import_export(str(self.export))
        self.hou.import_marker.assert_called_once()

    def test_shelf_opens_browser_directly_and_imports_once(self):
        self.hou.ui.selectFile.return_value = str(self.export)
        importer.show_import_menu()
        self.hou.ui.selectFile.assert_called_once_with(
            title="Choose 3DE Houdini Export", pattern="*.py",
            chooser_mode=self.hou.fileChooserMode.Read,
        )
        self.hou.ui.selectFromList.assert_not_called()
        self.hou.import_marker.assert_called_once()
        self.hou.ui.setStatusMessage.assert_called_once()

    def test_browser_cancellation_does_nothing(self):
        self.hou.ui.selectFile.return_value = ''
        importer.show_import_menu()
        self.hou.import_marker.assert_not_called()
        self.hou.ui.displayMessage.assert_not_called()
        self.hou.ui.setStatusMessage.assert_not_called()
        self.hou.ui.selectFromList.assert_not_called()

    def test_missing_file_reports_error(self):
        self.hou.ui.selectFile.return_value = str(self.root / 'missing.py')
        with mock.patch.object(importer.traceback, 'print_exc') as trace:
            importer.show_import_menu()
        self.hou.import_marker.assert_not_called()
        self.hou.ui.displayMessage.assert_called_once()
        trace.assert_called_once()

    def test_houdini_path_expansion(self):
        self.hou.expandString.side_effect = None
        self.hou.expandString.return_value = str(self.export)
        importer.import_export('$HIP/camera.py')
        self.hou.expandString.assert_called_with('$HIP/camera.py')
        self.hou.import_marker.assert_called_once()

    def test_live_houdini_variables_override_stale_python_environment(self):
        for variable in ("JOB", "HIP"):
            for reference in (f"${variable}/camera export.py", f"${{{variable}}}/camera export.py"):
                with self.subTest(reference=reference), mock.patch.dict(os.environ, {variable: str(self.root / "wrong-project")}):
                    self.hou.reset_mock()
                    self.hou.expandString.side_effect = lambda value: (
                        str(self.export) if value == reference else value
                    )
                    importer.import_export(reference)
                    self.hou.expandString.assert_called_once_with(reference)
                    self.hou.import_marker.assert_called_once_with("__main__", str(self.export))
