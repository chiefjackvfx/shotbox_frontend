"""Remember local Nuke lock owners independently of activity-user lookups."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit, urlunsplit

from nuke_lock_utils import normalize_system_id


def canonical_server_url(value: str) -> str:
    parts = urlsplit(value.rstrip("/"))
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))


def _state_directory() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return root / "ShotBox" / "nuke-lock-identities"


class NukeLockIdentityStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory
        self._mutex = threading.RLock()
        self._unsaved = {}

    def resolve(self, server_url: str, local_id: str, linked_owner: str | None = None):
        """Return the preferred owner and exact aliases for this server/login.

        A successful linked-user lookup can select a new preferred owner. A
        failed lookup keeps the saved owner, and never discards previous aliases.
        The OS identity also recognises locks created before persistence existed.
        """
        local_id = normalize_system_id(local_id)
        scope = canonical_server_url(server_url) + "\n" + local_id
        key = hashlib.sha256(scope.encode("utf-8")).hexdigest()
        path = (self.directory or _state_directory()) / f"{key}.json"
        with self._mutex:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(saved, dict):
                    saved = {}
            except (OSError, ValueError):
                saved = {}
            previous = self._unsaved.get(key, saved)
            preferred = previous.get("owner")
            if not isinstance(preferred, str) or not preferred.strip():
                preferred = local_id
            preferred = normalize_system_id(linked_owner or preferred)
            aliases = {local_id, preferred}
            for record in (saved, previous):
                values = record.get("aliases", [])
                if isinstance(values, list):
                    aliases.update(normalize_system_id(v) for v in values if isinstance(v, str) and v.strip())
            record = {"owner": preferred, "aliases": sorted(aliases)}
            if record != saved:
                temporary = None
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                        temporary = Path(stream.name)
                        json.dump(record, stream)
                    os.replace(temporary, path)
                except OSError as exc:
                    self._unsaved[key] = record
                    print(f"[ShotBox] Could not save Nuke lock identity: {exc}")
                else:
                    self._unsaved.pop(key, None)
                finally:
                    if temporary is not None:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
            return preferred, aliases
