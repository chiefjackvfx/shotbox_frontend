from typing import Optional

APP_VERSION = "0.1.0"
UPDATE_BRANCH = "main"
UPDATE_REMOTE = "origin"


def format_version_display(version: str, short_commit: Optional[str]) -> str:
    token = (short_commit or "unknown").strip()[:7] or "unknown"
    return f"{version} ({token})"
