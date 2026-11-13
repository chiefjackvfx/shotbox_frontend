"""Pure path selection for durable state inside the installed Shotbox cache.

No Qt, frontend imports, directory creation or state migration. The reserved
nuke-time category must never be treated as disposable by Clear Cache.
"""
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import socket


def default_config_path():
    """Return this OS user's workstation-scoped configuration path, without I/O."""
    frontend = Path(os.path.abspath(__file__)).parents[3]
    user = getpass.getuser()
    host = socket.gethostname()
    label = re.sub(r'[^a-zA-Z0-9_-]+', '-', user + '-' + host).strip('-')[:80] or 'state'
    # Hash unsanitized, framed identities so punctuation/truncation and ambiguous
    # user-host boundaries cannot make unrelated queues share credentials.
    identity = [os.name, user, host,
                os.getuid() if hasattr(os, 'getuid') else os.environ.get('USERDOMAIN', '')]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode('ascii')).hexdigest()[:32]
    namespace = label + '-' + digest
    return frontend / '.cache' / 'nuke-time' / namespace / 'config.json'
