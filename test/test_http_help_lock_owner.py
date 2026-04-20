from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import http_help


def _response(*, status_code=200, payload=None):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    return response


class DjangoAPILockOwnerTests(unittest.TestCase):
    def tearDown(self):
        http_help.DjangoAPI._current_username = None
        http_help.DjangoAPI._cached_users = None
        http_help.DjangoAPI._system_id = None
        http_help.DjangoAPI._lock_owner_id = None

    def test_get_lock_owner_id_uses_linked_username_and_machine(self):
        with mock.patch.object(http_help, "local_machine_name", return_value="rogue"):
            http_help.DjangoAPI.set_current_username("chief")

        self.assertEqual(http_help.DjangoAPI.get_lock_owner_id(), "chief@rogue")

    def test_get_lock_owner_id_falls_back_to_local_system_identity(self):
        with mock.patch.object(http_help, "local_system_id", return_value="artist@desk-01"):
            self.assertEqual(http_help.DjangoAPI.get_lock_owner_id(), "artist@desk-01")

    def test_set_current_user_by_id_missing_user_clears_stale_lock_owner(self):
        http_help.DjangoAPI._cached_users = [{"id": 4, "username": "chief"}]
        with mock.patch.object(http_help, "local_system_id", return_value="artist@desk-01"), \
            mock.patch.object(http_help.DjangoAPI, "get_users", return_value=[]):
            http_help.DjangoAPI.set_current_user_by_id(999)

        self.assertIsNone(http_help.DjangoAPI.get_current_username())
        self.assertEqual(http_help.DjangoAPI.get_lock_owner_id(), "artist@desk-01")

    def test_update_shot_lock_sends_linked_user_lock_owner_header(self):
        api = http_help.DjangoAPI()
        api._request = mock.Mock(return_value=_response(payload={"nuke_in_use": "chief@rogue|20260420T153400Z"}))

        with mock.patch.object(http_help, "local_machine_name", return_value="rogue"):
            http_help.DjangoAPI.set_current_username("chief")
            api.update_shot_lock(12, release=False, force=False)

        api._request.assert_called_once()
        headers = api._request.call_args.kwargs["headers"]
        self.assertEqual(headers["X-ShotBox-System"], "chief@rogue")


if __name__ == "__main__":
    unittest.main()
