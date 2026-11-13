"""Background automatic setup. No artist-facing setup or timer controls."""
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, QTimer, pyqtSignal


def _ensure_setup(**context):
    from nuke_time_auto_setup import ensure_setup
    return ensure_setup(**context)


class _Signals(QObject):
    finished = pyqtSignal(dict)


class _Task(QRunnable):
    def __init__(self, setup, context):
        super().__init__()
        self.signals = _Signals()
        self.setup = setup
        self.context = context

    def run(self):
        try:
            result = self.setup(**self.context)
        except Exception:
            result = {'status': 'retry', 'code': 'setup_failed'}
        self.signals.finished.emit(result)


class AutomaticNukeTimeSetup(QObject):
    status_changed = pyqtSignal(dict)

    def __init__(self, parent=None, *, setup=None):
        super().__init__(parent)
        self.setup = setup or _ensure_setup
        self.context = None
        self.closed = False
        self.task = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._launch)

    def set_context(self, *, server_url, artist_id):
        context = dict(server_url=server_url, artist_id=artist_id)
        if self.closed or context == self.context:
            return
        self.context = context
        self.timer.start(0)

    def _launch(self):
        if self.closed or self.context is None or self.task is not None:
            return
        self.task = _Task(self.setup, dict(self.context))
        self.task.signals.finished.connect(self._finished)
        QThreadPool.globalInstance().start(self.task)

    def _finished(self, result):
        task = self.task
        self.task = None
        if self.closed or task is None:
            return
        completed_context = task.context
        if completed_context != self.context:
            self.timer.start(0)
            return
        self.status_changed.emit(result)
        self.timer.start(12 * 60 * 60 * 1000 if result.get('status') == 'ready' else 30000)

    def close(self):
        self.closed = True
        self.timer.stop()
