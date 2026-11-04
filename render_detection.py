"""Session-only production render discovery and asynchronous delivery.

Existing outputs form a baseline. No file ownership or reporting-user identity
is used for attribution; the server snapshots its current Nuke lock.
"""
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import threading
import time

from nuke_lock_identity import canonical_server_url

log = logging.getLogger(__name__)


def scan_outputs(directory):
    directory = Path(directory)
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return {}
    outputs = {}
    for entry in entries:
        mov = re.fullmatch(r'.+_v(\d+)\.mov', entry.name, re.IGNORECASE)
        exr = re.fullmatch(r'.+_v(\d+)', entry.name, re.IGNORECASE)
        if mov and entry.is_file() and entry.stat().st_size > 0:
            outputs[entry.name] = ('mov', int(mov[1]))
        elif exr and entry.is_dir():
            pattern = re.compile(re.escape(entry.name) + r'[._-]?(\d+)\.exr', re.IGNORECASE)
            if any(pattern.fullmatch(frame.name) and frame.is_file() and frame.stat().st_size > 0
                   for frame in entry.iterdir()):
                outputs[entry.name] = ('exr', int(exr[1]))
    return outputs


class RenderReporter:
    def __init__(self, api_factory=None, *, start_worker=True):
        if api_factory is None:
            from http_help import DjangoAPI
            api_factory = DjangoAPI
        self.api_factory = api_factory
        self.seen = {}
        self.pending = {}
        self._scans = {}
        self._condition = threading.Condition()
        self._stopped = False
        self._thread = None
        if start_worker:
            self._thread = threading.Thread(target=self._run, name='render-activity', daemon=True)
            self._thread.start()

    def request_scan(self, server, shot_id, directory):
        key = (canonical_server_url(server), int(shot_id), str(directory))
        with self._condition:
            self._scans[key] = None
            self._condition.notify()

    def scan(self, server, shot_id, directory):
        try:
            outputs = scan_outputs(directory)
        except OSError as exc:
            log.warning('Cannot scan render outputs for shot %s: %s', shot_id, exc)
            return
        scope = (server, shot_id, str(directory))
        if scope not in self.seen:
            self.seen[scope] = set(outputs)
            return
        observed = datetime.now(timezone.utc).isoformat()
        for path in outputs.keys() - self.seen[scope]:
            kind, version = outputs[path]
            key = (server, shot_id, path)
            self.pending.setdefault(key, {
                'payload': {'output_path': path, 'output_type': kind,
                            'version': version, 'observed_at': observed},
                'retry_at': 0,
            })
        self.seen[scope].update(outputs)

    def deliver(self):
        for key, event in list(self.pending.items()):
            if time.monotonic() < event['retry_at']:
                continue
            server, shot_id, _ = key
            try:
                api = self.api_factory(base_url=server.rstrip('/') + '/')
                api.report_render_detected(shot_id, **event['payload'])
            except Exception as exc:
                status = getattr(exc, 'status_code', None)
                if status is not None and 400 <= status < 500 and status not in (408, 429):
                    log.error('Render activity rejected for shot %s: %s', shot_id, exc)
                    del self.pending[key]
                else:
                    log.warning('Render activity delivery failed for shot %s; retrying: %s', shot_id, exc)
                    event['retry_at'] = time.monotonic() + 30
            else:
                del self.pending[key]

    def _run(self):
        while True:
            with self._condition:
                if self._stopped:
                    return
                scans = list(self._scans)
                self._scans.clear()
            for scope in scans:
                try:
                    self.scan(*scope)
                except Exception:
                    log.exception('Unexpected render scan failure')
            self.deliver()
            with self._condition:
                if not self._scans and not self._stopped:
                    self._condition.wait(timeout=1)

    def stop(self):
        with self._condition:
            self._stopped = True
            self._condition.notify()


_reporter = None


def report_loaded_shot(server, shot_id, directory):
    global _reporter
    if _reporter is None:
        _reporter = RenderReporter()
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_reporter.stop)
    _reporter.request_scan(server, shot_id, directory)
