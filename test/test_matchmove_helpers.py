from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest
from unittest import mock

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import matchmove_helpers
import settings


class DummySettingsManager:
    def __init__(self, values: dict[str, object] | None = None):
        self._values = dict(values or {})

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class MatchmoveHelpersTests(unittest.TestCase):
    def test_resolve_3de_executable_prefers_settings_path(self):
        configured_path = os.path.abspath("/tmp/fake_3de/run_3DE4")
        original_manager = settings._settings_manager
        settings._settings_manager = DummySettingsManager({"threede_exe_path": configured_path})
        self.addCleanup(setattr, settings, "_settings_manager", original_manager)

        with mock.patch.object(matchmove_helpers, "THREEDE_CMD", ""), \
            mock.patch.object(matchmove_helpers, "POSIX_THREEDE_CANDIDATES", ("/opt/fallback/run_3DE4",)), \
            mock.patch.object(matchmove_helpers.os.path, "isfile", side_effect=lambda path: path == configured_path), \
            mock.patch.object(matchmove_helpers.shutil, "which", return_value=None):
            resolved = matchmove_helpers.resolve_3de_executable()

        self.assertEqual(resolved, configured_path)


if __name__ == "__main__":
    unittest.main()
