"""Opt-in local setup. No install, configuration write or network at import."""
import json
import os
from pathlib import Path
import tempfile


from .state_paths import default_config_path


def _safe_path(path):
    path = Path(path)
    if '\x00' in str(path):
        raise ValueError('NUL is not allowed in a local path')
    if not path.is_absolute() or any(part == '..' for part in path.parts):
        raise ValueError('expected an absolute, non-traversing local path')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('symlink setup targets are not allowed')
    return path


def _config_path(path):
    path = _safe_path(path if path is not None else default_config_path())
    if path.name != 'config.json':
        raise ValueError('time-plugin state target must be named config.json')
    return path


def _validate_config(config):
    from .transport import _validate_origin
    from uuid import UUID
    from .context import ContextResolver
    allowed = {'enabled', 'server', 'artist_id', 'device_id', 'token_file',
               'outbox_dir', 'mappings', 'ca_file', 'allow_lan_http'}
    if not isinstance(config, dict) or set(config) - allowed:
        raise ValueError('unsupported configuration fields; tokens belong in a separate file')
    result = dict(enabled=False, mappings=[], allow_lan_http=False)
    result.update(config)
    if type(result['enabled']) is not bool or not isinstance(result['mappings'], list):
        raise ValueError('enabled must be boolean and mappings must be a list')
    if type(result['allow_lan_http']) is not bool:
        raise ValueError('allow_lan_http must be boolean')
    ContextResolver(result['mappings'])
    required = {'server', 'artist_id', 'device_id', 'token_file', 'outbox_dir'}
    if result['enabled'] and not required.issubset(result):
        raise ValueError('enabled configuration is incomplete')
    if 'server' in result:
        _validate_origin(result['server'], allow_lan_http=result['allow_lan_http'])
    if 'artist_id' in result and (type(result['artist_id']) is not int or result['artist_id'] <= 0):
        raise ValueError('artist_id must be a positive integer')
    if 'device_id' in result:
        value = result['device_id']
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError('device_id must be a canonical UUID')
    for name in ('token_file', 'outbox_dir', 'ca_file'):
        if name in result:
            _safe_path(result[name])
    return result


