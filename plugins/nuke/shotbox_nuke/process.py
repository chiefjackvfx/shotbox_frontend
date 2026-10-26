"""Owned asynchronous child processes; no inference or Qt imports at startup."""
from pathlib import Path
import os
import re

from .protocol import EventParser


def child_environment(attempt, inherited=None):
    environment = dict(os.environ if inherited is None else inherited)
    for name in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                 "QML2_IMPORT_PATH", "QML_IMPORT_PATH"):
        environment.pop(name, None)
    # Preserve driver/system/license entries; remove only identifiable Nuke entries.
    for name in ("PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        if name in environment:
            environment[name] = os.pathsep.join(
                entry for entry in environment[name].split(os.pathsep)
                if not re.search(r"(?:^|[/\\])Nuke\d[^/\\]*", entry, re.IGNORECASE))
    if "LD_PRELOAD" in environment:
        environment["LD_PRELOAD"] = " ".join(
            entry for entry in re.split(r"[:\s]+", environment["LD_PRELOAD"])
            if not re.search(r"(?:^|[/\\])Nuke\d[^/\\]*", entry, re.IGNORECASE))
    environment.update(TMPDIR=str(attempt), TEMP=str(attempt), TMP=str(attempt))
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
                       SHOTBOX_JOB_DIR=str(Path(attempt) / "backend_jobs"))
    return environment


class ChildProcess:
    """Keep one QProcess alive until every stream is drained and exit checked."""

    def __init__(self, python, application, attempt, arguments, terminal, progress, done):
        from .qt_compat import qt_core
        QtCore = qt_core()
        self.process = QtCore.QProcess()
        self.parser = EventParser(expected_terminal=terminal)
        self.attempt = Path(attempt)
        self.done = done
        self.progress = progress
        self.closed = False
        self.failure = None
        self.stdout = (self.attempt / "events.jsonl").open("ab")
        try:
            self.stderr = (self.attempt / "stderr.log").open("ab")
        except OSError:
            self.stdout.close()
            raise
        environment = QtCore.QProcessEnvironment()
        for key, value in child_environment(attempt).items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(application))
        self.process.setProgram(str(python))
        self.process.setArguments(["-u", "-X", "utf8", *map(str, arguments)])
        self.process.readyReadStandardOutput.connect(self._stdout)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._error)

    def start(self):
        self.process.start()

    def _stdout(self):
        if self.closed:
            return
        chunk = bytes(self.process.readAllStandardOutput())
        try:
            self.stdout.write(chunk)
            self.stdout.flush()
            if self.failure is None:
                for event in self.parser.feed(chunk):
                    self.progress(event)
        except Exception as exc:
            self.failure = self.failure or str(exc)
            self.process.kill()

    def _stderr(self):
        if self.closed:
            return
        chunk = bytes(self.process.readAllStandardError())
        try:
            self.stderr.write(chunk)
            self.stderr.flush()
        except OSError as exc:
            self.failure = self.failure or str(exc)
            self.process.kill()

    def _error(self, error):
        from .qt_compat import qt_core
        if error == qt_core().QProcess.ProcessError.FailedToStart:
            self.failure = self.process.errorString()
            self._finished(-1, None)

    def _finished(self, code, status):
        if self.closed:
            return
        self._stdout()
        self._stderr()
        self.closed = True
        for stream in (self.stdout, self.stderr):
            try:
                stream.close()
            except OSError as exc:
                self.failure = self.failure or str(exc)
        from .qt_compat import qt_core
        try:
            try:
                if self.failure:
                    raise RuntimeError(self.failure)
                terminal = self.parser.finish(code, normal_exit=status == qt_core().QProcess.ExitStatus.NormalExit)
            except Exception as exc:
                self.done(None, str(exc), code)
            else:
                self.done(terminal, None, code)
        finally:
            self.process.deleteLater()

    def force_stop(self):
        from .qt_compat import qt_core
        self.process.terminate()
        qt_core().QTimer.singleShot(2000, self._kill_if_running)

    def _kill_if_running(self):
        from .qt_compat import qt_core
        if not self.closed and self.process.state() != qt_core().QProcess.ProcessState.NotRunning:
            self.process.kill()

    def reap(self):
        """Bounded shutdown only; ordinary generation never blocks the UI."""
        if not self.closed:
            self.process.terminate()
            if not self.process.waitForFinished(1000):
                self.process.kill()
                self.process.waitForFinished(2000)
