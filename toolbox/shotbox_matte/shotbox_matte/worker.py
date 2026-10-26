"""Qt worker transport shared by the full Matte window and compact host dialog."""
from __future__ import annotations

import codecs
import json
from pathlib import Path
import sys
import tempfile

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, pyqtSignal


class MatteWorker(QObject):
    stdout_line = pyqtSignal(str)
    stderr_line = pyqtSignal(str)
    raw_output = pyqtSignal(bytes)
    finished = pyqtSignal(int, object)

    def __init__(self, parent, options, runtime=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.directory = tempfile.TemporaryDirectory(
            prefix="shotbox-matte-", dir=str(runtime.temporary_directory) if runtime else None)
        self.path = Path(self.directory.name) / "job.json"
        try:
            self.path.write_text(json.dumps(options), encoding="utf-8")
        except BaseException:
            self.directory.cleanup()
            raise
        self._done = False
        self._cancelled = False
        self._buffers = {"stdout": "", "stderr": ""}
        self._decoders = {key: codecs.getincrementaldecoder("utf-8")(errors="replace")
                          for key in self._buffers}
        self.process.setProgram(str(runtime.worker_python) if runtime else sys.executable)
        if runtime is not None:
            environment = QProcessEnvironment.systemEnvironment()
            for key in ("PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                        "QML_IMPORT_PATH", "QML2_IMPORT_PATH"):
                environment.remove(key)
            environment.insert("PYTHONUTF8", "1")
            for key in ("TMPDIR", "TEMP", "TMP"):
                environment.insert(key, str(runtime.temporary_directory))
            environment.insert("SHOTBOX_JOB_DIR", str(runtime.temporary_directory / "jobs"))
            environment.insert("SHOTBOX_TEMP_DIR", str(runtime.temporary_directory))
            environment.insert("SHOTBOX_PORTABLE_DIR", str(runtime.application_root))
            self.process.setProcessEnvironment(environment)
            self.process.setWorkingDirectory(str(runtime.application_root))
        self.process.setArguments(["-u", "-X", "utf8", "-m", "shotbox_matte", "--worker", str(self.path)])
        self.process.readyReadStandardOutput.connect(lambda: self._read("stdout"))
        self.process.readyReadStandardError.connect(lambda: self._read("stderr"))
        self.process.finished.connect(self._finish)
        self.process.errorOccurred.connect(self._error)

    def start(self):
        self.process.start()

    def _read(self, stream, final=False):
        raw = bytes(self.process.readAllStandardOutput() if stream == "stdout"
                    else self.process.readAllStandardError())
        if raw:
            self.raw_output.emit(raw)
        self._buffers[stream] += self._decoders[stream].decode(raw, final=final)
        signal = self.stdout_line if stream == "stdout" else self.stderr_line
        while "\n" in self._buffers[stream]:
            line, self._buffers[stream] = self._buffers[stream].split("\n", 1)
            signal.emit(line)
        if final and self._buffers[stream]:
            signal.emit(self._buffers[stream])
            self._buffers[stream] = ""

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart and not self._done:
            self.stdout_line.emit(json.dumps({"type": "error", "message": self.process.errorString()}))
            self._finish(-1, QProcess.ExitStatus.CrashExit)

    def _finish(self, code, status):
        if self._done:
            return
        self._done = True
        self._read("stdout", final=True)
        self._read("stderr", final=True)
        self.finished.emit(code, status)

    def cancel(self):
        if self._done or self._cancelled:
            return
        self._cancelled = True
        Path(str(self.path) + ".cancel").touch()
        QTimer.singleShot(10000, self._kill_if_running)

    def _kill_if_running(self):
        if not self._done and self.process.state() != QProcess.ProcessState.NotRunning:
            self.stderr_line.emit("Stopping the worker process to release GPU memory.")
            self.process.kill()

    def cleanup(self):
        self.directory.cleanup()
        self.deleteLater()
