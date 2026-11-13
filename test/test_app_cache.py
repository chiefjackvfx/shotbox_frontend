from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app_cache

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class AppCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / ".cache"
        patcher = mock.patch.object(app_cache, "CACHE_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_creates_cache_categories_in_frontend_cache_root(self):
        self.assertEqual(app_cache.cache_directory("temp"), self.root / "temp")
        self.assertTrue((self.root / "temp").is_dir())

    def test_clears_only_cache_contents_and_keeps_the_root(self):
        outside = Path(self.temporary.name) / "settings.yaml"
        outside.write_text("settings", encoding="utf-8")
        (app_cache.cache_directory("exr-previews-v1") / "frame.jpg").touch()
        (app_cache.cache_directory("temp") / "request.json").touch()
        result = app_cache.clear_cache_files()
        self.assertEqual(result["errors"], [])
        self.assertEqual([path for path in self.root.iterdir() if path.name != ".lease.lock"], [])
        self.assertEqual(outside.read_text(encoding="utf-8"), "settings")

    def test_clear_preserves_dormant_nuke_credentials_config_and_pending_uploads(self):
        state = app_cache.cache_directory("nuke-time") / "artist-workstation"
        state.mkdir()
        contents = {
            "config.json": b'{"enabled": true}',
            "device.token": b"private-test-token",
            "outbox/pending.sqlite3": b"unacknowledged-test-record",
        }
        for name, data in contents.items():
            path = state / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
        preview = app_cache.cache_directory("exr-previews-v1") / "frame.jpg"
        preview.touch()
        with mock.patch.object(app_cache, "_active_temp_tasks", 0), \
             mock.patch.object(app_cache, "_external_temp_tasks", return_value=False):
            result = app_cache.clear_cache_files()
        self.assertFalse(result["active_temp"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["removed"], 1)
        self.assertFalse(preview.exists())
        for name, data in contents.items():
            self.assertEqual((state / name).read_bytes(), data)

    def test_clear_does_not_follow_symlinks_outside_cache(self):
        outside = Path(self.temporary.name) / "project"
        outside.mkdir()
        sentinel = outside / "plate.exr"
        sentinel.touch()
        app_cache.cache_directory()
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        app_cache.clear_cache_files()
        self.assertTrue(sentinel.exists())
        self.assertFalse((self.root / "linked").exists())

    def test_running_task_temporary_files_survive_until_task_finishes(self):
        started = threading.Event()
        release = threading.Event()
        path = app_cache.cache_directory("temp") / "active.json"

        @app_cache.temporary_cache_task
        def operation():
            path.touch()
            started.set()
            release.wait(3)

        thread = threading.Thread(target=operation)
        thread.start()
        try:
            self.assertTrue(started.wait(1))
            (app_cache.cache_directory("exr-previews-v1") / "proxy.jpg").touch()
            result = app_cache.clear_cache_files()
            self.assertTrue(result["active_temp"])
            self.assertTrue(path.exists())
            self.assertFalse((self.root / "exr-previews-v1").exists())
        finally:
            release.set()
            thread.join(3)
        result = app_cache.clear_cache_files()
        self.assertFalse(result["active_temp"])
        self.assertFalse(path.exists())

    def test_failed_tasks_release_temporary_file_protection(self):
        @app_cache.temporary_cache_task
        def operation():
            (app_cache.cache_directory("temp") / "failed.json").touch()
            raise RuntimeError("failed")

        with self.assertRaises(RuntimeError):
            operation()
        result = app_cache.clear_cache_files()
        self.assertFalse(result["active_temp"])
        self.assertEqual([path for path in self.root.iterdir() if path.name != ".lease.lock"], [])

    def test_reports_files_that_cannot_be_removed(self):
        category = app_cache.cache_directory("exr-previews-v1")
        with mock.patch.object(app_cache.shutil, "rmtree", side_effect=PermissionError("locked")):
            result = app_cache.clear_cache_files()
        self.assertTrue(category.exists())
        self.assertIn("locked", result["errors"][0])


class NukeStatePathTests(unittest.TestCase):
    def test_nuke_only_plugin_parent_import_needs_neither_frontend_nor_qt(self):
        frontend = Path(__file__).resolve().parents[1]
        script = f"""
import sys
sys.path.insert(0, {str(frontend / 'plugins' / 'nuke')!r})
class BlockFrontendImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('app_cache', 'PyQt6', 'PySide2', 'PySide6', 'nuke'):
            raise AssertionError('unexpected frontend/Qt import: ' + fullname)
sys.meta_path.insert(0, BlockFrontendImports())
from nuke_time_calc.managed_setup import default_config_path
path = default_config_path()
assert str(path.parent.parent) == {str(frontend / '.cache' / 'nuke-time')!r}, path
assert 'app_cache' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-I", "-c", script],
                                capture_output=True, text=True, timeout=10,
                                cwd=tempfile.gettempdir())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_namespace_is_stable_safe_and_distinguishes_users_and_workstations(self):
        from plugins.nuke.nuke_time_calc.managed_setup import default_config_path

        def selected(user, host):
            with mock.patch("getpass.getuser", return_value=user), \
                 mock.patch("socket.gethostname", return_value=host):
                return default_config_path()

        path = selected("../../artist/../unsafe\\name", "studio/../host")
        self.assertEqual(path, selected("../../artist/../unsafe\\name", "studio/../host"))
        self.assertRegex(path.parent.name, r"^[a-zA-Z0-9_-]+$")
        self.assertNotIn("..", path.parts)
        self.assertEqual(path.parent.parent.name, "nuke-time")
        # Sanitizing labels alone must not merge different user/host identities.
        self.assertNotEqual(selected("artist/a", "host"), selected("artist?a", "host"))
        self.assertNotEqual(selected("artist", "host/a"), selected("artist", "host?a"))
        self.assertNotEqual(selected("artist-host", "box"), selected("artist", "host-box"))

    def test_default_config_is_install_cache_without_creating_files(self):
        from plugins.nuke.nuke_time_calc.managed_setup import default_config_path

        frontend = Path(__file__).resolve().parents[1]
        with mock.patch("pathlib.Path.mkdir", side_effect=AssertionError("must not create state")), \
             mock.patch("pathlib.Path.stat", side_effect=AssertionError("must not inspect state")), \
             mock.patch("pathlib.Path.resolve", side_effect=AssertionError("must not resolve on disk")), \
             mock.patch("pathlib.Path.open", side_effect=AssertionError("must not open state")), \
             mock.patch("builtins.open", side_effect=AssertionError("must not read state")):
            path = default_config_path()
        self.assertEqual(path.parent.parent, frontend / ".cache" / "nuke-time")
        self.assertEqual(path.name, "config.json")
        self.assertTrue(path.is_absolute())


class ImageCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_late_download_does_not_refill_cleared_thumbnail_cache(self):
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt6.QtGui import QImage, QPixmap
        from PyQt6.QtNetwork import QNetworkReply
        from image_loader import ImageLoader

        loader = ImageLoader()
        image = QImage(16, 16, QImage.Format.Format_RGB888)
        image.fill(0xFF0000)
        payload = QByteArray()
        buffer = QBuffer(payload)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        buffer.close()
        callback = mock.Mock()
        loader._cache_set("old", QPixmap.fromImage(image))
        loader._in_flight["late"] = [callback]
        reply = mock.Mock()
        reply.error.return_value = QNetworkReply.NetworkError.NoError
        reply.readAll.return_value = payload
        generation = loader._generation
        loader.clear_cache()
        loader._on_reply("late", reply, generation)
        self.assertFalse(loader._cache)
        callback.assert_called_once()
        self.assertFalse(callback.call_args.args[0].isNull())
        loader.deleteLater()

    def test_default_proxy_cache_uses_frontend_cache_root(self):
        from exr_preview_proxy import EXRProxyLoader

        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(app_cache, "CACHE_ROOT", Path(directory) / ".cache"):
            loader = EXRProxyLoader()
            self.assertEqual(loader.directory, Path(directory) / ".cache" / "exr-previews-v1")
            self.assertTrue(loader.directory.is_dir())
            loader.deleteLater()
