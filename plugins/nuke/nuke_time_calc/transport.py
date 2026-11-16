"""Opt-in schema-2 uploader. Importing never starts delivery.

The server is a clean origin (no path/userinfo/query). HTTPS certificate and
hostname validation are mandatory. Explicit private-LAN HTTP accepts literal
RFC1918 IPv4 only; the separate development switch accepts literal loopback.
Neither HTTP exception accepts DNS aliases. Redirects and ambient proxies are
disabled. Token files contain the operator-issued selector.secret line, are
opened privately without following the final symlink, and are reread per attempt
so rotation within the same declared binding does not reattribute records.
Default POSTs send expected artist/device headers from the outbox binding; the
backend rejects credentials belonging to another binding before accepting work.

One daemon thread per Uploader; no HTTP in put/callbacks. Socket operations have
finite inactivity timeouts, but platform DNS or a trickling peer can outlive a
stop deadline. stop returns False while an in-flight thread still exists; it
never waits past its bounded join or pretends the thread was killed. Independent
processes may send the same immutable record concurrently: delivery is at least
once and backend keyed acknowledgements are the deduplication authority.

Transport injection signature: (url, body_bytes, token, timeout) ->
(status_int, header_mapping, response_bytes). Injected code is trusted and must
apply equivalent security/timeouts. clock is monotonic; wall_clock is Unix time
for HTTP-date Retry-After. Retry state is process-local (resets on restart), with
1..300-second exponential defaults, at least 60 seconds for permanent/config
errors, and Retry-After honored up to 24 hours. Status never includes response
bodies, credentials, batch data or raw exception text. No logging is performed.
"""
import http.client
import ipaddress
import json
import math
import os
import re
import time
import threading
from email.utils import parsedate_to_datetime
import ssl
import sqlite3

from .outbox import CorruptOutbox
from . import storage
import urllib.error
from urllib.parse import urlsplit
from pathlib import Path
import urllib.request


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate response key')
        result[key] = value
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TLSConfigurationError(ValueError):
    """Invalid explicit CA configuration; never fall back to another trust policy."""


def _tls_context(ca_file=None):
    context = ssl.create_default_context()
    if ca_file is not None:
        from .managed_setup import _safe_path
        try:
            path = _safe_path(ca_file)
            if not path.is_file():
                raise ValueError('CA must be a regular file')
            # Additional public roots extend, never replace, system trust.
            context.load_verify_locations(cafile=str(path))
        except (OSError, ValueError, TypeError):
            raise TLSConfigurationError('tls_configuration_error') from None
    return context