def _atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
    try:
        os.chmod(name, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_config(path=None):
    """Read private state; a missing file is explicitly disabled."""
    path = _config_path(path)
    if not path.exists():
        return dict(enabled=False, mappings=[], allow_lan_http=False)
    if os.name != 'nt' and path.stat().st_mode & 0o077:
        raise ValueError('configuration permissions must be private')
    return _validate_config(json.loads(path.read_text(encoding='utf-8')))


def save_config(config, path=None):
    """Atomically persist non-secret configuration outside disposable caches."""
    config = _validate_config(config)
    _atomic_write(_config_path(path), json.dumps(config, sort_keys=True, indent=2).encode('utf-8') + b'\n')


_BEGIN = b'# >>> SHOTBOX NUKE TIME CALC MANAGED v1 >>>'
_END = b'# <<< SHOTBOX NUKE TIME CALC MANAGED v1 <<<'


def _menu_path(nuke_dir):
    return _safe_path((Path(nuke_dir) if nuke_dir is not None else Path.home() / '.nuke') / 'menu.py')


def _without_block(data):
    start_marker = b'\n' + _BEGIN + b'\n'
    end_marker = _END + b'\n'
    start = data.find(start_marker)
    end = data.find(end_marker)
    if b'SHOTBOX NUKE TIME CALC MANAGED' not in data:
        return data
    if (data.count(b'SHOTBOX NUKE TIME CALC MANAGED') != 2
            or data.count(start_marker) != 1 or data.count(end_marker) != 1
            or end < start + len(start_marker)
            or data[end - 1:end] != b'\n'):
        raise ValueError('malformed, unsupported or duplicate time-plugin startup block')
    return data[:start] + data[end + len(end_marker):]


def _registration(config_path):
    # Path is fixed to this distribution, never supplied as executable content.
    plugin_root = str(Path(__file__).resolve().parent.parent)
    body = ('import sys as _shotbox_time_sys\n'
            'if {root} not in _shotbox_time_sys.path:\n'
            '    _shotbox_time_sys.path.insert(0, {root})\n'
            'import nuke_time_calc.runtime as _shotbox_time_runtime\n'
            '_shotbox_time_runtime.register(config_path={config})\n').format(
                root=ascii(plugin_root), config=ascii(str(config_path)))
    return b'\n' + _BEGIN + b'\n' + body.encode('ascii') + _END + b'\n'


def record_launch_context(jobs, shot, script_path, *, source_server=None,
                          source_artist_id=None, config_path=None):
    """Opt-in handoff from existing API snapshot, before normal OS launch.

    No network, credentials or launch arguments. False means no binding was
    approved. The caller must not substitute its active/last-selected job.
    source_server/source_artist_id must be captured from the API snapshot's
    desktop context, never copied from the saved reporting configuration.
    """
    from .context import ContextResolver, _key
    config = load_config(config_path)
    if not config['enabled'] or not isinstance(shot, dict):
        return False
    # Provenance is independent of the saved reporting identity. Reject before
    # even invalidating overlapping mappings from another server/account.
    if (type(source_artist_id) is not int or source_artist_id <= 0
            or source_artist_id != config.get('artist_id')):
        return False
    from .transport import _validate_origin
    try:
        # Strip only the actual desktop API root, never arbitrary URL paths.
        origin = source_server
        if isinstance(origin, str) and origin.endswith('/api/'):
            origin = origin[:-5]
        source = _validate_origin(origin, allow_lan_http=config['allow_lan_http'])
        saved = _validate_origin(config['server'], allow_lan_http=config['allow_lan_http'])
        if (source.scheme, source.netloc) != (saved.scheme, saved.netloc):
            return False
    except (ValueError, TypeError):
        return False
    shot_id = shot.get('id')
    if type(shot_id) is not int or shot_id <= 0:
        return False
    # Validate containment before either approving or invalidating a binding.
    try:
        resolver = ContextResolver([dict(job_id=1, shot_id=shot_id,
                                         base_path=shot.get('base_path'))])
    except ValueError:
        return False
    if resolver(script_path) is None or not Path(script_path).is_file():
        return False
    # A local symlink must not turn a lexical in-folder path into another shot.
    if resolver(str(Path(script_path).resolve())) is None:
        return False
    folder = _key(shot['base_path'])

    def overlaps(base_path):
        other = _key(base_path)
        return other is not None and (other == folder
                or other.startswith(folder.rstrip('/') + '/')
                or folder.startswith(other.rstrip('/') + '/'))

    matches = []
    conflicting = False
    for job in jobs:
        for timeline in job.get('timelines', []):
            for known in timeline.get('shots', []):
                if (type(known.get('id')) is int and known['id'] == shot_id
                        and _key(known.get('base_path')) == folder
                        and (shot.get('timeline') is None or shot['timeline'] == timeline.get('id'))):
                    matches.append(dict(job_id=job.get('id'), shot_id=shot_id,
                                        base_path=shot['base_path']))
                elif overlaps(known.get('base_path')):
                    conflicting = True
    if not matches:
        return False
    approved = len(matches) == 1 and not conflicting
    try:
        ContextResolver(matches)
    except ValueError:
        approved = False
    # Replace overlaps only on unique proof. Otherwise persist their removal so
    # a normal launch/direct reopen cannot continue reporting the stale shot.
    retained = [item for item in config['mappings'] if not overlaps(item['base_path'])]
    if approved:
        retained = [item for item in retained if item['shot_id'] != shot_id] + matches
    if retained != config['mappings']:
        config['mappings'] = retained
        save_config(config, config_path)
    return approved


def install(*, nuke_dir=None, config_path=None):
    """Explicitly install only a managed menu.py block; never enables reporting."""
    menu = _menu_path(nuke_dir)
    config_path = _config_path(config_path)
    old = menu.read_bytes() if menu.exists() else b''
    new = _without_block(old) + _registration(config_path)
    config = load_config(config_path)
    if not config_path.exists():
        save_config(config, config_path)
    if old != new:
        _atomic_write(menu, new, mode=(menu.stat().st_mode & 0o777) if menu.exists() else 0o600)
    return menu


def uninstall(*, nuke_dir=None, config_path=None, remove_config=False):
    """Remove only our block; retain tokens/outbox and mappings by default."""
    menu = _menu_path(nuke_dir)
    config_path = _config_path(config_path)
    if menu.exists():
        old = menu.read_bytes()
        new = _without_block(old)
        if new != old:
            _atomic_write(menu, new, mode=menu.stat().st_mode & 0o777)
    if remove_config and config_path.exists():
        config_path.unlink()
    return menu

