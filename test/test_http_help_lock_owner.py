from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import http_help
from nuke_lock_identity import NukeLockIdentityStore


def _response(*, status_code=200, payload=None):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = payload if payload is not None else {}
    return response


class DjangoAPILockOwnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state = mock.patch.multiple(
            http_help.DjangoAPI,
            _default_base_url="http://old-server:8000/api/",
            _current_username=None, _cached_users=None,
            _system_id=None, _lock_owner_id=None,
            _lock_identity_store=NukeLockIdentityStore(Path(temporary.name)),
        )
        state.start()
        self.addCleanup(state.stop)

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

    def test_startup_helper_uses_saved_server_before_resolving_user(self):
        settings = mock.Mock()
        settings.get.side_effect = {"server_url": "http://saved-server:8000/", "django_username": 1}.get
        with mock.patch("settings.get_settings_manager", return_value=settings), \
            mock.patch.object(http_help.DjangoAPI, "get_users", return_value=[{"id": 1, "username": "jack"}]) as users:
            http_help.setup_activity_tracking_from_settings()
        users.assert_called_once()
        self.assertEqual(http_help.DjangoAPI._default_base_url, "http://saved-server:8000/api/")
        self.assertEqual(http_help.DjangoAPI.get_current_username(), "jack")

    def test_existing_lock_can_be_renewed_after_selected_user_changes(self):
        api = http_help.DjangoAPI()
        api._request = mock.Mock(return_value=_response())
        http_help.DjangoAPI.set_current_username("replacement")

        api.update_shot_lock(12, owner_id="original@desk")

        self.assertEqual(
            api._request.call_args.kwargs["headers"]["X-ShotBox-System"], "original@desk",
        )

    def test_restart_with_failed_user_lookup_keeps_saved_owner_and_aliases(self):
        cls = http_help.DjangoAPI
        with mock.patch.object(http_help, "local_machine_name", return_value="cerebro"), \
                mock.patch.object(http_help, "local_system_id", return_value="cerebro-local@cerebro"):
            cls.set_current_username("kieran")
            # New store and cleared class state simulate a new process.
            cls._lock_identity_store = NukeLockIdentityStore(cls._lock_identity_store.directory)
            cls._current_username = None
            cls._lock_owner_id = None
            with mock.patch.object(cls, "get_users", side_effect=http_help.requests.ConnectionError("offline")):
                cls.set_current_user_by_id(4)
            self.assertEqual(cls.get_lock_owner_id(), "kieran@cerebro")
            self.assertEqual(cls().get_lock_owner_ids(), {"kieran@cerebro", "cerebro-local@cerebro"})

    def test_selected_user_change_preserves_previous_owner_for_recovery_after_restart(self):
        cls = http_help.DjangoAPI
        with mock.patch.object(http_help, "local_machine_name", return_value="desk"), \
                mock.patch.object(http_help, "local_system_id", return_value="os-user@desk"):
            cls.set_current_username("original")
            cls.set_current_username("replacement")
            cls._lock_identity_store = NukeLockIdentityStore(cls._lock_identity_store.directory)
            cls._current_username = None
            cls._lock_owner_id = None
            self.assertEqual(cls.get_lock_owner_id(), "replacement@desk")
            self.assertEqual(cls().get_lock_owner_ids(), {"original@desk", "replacement@desk", "os-user@desk"})

    def test_server_switch_does_not_reuse_previous_server_aliases(self):
        cls = http_help.DjangoAPI
        with mock.patch.object(http_help, "local_machine_name", return_value="desk"), \
                mock.patch.object(http_help, "local_system_id", return_value="os-user@desk"):
            cls.set_current_username("original")
            original_api = cls()
            cls.set_default_base_url("http://new-server:8000/api/")
            cls.set_current_username("replacement")
            self.assertNotIn("original@desk", cls().get_lock_owner_ids())
            self.assertNotIn("replacement@desk", original_api.get_lock_owner_ids())
            cls.set_default_base_url(original_api.base_url)
            self.assertEqual(cls.get_lock_owner_id(), "original@desk")


if __name__ == "__main__":
    unittest.main()
