import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt6.QtCore import QSettings
from artist_session import ArtistSettings
from settings import SettingsManager


class ArtistSessionTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        self.station = QSettings(str(directory / "station.ini"), QSettings.Format.IniFormat)
        self.legacy = SettingsManager(str(directory / "legacy.yaml"))
        self.legacy.set("server_url", "http://studio")
        self.legacy.set("django_username", 7)
        self.legacy.set("theme_file", "christmas_2026.qss")
        self.legacy.set("review_ocio.file", "/studio/config.ocio")
        self.fetch = self.enterContext(patch("artist_session.requests.get"))
        self.write = self.enterContext(patch("artist_session.requests.put"))
        self.fetch.return_value.json.return_value = {"revision": None, "settings": {}}
        self.write.return_value.json.return_value = {"revision": "first"}

    def open(self, artist_id):
        return ArtistSettings("http://studio", {"id": artist_id, "username": f"artist{artist_id}"}, self.station, self.legacy)

    def test_first_profile_imports_only_matching_artist_and_portable_preferences(self):
        manager = self.open(7)
        data = self.write.call_args.kwargs["json"]["settings"]
        self.assertEqual(data["theme_file"], "christmas_2026.qss")
        self.assertNotIn("file", data["review_ocio"])
        self.assertNotIn("server_url", data)
        self.assertEqual(manager.get("review_ocio.file"), "/studio/config.ocio")
        self.assertEqual(self.open(8).get("theme_file"), "dark_v01.qss")

    def test_existing_profile_and_stale_write(self):
        self.fetch.return_value.json.return_value = {"revision": "remote", "settings": {"startup_tab": 3}}
        manager = self.open(7)
        self.assertEqual(manager.get("startup_tab"), 3)
        self.assertEqual(manager.get("theme_file"), "dark_v01.qss")
        self.write.assert_not_called()
        self.write.return_value = Mock(status_code=409)
        manager.set("startup_tab", 4, save=False)
        self.assertFalse(manager.save())
        self.assertEqual(self.write.call_args.kwargs["json"]["revision"], "remote")
        self.assertEqual(manager.revision, "remote")
