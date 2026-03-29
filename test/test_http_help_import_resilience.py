from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest
from unittest import mock

import requests

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import http_help


def _response(*, status_code=200, payload=None, text=""):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.text = text
    return response


class DjangoAPIImportResilienceTests(unittest.TestCase):
    def test_import_mode_client_uses_custom_timeout_and_skip_activity_header(self):
        api = http_help.DjangoAPI(
            timeout=30,
            retry_count=3,
            retry_backoff=0,
            skip_activity_logging=True,
        )
        api._s = mock.Mock()
        api._s.request.return_value = _response(payload=[])

        api.get_jobs()

        api._s.request.assert_called_once()
        _method, _url = api._s.request.call_args.args[:2]
        kwargs = api._s.request.call_args.kwargs
        self.assertEqual(_method, "GET")
        self.assertEqual(_url, f"{api.base_url}jobs")
        self.assertEqual(kwargs["timeout"], 30.0)
        self.assertEqual(kwargs["headers"]["X-ShotBox-Skip-Activity"], "1")

    def test_get_request_timeout_retries_and_raises_clear_error(self):
        api = http_help.DjangoAPI(timeout=30, retry_count=3, retry_backoff=0)
        api._s = mock.Mock()
        api._s.request.side_effect = requests.Timeout("read timeout")

        with self.assertRaises(http_help.DjangoAPIError) as raised:
            api.get_recent_activity()

        self.assertEqual(api._s.request.call_count, 4)
        self.assertIn("get_recent_activity", str(raised.exception))
        self.assertIn("GET /api/activity/recent", str(raised.exception))
        self.assertIn("timed out after 30s", str(raised.exception))
        self.assertIn("attempt 4/4", str(raised.exception))

    def test_get_or_create_shot_reconciles_after_create_timeout(self):
        api = http_help.DjangoAPI(timeout=30, retry_count=3, retry_backoff=0)
        expected_shot = {"id": 55, "title": "sho010", "base_path": "/tmp/sho010"}
        create_error = http_help.DjangoAPIError(
            operation="create_shot",
            method="POST",
            url="http://example.test/api/shots",
            timeout=30,
            attempt=1,
            total_attempts=1,
            detail="timed out after 30s",
            retryable=True,
        )

        with mock.patch.object(api, "get_shots_for_timeline", side_effect=[[], [expected_shot]]), \
            mock.patch.object(api, "create_shot", side_effect=create_error) as create_shot, \
            mock.patch.object(api, "_sleep_before_retry") as sleep_retry:
            shot, created = api.get_or_create_shot(12, "sho010", "/tmp/sho010")

        self.assertEqual(shot, expected_shot)
        self.assertFalse(created)
        create_shot.assert_called_once_with(12, "sho010", "/tmp/sho010")
        sleep_retry.assert_not_called()


if __name__ == "__main__":
    unittest.main()
