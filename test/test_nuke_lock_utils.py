from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from pyqt_frontend.nuke_lock_utils import (
    STALE_THRESHOLD_SECONDS,
    display_owner_name,
    normalize_system_id,
    parse_lock_info,
)


class NukeLockUtilsTests(unittest.TestCase):
    def test_parse_lock_info_empty(self):
        self.assertIsNone(parse_lock_info(None))
        self.assertIsNone(parse_lock_info("None"))
        self.assertIsNone(parse_lock_info(""))

    def test_normalize_system_id(self):
        self.assertEqual(normalize_system_id("Artist One@Desk 01"), "artist-one@desk-01")
        self.assertEqual(normalize_system_id("   "), "unknown")

    def test_display_owner_name_user_only(self):
        self.assertEqual(display_owner_name("artist-one@desk-01"), "artist-one")
        self.assertEqual(display_owner_name("artist-one"), "artist-one")
        self.assertEqual(display_owner_name(None), "unknown")

    def test_lock_match_and_recent_stale_check(self):
        timestamp = (
            datetime.now(timezone.utc) - timedelta(seconds=60)
        ).strftime("%Y%m%dT%H%M%SZ")
        info = parse_lock_info(f"artist@ws01|{timestamp}")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertEqual(info.display_owner, "artist")
        self.assertTrue(info.matches_system("artist@ws01"))
        self.assertFalse(info.is_stale(stale_threshold_seconds=STALE_THRESHOLD_SECONDS))

    def test_old_lock_is_stale(self):
        info = parse_lock_info("artist@ws01|20000101T000000Z")
        self.assertIsNotNone(info)
        assert info is not None
        self.assertTrue(info.is_stale(stale_threshold_seconds=STALE_THRESHOLD_SECONDS))


if __name__ == "__main__":
    unittest.main()