def _validate_origin(origin, *, allow_lan_http=False, allow_loopback_http=False):
    """Require TLS unless the exact literal address has an explicit exception."""
    if type(allow_lan_http) is not bool or type(allow_loopback_http) is not bool:
        raise ValueError('HTTP exceptions must be boolean')
    if (not isinstance(origin, str) or any(ord(c) <= 32 or c.isspace() for c in origin)
            or '\\' in origin or '?' in origin or '#' in origin):
        raise ValueError('server must be a clean origin')
    parsed = urlsplit(origin)
    if (not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('server must be a clean origin')
    port = parsed.port  # Validate before reading any credentials.
    if parsed.netloc.endswith(':') or port == 0:
        raise ValueError('server port is invalid')
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    lan = isinstance(address, ipaddress.IPv4Address) and any(
        address in ipaddress.IPv4Network(network)
        for network in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    loopback = address is not None and address.is_loopback
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and (
            (allow_lan_http and lan) or (allow_loopback_http and loopback))):
        raise ValueError('HTTPS required unless literal HTTP address is explicitly allowed')
    return parsed


class Uploader:
    """Explicit worker; start/stop return booleans, run_once delivers at most one."""

    def __init__(self, outbox, *, token_file, ca_file=None, allow_loopback_http=False,
                 allow_lan_http=False, timeout=5.0,
                 transport=None, clock=time.monotonic, wall_clock=time.time,
                 retry_base=1.0, retry_max=300.0, poll_interval=1.0):
        for value in (timeout, retry_base, retry_max, poll_interval):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('finite positive worker options required')
        if timeout > 300 or retry_max > 86400 or poll_interval > 300 or retry_base > retry_max:
            raise ValueError('worker options exceed supported bounds')
        self.outbox = outbox
        self.token_file = Path(token_file)
        self.ca_file = ca_file
        if ca_file is not None:
            _tls_context(ca_file)  # Fail before reading credentials or starting a worker.
        origin = outbox.binding[0]
        _validate_origin(origin, allow_lan_http=allow_lan_http,
                         allow_loopback_http=allow_loopback_http)
        self.url = origin.rstrip('/') + '/api/time/batches'
        self.timeout = timeout
        self.transport = transport or self._post_bound
        self.clock, self.wall_clock = clock, wall_clock
        self.retry_base, self.retry_max = retry_base, retry_max
        self._failures = 0
        self._next_attempt = 0.0
        self._status = dict(code='idle', failures=0, next_attempt=0.0)
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread = None
        self._lifecycle = threading.Lock()
        self._run_lock = threading.Lock()

    def _post_bound(self, url, body, token, timeout):
        return self._post(url, body, token, timeout, binding=self.outbox.binding, ca_file=self.ca_file)

    @staticmethod
    def _post(url, body, token, timeout, *, binding=None, ca_file=None):
        headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
        if binding is not None:
            headers.update({'X-Shotbox-Time-Artist': binding[1],
                            'X-Shotbox-Time-Device': binding[2]})
        request = urllib.request.Request(url, data=body, headers=headers, method='POST')
        # No ambient proxy/cookie/auth handlers, no redirects, verified TLS only.
        context = _tls_context(ca_file)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect(),
                                            urllib.request.HTTPSHandler(context=context))
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            return response.code, dict(response.headers), response.read(1048577)

    def _load_token(self):
        raw = storage.private_read(self.token_file, max_bytes=4096)
        token = raw.decode('ascii').rstrip('\r\n')
        if not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', token):
            raise ValueError('credential_error')
        return token

    def status(self):
        return dict(self._status, running=self._thread is not None and self._thread.is_alive())

    def start(self):
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._work, name='ShotboxTimeUploader', daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout=0.0):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 300:
            raise ValueError('stop timeout must be finite and within 0..300 seconds')
        self._stop.set()
        thread = self._thread
        if thread is None:
            return True
        if thread is not threading.current_thread():
            thread.join(max(0, timeout))
        return not thread.is_alive()

    def _work(self):
        while not self._stop.is_set():
            try:
                delivered = self.run_once()
            except Exception:
                # Never expose exception text: it may contain credentials/payloads.
                delivered = self._failure('internal_error', permanent=True)
            if not delivered:
                self._stop.wait(self.poll_interval)

    def run_once(self):
        if not self._run_lock.acquire(blocking=False):
            return False
        try:
            return self._run_once()
        except (sqlite3.DatabaseError, CorruptOutbox, OSError, ValueError):
            return self._failure('outbox_error', permanent=True)
        finally:
            self._run_lock.release()

    def _failure(self, code, headers=None, permanent=False):
        self._failures += 1
        delay = min(self.retry_max, self.retry_base * 2 ** min(self._failures - 1, 30))
        if permanent:
            delay = max(delay, 60.0)
        for key, value in (headers or {}).items():
            if key.lower() == 'retry-after':
                try:
                    seconds = int(value)
                except (ValueError, TypeError):
                    try:
                        seconds = parsedate_to_datetime(value).timestamp() - self.wall_clock()
                    except (ValueError, TypeError, OverflowError):
                        continue
                delay = max(delay, min(max(seconds, 0), 86400))
        self._next_attempt = self.clock() + delay
        self._status = dict(code=code, failures=self._failures, next_attempt=self._next_attempt)
        return False

    def _run_once(self):
        if self.clock() < self._next_attempt:
            return False
        pending = self.outbox.pending()
        if not pending:
            return False
        batch = pending[0]
        try:
            token = self._load_token()
        except (OSError, ValueError, UnicodeError):
            return self._failure('credential_error', permanent=True)
        body = json.dumps(dict(schema_version=2, batches=[batch]), separators=(',', ':')).encode()
        try:
            status, headers, raw = self.transport(self.url, body, token, self.timeout)
        except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
            reason = getattr(exc, 'reason', exc)
            if isinstance(reason, ssl.SSLError):
                return self._failure('tls_error', permanent=True)
            return self._failure('network_error')
        if 300 <= status < 400:
            return self._failure('redirect_rejected', permanent=True)
        if status == 400 and len(raw) <= 1048576:
            try:
                rejected = json.loads(raw, object_pairs_hook=_unique_object)
                unavailable = rejected.get('code') == 'invalid_shot'
            except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError):
                unavailable = False
            if unavailable:
                # Each request contains exactly one batch. Preserve its payload
                # for inspection while allowing other shots to keep uploading.
                self.outbox.quarantine(batch['session_id'], batch['sequence'])
                self._failures = 0
                self._next_attempt = 0.0
                self._status = dict(code='invalid_shot_quarantined', failures=0, next_attempt=0.0)
                return True
        if status != 200:
            code = {400: 'validation_error', 401: 'authentication_error',
                    403: 'permission_error', 409: 'batch_conflict', 413: 'body_too_large',
                    429: 'throttled'}.get(status, 'server_error' if status >= 500 else 'http_error')
            if status == 503:
                try:
                    backend_code = json.loads(raw).get('code')
                    if backend_code == 'time_disabled':
                        code = 'disabled'
                    elif backend_code == 'database_busy':
                        code = 'database_busy'
                except (ValueError, AttributeError, UnicodeError):
                    pass
            return self._failure(code, headers, permanent=status in (400, 401, 403, 409, 413) or code == 'disabled')
        try:
            if len(raw) > 1048576:
                raise ValueError()
            data = json.loads(raw, object_pairs_hook=_unique_object)
            results = data['results']
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError()
            ack = results[0]
            if (status != 200 or set(data) != {'results'}
                    or set(ack) != {'session_id', 'sequence', 'status'}
                    or ack['session_id'] != batch['session_id']
                    or type(ack['sequence']) is not int or ack['sequence'] != batch['sequence']
                    or ack['status'] not in ('accepted', 'duplicate')):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            return self._failure('invalid_ack', permanent=True)
        self.outbox.acknowledge(batch['session_id'], batch['sequence'])
        self._failures = 0
        self._next_attempt = 0.0
        self._status = dict(code=ack['status'], failures=0, next_attempt=0.0)
        return True
