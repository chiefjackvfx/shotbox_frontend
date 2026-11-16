"""Offline, explicit shot bindings. Importing this module has no side effects."""
import posixpath
import re
import os
from pathlib import Path

# Same mounted project-root aliases as the desktop; no drive/basename guessing.
ROOT_ALIASES = (
    ('Z:/PROJECTS', '/Volumes/projects/PROJECTS', '/mnt/projects/PROJECTS'),
    ('X:/Projects B', '/Volumes/Expansion_B/Projects B'),
)


def _mounted_aliases(aliases):
    """Include the UNC target of configured Windows project mounts.

    Resolve only the trusted project root, not individual shot/script paths:
    symlinks within a shot must still fail the real-path containment check.
    Do not persist machine-specific share names or infer shots from filenames.
    """
    yield from aliases
    if os.name == 'nt':
        for root in aliases:
            if re.match(r'^[A-Za-z]:/', root):
                try:
                    resolved = str(Path(root).resolve(strict=True)).replace('\\', '/')
                except (OSError, RuntimeError):
                    continue
                if resolved.startswith('//'):
                    yield resolved.rstrip('/')


def _path(value):
    if not isinstance(value, str) or not value or '\x00' in value:
        return None
    value = value.replace('\\', '/')
    if not (value.startswith('/') or re.match(r'^[A-Za-z]:/', value)):
        return None
    return posixpath.normpath(value)


def _key(value):
    path = _path(value)
    if path is None:
        return None
    for index, aliases in enumerate(ROOT_ALIASES):
        for root in _mounted_aliases(aliases):
            windows = os.name == 'nt' or ':' in root or root.startswith('//')
            candidate = path.casefold() if windows else path
            compare_root = root.casefold() if windows else root
            if candidate == compare_root or candidate.startswith(compare_root + '/'):
                suffix = path[len(root):]
                return '@root%d%s' % (index, suffix.casefold() if os.name == 'nt' else suffix)
    return path


class ContextResolver:
    """Resolve only registered shot folders; every call revalidates the script."""
    def __init__(self, mappings=()):
        self.mappings = []
        for item in mappings:
            if not isinstance(item, dict):
                raise ValueError('mapping must be an object')
            folder = _key(item.get('base_path'))
            if (folder is None or folder == '/' or re.match(r'^[A-Za-z]:/?$', folder)
                    or re.match(r'^@root\d+$', folder)
                    or any(type(item.get(name)) is not int or item[name] <= 0
                           for name in ('job_id', 'shot_id'))):
                raise ValueError('mapping needs positive integer IDs and an absolute shot folder')
            self.mappings.append((folder, item['job_id'], item['shot_id']))

    def resolve(self, script_path):
        key = _key(script_path)
        if key is None or not key.lower().endswith('.nk'):
            return None
        matches = {(job_id, shot_id) for folder, job_id, shot_id in self.mappings
                   if folder is not None and key.startswith(folder.rstrip('/') + '/')}
        if len(matches) != 1:
            return None
        job_id, shot_id = matches.pop()
        return dict(job_id=job_id, shot_id=shot_id, script_path=script_path)

    __call__ = resolve
