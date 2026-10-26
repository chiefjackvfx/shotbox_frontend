"""One headless Nuke process per preview batch, with a fresh comp per job."""
from __future__ import annotations

import json
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import sys
import tempfile
from threading import Thread
import traceback

import app_cache

RESULT_PREFIX = "SHOTBOX_PREVIEW_RESULT "


def run_worker(task_factory):
    """Run inside Nuke; only JSON task arguments cross the process boundary."""
    task = task_factory()
    nuke = task.setup_nuke()
    print("SHOTBOX_PREVIEW_WORKER_READY", flush=True)
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            request_id = request["id"]
            # Reset nodes, ranges, callbacks and slate values between inputs.
            nuke.scriptClear(ignoreUnsavedChanges=True)
            success = task.run(**request["arguments"])
            result = {"id": request_id, "success": bool(success), "error": ""}
        except Exception as exc:
            traceback.print_exc(file=sys.stdout)
            result = {"id": request_id, "success": False, "error": str(exc)}
        # Start a new line even if a codec left a partial progress message.
        print("\n" + RESULT_PREFIX + json.dumps(result), flush=True)


class PreviewBatchWorker:
    def __init__(self, executable, headless_script):
        self.executable = str(executable)
        self.headless_script = str(headless_script)
        self.process = None
        self._reader = None
        self._lines = Queue()
        self._temporary = None
        self._lease = None
        self._request_id = 0
        self.launches = 0
        threads = min(16, max(1, (os.cpu_count() or 8) // 2))
        self.command = [self.executable, "-m", str(threads), "--topdown", "-t", self.headless_script, "preview_worker"]

    def _start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.close()
        self._lease = app_cache.temporary_cache_lease()
        self._lease.__enter__()
        try:
            self._temporary = tempfile.TemporaryDirectory(
                prefix="preview-worker-", dir=app_cache.cache_directory("temp"),
            )
            directory = Path(self._temporary.name)
            environment = dict(os.environ)
            for variable in ("NUKE_TEMP_DIR", "NUKE_DISK_CACHE", "TMPDIR", "XDG_CACHE_HOME"):
                target = directory / variable.lower()
                target.mkdir()
                environment[variable] = str(target)
            self.process = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=environment,
                start_new_session=os.name != "nt",
            )
            self.launches += 1
            self._lines = Queue()
            self._reader = Thread(target=self._read_lines, args=(self.process.stdout, self._lines), daemon=True)
            self._reader.start()
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _read_lines(stream, lines):
        try:
            for line in stream:
                lines.put(line.rstrip("\r\n"))
        except (OSError, ValueError) as exc:
            lines.put(f"Nuke preview output error: {exc}")
        finally:
            lines.put(None)

    def _stop(self):
        process = self.process
        if process is None or process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            process.wait(timeout=5)

    @app_cache.temporary_cache_task
    def render(self, arguments, *, on_output=None, check_cancelled=None):
        if check_cancelled and check_cancelled():
            return False, "Operation cancelled by user"
        self._start()
        self._request_id += 1
        request_id = self._request_id
        try:
            if on_output:
                on_output(f"PREVIEW_WORKER_PID: {self.process.pid}")
            self.process.stdin.write(json.dumps({"id": request_id, "arguments": arguments}) + "\n")
            self.process.stdin.flush()
            while True:
                if check_cancelled and check_cancelled():
                    self._stop()
                    return False, "Operation cancelled by user"
                try:
                    line = self._lines.get(timeout=.05)
                except Empty:
                    continue
                if line is None:
                    self._stop()
                    return False, f"Nuke preview worker closed its output (code {self.process.poll()})"
                if line.startswith(RESULT_PREFIX):
                    result = json.loads(line[len(RESULT_PREFIX):])
                    if result.get("id") != request_id:
                        raise RuntimeError("Nuke preview worker returned a different task ID")
                    success = result.get("success") is True
                    output = Path(arguments["output_path"])
                    if success and (not output.is_file() or not output.stat().st_size):
                        return False, "Nuke preview worker produced no MP4 output"
                    return success, str(result.get("error", ""))
                if line and on_output:
                    on_output(line)
        except BaseException:
            self._stop()
            raise

    def close(self):
        process = self.process
        try:
            if process is not None:
                if process.stdin is not None:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self._stop()
                if self._reader is not None:
                    self._reader.join(timeout=2)
                if process.stdout is not None:
                    process.stdout.close()
        finally:
            self.process = None
            self._reader = None
            try:
                if self._temporary is not None:
                    self._temporary.cleanup()
            finally:
                self._temporary = None
                if self._lease is not None:
                    self._lease.__exit__(None, None, None)
                    self._lease = None
