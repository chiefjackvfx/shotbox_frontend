"""Non-secret, offline suggestions; never create state or inspect credentials."""
from urllib.parse import urlsplit
from pathlib import Path
from ipaddress import IPv4Address, IPv4Network
from . import managed_setup
import json


DEFAULTS_PATH = Path(__file__).resolve().parents[3] / 'nuke_time_defaults.json'


def deployment_defaults(path=None):
    """Read only an origin, explicit private-LAN policy and optional public CA."""
    path = Path(path if path is not None else DEFAULTS_PATH)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(data, dict) or 'server' not in data
                or set(data) - {'server', 'ca_file', 'allow_lan_http'}):
            return {}
        server = data['server']
        if 'allow_lan_http' in data and type(data['allow_lan_http']) is not bool:
            return {}
        origin = https_origin(server) or (lan_origin(server) if data.get('allow_lan_http') is True else None)
        if not origin or server not in (origin, origin + '/'):
            return {}
        result: dict = {'server': origin}
        if data.get('allow_lan_http') is True:
            result['allow_lan_http'] = True
        if 'ca_file' in data:
            if not isinstance(data['ca_file'], str) or not data['ca_file']:
                return {}
            relative = Path(data['ca_file'])
            if relative.is_absolute() or '..' in relative.parts:
                return {}
            root = path.parent.resolve()
            ca = root / relative
            if (not ca.is_file() or not ca.resolve().is_relative_to(root)
                    or any(part.is_symlink() for part in (ca, *ca.parents))):
                return {}
            result['ca_file'] = str(ca)
        return result
    except (OSError, ValueError):
        return {}


def deployment_origin(path=None):
    """Return only the public origin for callers not using custom CA trust."""
    return deployment_defaults(path).get('server')


def suggest(*, server_url=None, artist_id=None, users=None, device_id=None,
            config_path=None, defaults_path=None):
    """Suggest enabled setup intent, without writes, token reads or activation."""
    result: dict = dict(enabled=True, mappings=[])
    deployment = deployment_defaults(defaults_path)
    server = https_origin(server_url)
    if not server and deployment.get('allow_lan_http') is True:
        current = lan_origin(server_url)
        if current and urlsplit(current).port == urlsplit(deployment['server']).port:
            server = current
    if server_url is None:
        server = deployment.get('server')
    if server:
        result['server'] = server
        if lan_origin(server):
            result['allow_lan_http'] = True
        if server == deployment.get('server') and deployment.get('ca_file'):
            result['ca_file'] = deployment['ca_file']
    artist = linked_artist(artist_id, users)
    if artist:
        result['artist_id'] = artist
    if device_id:
        managed_setup._validate_config({'device_id': device_id})
        result['device_id'] = device_id
    if artist and device_id:
        root = Path(config_path if config_path is not None else managed_setup.default_config_path()).parent
        result['token_file'] = str(root / 'credentials' / str(artist) / (device_id + '.token'))
        result['outbox_dir'] = str(root / 'outbox' / str(artist) / device_id)
    return result



def linked_artist(value, users=None):
    """Use only the explicit linked Django ID, checking an available snapshot."""
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or value <= 0:
        return None
    if users is not None and not any(
            isinstance(user, dict) and type(user.get('id')) is int
            and user['id'] == value and user.get('is_active', True) is True
            for user in users):
        return None
    return value



def lan_origin(value):
    """Accept only literal RFC1918 IPv4 HTTP API roots, with no DNS lookup."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ('', '/', '/api', '/api/')
                or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
            return None
        parsed.port
        address = IPv4Address(parsed.hostname)
        if not any(address in IPv4Network(network) for network in
                   ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
            return None
    except ValueError:
        return None
    return 'http://' + parsed.netloc


def https_origin(value):
    """Accept an HTTPS origin or the standard API root, never invent TLS."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ('', '/', '/api', '/api/')
                or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
            return None
        parsed.port
    except ValueError:
        return None
    return 'https://' + parsed.netloc
