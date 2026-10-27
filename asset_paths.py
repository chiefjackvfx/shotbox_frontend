"""Lowercase asset folders for new projects, with legacy title-case lookup."""
from pathlib import Path


_LEGACY_NAMES = {
    'job_assets': 'Job_Assets',
    'timeline_assets': 'Timeline_Assets',
    'shot_assets': 'Shot_Assets',
}


def asset_path(parent, directory, *relative_parts) -> Path:
    """Find an existing asset path, otherwise return its lowercase destination.

    Prefer lowercase when both spellings contain the requested path. Reuse a
    title-case folder/file when it already exists, without moving any assets.
    This function only resolves paths; callers decide whether to create them.
    """
    canonical = directory.lower()
    candidates = [Path(parent) / canonical, Path(parent) / _LEGACY_NAMES[canonical]]
    if relative_parts:
        for candidate in candidates:
            child = candidate.joinpath(*relative_parts)
            if child.exists():
                return child
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.joinpath(*relative_parts)
    return candidates[0].joinpath(*relative_parts)
