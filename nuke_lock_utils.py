from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import getpass
import hashlib
import re
import socket
from typing import Any


EMPTY_LOCK_VALUES = (None, "", "None", "null", "0", 0)
STALE_THRESHOLD_SECONDS = 180
_ALLOWED_SYSTEM_CHARS = re.compile(r"[^a-z0-9@._-]+")
_WHITESPACE = re.compile(r"\s+")
_REPEATED_SEPARATORS = re.compile(r"[-._]{2,}")


def is_empty_lock_value(value: Any) -> bool:
    return value in EMPTY_LOCK_VALUES


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def normalize_system_id(value: Any) -> str:
    text = _coerce_text(value).strip().lower()
    if not text:
        return "unknown"

    text = _WHITESPACE.sub("-", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _ALLOWED_SYSTEM_CHARS.sub("-", text)
    text = _REPEATED_SEPARATORS.sub(lambda match: match.group(0)[0], text)
    text = text.strip("-._")

    if not text:
        return "unknown"
    if len(text) <= 32:
        return text

    prefix = text[:24].rstrip("-._")
    prefix = prefix or text[:24]
    digest = hashlib.sha1(text.encode("ascii")).hexdigest()[:7]
    return f"{prefix}-{digest}"


def display_owner_name(value: Any) -> str:
    normalized = normalize_system_id(value)
    if not normalized:
        return "unknown"
    return normalized.split("@", 1)[0] or "unknown"


def local_machine_name() -> str:
    return socket.gethostname() or "unknown-machine"


def local_system_label() -> str:
    user = getpass.getuser() or "unknown"
    return f"{user}@{local_machine_name()}"


def local_system_id() -> str:
    return normalize_system_id(local_system_label())


def parse_lock_timestamp(value: Any) -> datetime | None:
    text = _coerce_text(value).strip()
    if not text:
        return None

    try:
        return datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_lock_timestamp(value: Any) -> str | None:
    parsed = parse_lock_timestamp(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class FrontendLockInfo:
    owner: str
    timestamp: str | None = None
    raw_value: str | None = None

    @property
    def normalized_owner(self) -> str:
        return normalize_system_id(self.owner)

    @property
    def display_owner(self) -> str:
        return display_owner_name(self.owner)

    def normalized_timestamp(self) -> str | None:
        return normalize_lock_timestamp(self.timestamp)

    def matches_system(self, system_id: Any) -> bool:
        return self.normalized_owner == normalize_system_id(system_id)

    def is_stale(self, stale_threshold_seconds: int = STALE_THRESHOLD_SECONDS) -> bool:
        parsed = parse_lock_timestamp(self.timestamp)
        if parsed is None:
            return True
        age_seconds = (datetime.now(timezone.utc) - parsed).total_seconds()
        return age_seconds > stale_threshold_seconds


def parse_lock_info(value: Any) -> FrontendLockInfo | None:
    if is_empty_lock_value(value):
        return None

    raw = _coerce_text(value).strip()
    if not raw:
        return None

    owner, separator, timestamp = raw.partition("|")
    timestamp_value = timestamp if separator else None
    return FrontendLockInfo(owner=owner.strip(), timestamp=timestamp_value, raw_value=raw)
