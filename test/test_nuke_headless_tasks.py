from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import nuke_headless_tasks as module


class FindNukeExecutableTests(unittest.TestCase):
    def test_custom_directory_returns_nuke_binary_inside_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            install_dir = Path(tmpdir) / "Nuke16.0v8"
            install_dir.mkdir()
            nuke_binary = install_dir / "Nuke16.0"
            nuke_binary.write_text("", encoding="utf-8")

            with mock.patch.object(module.platform, "system", return_value="Linux"):
                with mock.patch.object(module.shutil, "which", return_value=None):
                    found = module.find_nuke_executable([str(install_dir)])

            self.assertEqual(found, str(nuke_binary))

    def test_custom_file_path_is_returned_directly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nuke_binary = Path(tmpdir) / "Nuke16.0"
            nuke_binary.write_text("", encoding="utf-8")

            with mock.patch.object(module.shutil, "which", return_value=None):
                found = module.find_nuke_executable([str(nuke_binary)])

            self.assertEqual(found, str(nuke_binary))


if __name__ == "__main__":
    unittest.main()
