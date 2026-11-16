"""Automatic Nuke Time provisioning; pure Python, no import-time I/O or Qt.

Call ensure_setup from one serialized worker. The trusted injected request
contract is (url, body_bytes, timeout_seconds) -> (status, headers, bytes).
It MUST enforce verified TLS, no proxies/redirects, and finite timeouts like
our default implementation. The timeout is socket inactivity, not a DNS or
whole-response deadline. Results never include credentials or exception text.
"""
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import re
import uuid
import time
from datetime import datetime

from plugins.nuke.nuke_time_calc import managed_setup, setup_defaults, storage
from plugins.nuke.nuke_time_calc.outbox import Outbox, _sync_directory
from plugins.nuke.nuke_time_calc.transport import _unique_object, _validate_origin


def _post(url, body, timeout, *, ca_file=None):
    import urllib.request
    import urllib.error
    from plugins.nuke.nuke_time_calc.transport import _NoRedirect, _tls_context
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
        urllib.request.HTTPSHandler(context=_tls_context(ca_file)))
    req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        response = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.code, dict(response.headers), response.read(65537)


def _expiry(value):
    if not isinstance(value, str):
        raise ValueError('invalid_expiry')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('invalid_expiry')
    return parsed.timestamp()


def _private_read(path):
    return storage.private_read(path)


def _private_directory(path):
    return storage.private_directory(path)


def ensure_setup(*, server_url, artist_id, users=None, config_path=None,
                 nuke_dir=None, request=None, clock=time.time):
    if not storage.supported_platform():
        return dict(status='error', code='unsupported_platform')
    try:
        return _ensure_setup(server_url=server_url, artist_id=artist_id, users=users,
                            config_path=config_path, nuke_dir=nuke_dir, request=request, clock=clock)
    except Exception:
        # No exception text or metadata is returned, including from injected I/O.
        return dict(status='error', code='private_state_error')


def _ensure_setup(*, server_url, artist_id, users, config_path, nuke_dir, request, clock):
    path = Path(config_path if config_path is not None else managed_setup.default_config_path())
    proposed = setup_defaults.suggest(server_url=server_url, artist_id=artist_id, users=users)
    managed_setup._config_path(path)
    # Validate the per-user startup destination before credentials or enrollment.
    managed_setup._menu_path(nuke_dir)
    if storage.filesystem_path(path.parent).exists():
        _private_directory(path.parent)
    if storage.filesystem_path(path).exists():
        _private_read(path)
    previous = managed_setup.load_config(path)
    same = (previous.get('server'), previous.get('artist_id')) == (proposed.get('server'), proposed.get('artist_id'))
    if not same and previous['enabled']:
        managed_setup.save_config(dict(previous, enabled=False), path)
    if 'artist_id' not in proposed:
        return dict(status='waiting', code='linked_artist_required', config_path=str(path))
    try:
        _validate_origin(proposed.get('server'), allow_lan_http=proposed.get('allow_lan_http', False))
    except (ValueError, TypeError):
        return dict(status='error', code='invalid_origin', config_path=str(path))
    if same:
        proposed['mappings'] = previous['mappings']
    _private_directory(path.parent)
    binding = hashlib.sha256(json.dumps([proposed['server'], proposed['artist_id']]).encode()).hexdigest()
    root = path.parent / 'bindings' / binding
    _private_directory(root.parent)
    existed = storage.filesystem_path(root).exists()
    if not existed and previous.get('token_file') == str(root / 'credential.token'):
        raise ValueError('missing_current_binding')
    _private_directory(root)
    metadata_path = root / 'provision.json'
    if existed:
        metadata = json.loads(_private_read(metadata_path), object_pairs_hook=_unique_object)
        if (not isinstance(metadata, dict) or set(metadata) - {'server', 'artist_id', 'device_id', 'token', 'expires_at'}
                or metadata['server'] != proposed['server']
                or type(metadata['artist_id']) is not int or metadata['artist_id'] != proposed['artist_id']
                or str(uuid.UUID(metadata['device_id'])) != metadata['device_id']
                or not re.fullmatch(r'[0-9a-f]{32}\.[A-Za-z0-9_-]{43}', metadata['token'])
                or _private_read(root / 'credential.token') != (metadata['token'] + '\n').encode()):
            raise ValueError('invalid_provision_metadata')
    else:
        metadata = dict(server=proposed['server'], artist_id=proposed['artist_id'],
                        device_id=str(uuid.uuid4()), token=secrets.token_hex(16) + '.' + secrets.token_urlsafe(32))
        managed_setup._atomic_write(metadata_path, json.dumps(metadata).encode())
        managed_setup._atomic_write(root / 'credential.token', (metadata['token'] + '\n').encode())
        _sync_directory(root)
    device_id, token = metadata['device_id'], metadata['token']
    config = dict(proposed, device_id=device_id, token_file=str(root / 'credential.token'), outbox_dir=str(root / 'outbox'))
    Outbox(config['outbox_dir'], server=config['server'], artist_id=config['artist_id'], device_id=device_id)
    payload = dict(artist_id=config['artist_id'], device_id=device_id,
                   label=(socket.gethostname() + '/' + getpass.getuser())[:128], token=token)
    expires = _expiry(metadata['expires_at']) if 'expires_at' in metadata else 0
    now = clock()
    if expires <= now and previous['enabled'] and same:
        managed_setup.save_config(dict(previous, enabled=False), path)
    if expires - now <= 14 * 86400:
        try:
            transport = request or (lambda url, body, timeout: _post(url, body, timeout, ca_file=config.get('ca_file')))
            status, headers, raw = transport(config['server'] + '/api/time/devices/auto-setup', json.dumps(payload).encode(), 5.0)
        except Exception:
            return dict(status='retry', code='network_error', config_path=str(path))
        if type(status) is not int:
            return dict(status='error', code='invalid_ack', config_path=str(path))
        if 300 <= status < 400:
            return dict(status='error', code='redirect_rejected', config_path=str(path))
        if status == 429 or status >= 500:
            return dict(status='retry', code='server_unavailable', config_path=str(path))
        if status != 200:
            return dict(status='error', code='enrollment_refused', config_path=str(path))
        try:
            if not isinstance(raw, bytes) or len(raw) > 65536:
                raise ValueError()
            data = json.loads(raw, object_pairs_hook=_unique_object)
            if (status != 200 or set(data) != {'artist_id', 'device_id', 'status', 'expires_at'}
                    or type(data['artist_id']) is not int or data['artist_id'] != config['artist_id']
                    or data['device_id'] != device_id or data['status'] != 'ready'
                    or not isinstance(data['expires_at'], str)):
                raise ValueError()
            if _expiry(data['expires_at']) <= now:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, AttributeError):
            return dict(status='error', code='invalid_ack', config_path=str(path))
        metadata['expires_at'] = data['expires_at']
        managed_setup._atomic_write(metadata_path, json.dumps(metadata).encode())
    managed_setup.save_config(config, path)
    managed_setup.install(nuke_dir=nuke_dir, config_path=path)
    return dict(status='ready', code='ready', config_path=str(path), artist_id=config['artist_id'], device_id=device_id)
