"""Local diagnostics only: estimates, no API, no authenticated Shotbox mapping.

The managed developer menu.py explicitly opts in by calling register(). Records
are durable diagnostic state, NOT disposable application cache. Import is inert.
"""
import json
import os
from pathlib import Path
import uuid

from nuke_time_calc.nuke_adapter import install


IDENTITY = 'Jack diagnostic local-test (not authenticated)'


def _qt_modules():
    # Use Nuke's shipped Qt binding, never the frontend's PyQt.
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide6 import QtCore, QtGui, QtWidgets
    return QtCore, QtGui, QtWidgets


class LocalTest:
    def __init__(self, nuke, data_dir, installer=None):
        self.nuke = nuke
        self.data_dir = Path(data_dir) if data_dir is not None else (
            Path.home() / '.local/state/shotbox/nuke_time_calc')
        self.installer = installer or install
        self.adapter = None
        self.last_error = None
        self.auto_start = True
        self.cleanup_failed = False

    def _context(self):
        path = str(self.nuke.root().name() or '')
        return dict(project_id='local-test', shot_id=path or 'unsaved-sandbox',
                    script_path=path, shotbox_mapping=False,
                    attribution='diagnostic root name only; NOT Shotbox mapping')

    def _directory(self):
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.data_dir.is_symlink():
            raise OSError('diagnostic directory must not be a symlink')
        self.data_dir.chmod(0o700)

    def emit(self, record):
        self._directory()
        session = str(uuid.UUID(record['session_id']))
        path = self.data_dir / (session + '.jsonl')
        line = (json.dumps(record, allow_nan=False) + '\n').encode('utf-8')
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                     getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'ab') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        directory_fd = os.open(self.data_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _error(self, exc):
        self.last_error = type(exc).__name__
        self.nuke.tprint('shotbox local diagnostics: ' + self.last_error)

    def status(self):
        state = self.adapter.status() if self.adapter is not None else {}
        text = ('shotbox / nuke_time_calc: local diagnostics; activity estimates; no API\n'
                + IDENTITY + '; NOT Shotbox mapping\n'
                + 'installed={installed}, running={running}, pending={pending}, '
                  'blocked={blocked}, unsealed={seconds:.3f}s, error={error}\n'.format(
                      installed=state.get('installed', False),
                      running=state.get('running', False),
                      pending=state.get('pending_count', 0),
                      blocked=state.get('blocked', False),
                      seconds=state.get('unsealed_comp_seconds', 0),
                      error=self.last_error or state.get('last_error') or 'none')
                + 'Durable diagnostics: ' + str(self.data_dir))
        self.nuke.tprint(text)
        return text

    def enable(self):
        if self.cleanup_failed:
            # Callback removal was incomplete: never install a competing tracker.
            return self.adapter
        try:
            if self.adapter is not None:
                old = self.adapter
                collector = old.collector
                if old.installed and collector.running and not collector.blocked:
                    return old
                collector.retry_pending()
                if collector.pending_count:
                    raise BufferError('pending diagnostics retained; repair storage then enable')
                if collector.blocked or collector.comp_seconds or collector.excluded_gap_seconds:
                    collector.flush('local_test_recovery')
                if collector.pending_count or collector.blocked:
                    raise BufferError('held diagnostics retained')
                if old.installed:
                    if old.refresh_context():
                        self.last_error = None
                    return old
            core, _, widgets = _qt_modules()
            app = widgets.QApplication.instance()
            if app is None:
                raise RuntimeError('GUI application is not ready; retry Enable local test')
            self.adapter = self.installer(enabled=True, identity=IDENTITY,
                                          context_resolver=self._context, emit=self.emit,
                                          nuke_module=self.nuke, qt_core=core, app=app)
            self.last_error = None
        except Exception as exc:
            self._error(exc)
        return self.adapter

    def disable(self):
        self.auto_start = False
        if self.adapter is not None:
            try:
                self.adapter.uninstall()
            except Exception as exc:
                self.cleanup_failed = True
                self._error(exc)
                try:
                    self.adapter.collector.stop("local_test_disable_error")
                except Exception as stop_error:
                    self._error(stop_error)
        # Keep the adapter even after uninstall: it owns any unflushed intervals.
        return self.adapter

    def open_folder(self):
        try:
            self._directory()
            core, gui, _ = _qt_modules()
            if not gui.QDesktopServices.openUrl(core.QUrl.fromLocalFile(str(self.data_dir.resolve()))):
                raise OSError('could not open diagnostics folder')
            return True
        except Exception as exc:
            self._error(exc)
            return False


def register(nuke_module=None, data_dir=None, *, installer=None):
    """Register once per GUI host; defer automatic local-only enable to Qt."""
    if nuke_module is None:
        import nuke as nuke_module
    if not getattr(nuke_module, 'GUI', False):
        return None
    existing = getattr(nuke_module, '_shotbox_local_time_test', None)
    if existing is not None:
        return existing
    controller = LocalTest(nuke_module, data_dir, installer)
    try:
        main = nuke_module.menu('Nuke')
        top = main.findItem('shotbox') or main.addMenu('shotbox')
        menu = top.findItem('nuke_time_calc') or top.addMenu('nuke_time_calc')
        for name, callback in (('Status', controller.status),
                               ('Enable local test', controller.enable),
                               ('Disable local test', controller.disable),
                               ('Open diagnostics folder', controller.open_folder)):
            if menu.findItem(name) is None:
                menu.addCommand(name, callback)
    except Exception as exc:
        controller._error(exc)
        return None
    nuke_module._shotbox_local_time_test = controller
    try:
        core, _, _ = _qt_modules()
        core.QTimer.singleShot(0, lambda: controller.enable() if controller.auto_start else None)
    except Exception as exc:
        controller._error(exc)
    return controller
