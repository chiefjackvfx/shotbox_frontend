from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nuke_lock_identity as identity


class NukeLockIdentityStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.store = identity.NukeLockIdentityStore(self.directory)
        self.server = "http://server:8000/api/"

    def test_restart_preserves_linked_identity_and_os_alias(self):
        self.store.resolve(self.server, "os-user@desk", "artist@desk")
        restarted = identity.NukeLockIdentityStore(self.directory)
        self.assertEqual(
            restarted.resolve(self.server, "os-user@desk"),
            ("artist@desk", {"artist@desk", "os-user@desk"}),
        )

    def test_other_logins_and_machines_do_not_inherit_aliases(self):
        self.store.resolve(self.server, "os-user@desk", "artist@desk")
        for local_id in ("other-user@desk", "os-user@other-desk"):
            with self.subTest(local_id=local_id):
                self.assertEqual(self.store.resolve(self.server, local_id), (local_id, {local_id}))

    def test_equivalent_server_urls_share_identity(self):
        self.store.resolve("HTTP://SERVER:8000/api/", "os-user@desk", "artist@desk")
        self.assertEqual(self.store.resolve(self.server.rstrip("/"), "os-user@desk")[0], "artist@desk")

    def test_corrupt_or_invalid_record_can_be_replaced(self):
        self.store.resolve(self.server, "os-user@desk", "artist@desk")
        path, = self.directory.glob("*.json")
        for invalid in ("{broken", "[]", '{"owner": null, "aliases": null}'):
            with self.subTest(invalid=invalid):
                path.write_text(invalid, encoding="utf-8")
                self.assertEqual(self.store.resolve(self.server, "os-user@desk"), ("os-user@desk", {"os-user@desk"}))

    def test_failed_save_keeps_session_identity_and_retries_persistence(self):
        with mock.patch.object(identity.os, "replace", side_effect=OSError("read-only")), \
                mock.patch("builtins.print"):
            self.store.resolve(self.server, "os-user@desk", "artist@desk")
            self.assertEqual(self.store.resolve(self.server, "os-user@desk")[0], "artist@desk")
        self.store.resolve(self.server, "os-user@desk")
        restarted = identity.NukeLockIdentityStore(self.directory)
        self.assertEqual(restarted.resolve(self.server, "os-user@desk")[0], "artist@desk")
        self.assertEqual(len(list(self.directory.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
