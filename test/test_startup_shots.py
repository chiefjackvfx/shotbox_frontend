"""Exercise picker-time shot loading and the dashboard's first fetch."""
import sys
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from artist_session import StartupShots
from page_nukedash import ApiWorker


class StartupShotsTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.server = "http://studio"
        self.artist = {"id": 8, "username": "avery"}
        self.worker = ApiWorker()
        self.worker.api = MagicMock(base_url=self.server + "/api/")
        self.worker.api.get_current_username.return_value = "avery"
        self.worker.api.getAPI.return_value = [{"id": 2}]
        self.results = []
        self.worker.data_ready.connect(self.results.append)

    def test_picker_stays_responsive_and_first_dashboard_fetch_reuses_jobs(self):
        entered, release = Event(), Event()
        response = MagicMock()
        response.__enter__.return_value.json.return_value = [{"id": 1, "timelines": []}]

        def get(url, *, headers, timeout):
            self.assertEqual((url, headers), (self.worker.api.base_url, {"X-ShotBox-User": "avery"}))
            entered.set()
            release.wait(2)
            return response

        with patch("artist_session.requests.get", side_effect=get):
            self.worker.startup_shots = StartupShots(self.server, self.artist)
            self.assertTrue(entered.wait(1))
            QTimer.singleShot(0, release.set)
            self.app.processEvents()
            self.assertTrue(release.is_set())
            self.worker.fetch()
        self.assertEqual(self.results[0], [{"id": 1, "timelines": []}])
        self.worker.api.getAPI.assert_not_called()
        self.worker.fetch()
        self.worker.api.getAPI.assert_called_once()

    def test_switched_artist_or_failed_request_uses_normal_fetch(self):
        response = MagicMock()
        response.__enter__.return_value.json.return_value = [{"id": 1}]
        with patch("artist_session.requests.get", return_value=response):
            self.worker.startup_shots = StartupShots(self.server, self.artist)
            self.assertTrue(self.worker.startup_shots.ready.wait(1))
            self.worker.api.get_current_username.return_value = "other"
            self.worker.fetch()
        with patch("artist_session.requests.get", side_effect=requests.Timeout()):
            self.worker.startup_shots = StartupShots(self.server, self.artist)
            self.worker.api.get_current_username.return_value = "avery"
            self.worker.fetch()
        self.assertEqual(self.results, [[{"id": 2}], [{"id": 2}]])
