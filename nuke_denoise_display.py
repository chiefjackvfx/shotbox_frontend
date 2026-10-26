"""Linux private-display automation for the qualified Neat Video 6.1.2 UI.

Never connect to the inherited DISPLAY: input is restricted to our Xvfb server.
"""
from __future__ import annotations

import os
from pathlib import Path
import selectors
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time


class DenoiseCancelled(RuntimeError):
    pass


def child_environment(directory, base=None):
    return dict(os.environ if base is None else base, SHOTBOX_DENOISE_SUPERVISOR_PID=str(os.getpid()),
                SHOTBOX_DENOISE_JOB_DIR=str(directory))


def child_command(command):
    return [sys.executable, str(Path(__file__).with_name("nuke_denoise_child.py")), *command]


def stop_recorded_children(directory, *, force=False):
    """Nonblocking crash cleanup; return whether any identified child is alive."""
    import json
    import psutil
    alive = False
    for path in Path(directory).glob("child-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            process = psutil.Process(record["pid"])
            if process.create_time() != record["created"] or process.status() == psutil.STATUS_ZOMBIE:
                continue
            if sys.platform == "win32":
                for child in reversed(process.children(recursive=True)):
                    try:
                        child.kill() if force else child.terminate()
                    except psutil.NoSuchProcess:
                        pass
                process.kill() if force else process.terminate()
                alive = True
                continue
            if os.getpgid(process.pid) != process.pid:
                continue
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            alive = True
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass
    return alive


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        import psutil
        try:
            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
            for child in reversed(children):
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            process.terminate()
            process.wait(timeout=5)
            _, alive = psutil.wait_procs(children, timeout=5)
            if alive:
                raise RuntimeError("Windows denoise children have not stopped.")
        except psutil.NoSuchProcess:
            process.wait(timeout=5)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except ProcessLookupError:
        pass


def discover_tools():
    if sys.platform == "win32":
        return {}
    if sys.platform != "linux":
        raise RuntimeError("Background denoise supports Windows and Linux.")
    result = {}
    for name, variable in (("Xvfb", "SHOTBOX_DENOISE_XVFB"), ("openbox", "SHOTBOX_DENOISE_OPENBOX")):
        value = os.environ.get(variable) or shutil.which(name)
        if not value or not os.path.isfile(value) or not os.access(value, os.X_OK):
            raise RuntimeError(f"Hidden denoise requires {name}. Install Xvfb and Openbox; see README → Denoise.")
        result[name] = value
    return result


class PrivateDisplay:
    def __init__(self, directory, cancelled, tools=None, notify=None):
        self.directory = Path(directory)
        self.cancelled = cancelled
        self.tools = tools or discover_tools()
        self.notify = notify or (lambda event: None)
        self.processes = []
        self.logs = []
        self.connection = None
        self.child = None
        self.number = None
        self.authority = self.directory / "Xauthority"
        self.clicked_auto = False
        self.ready_since = None
        self.applied = False
        self.templates = {}

    def environment(self, base):
        return dict(base, DISPLAY=self.number, XAUTHORITY=str(self.authority), QT_QPA_PLATFORM="xcb")

    def launch(self, command, environment, cwd, log):
        return subprocess.Popen(child_command(command), env=child_environment(self.directory, environment),
                                cwd=cwd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def _load_templates(self):
        import cv2
        for path in (Path(__file__).parent / "ui" / "denoise").glob("*.png"):
            self.templates[path.stem] = cv2.imread(str(path))
        if any(self.templates.get(key) is None for key in ("auto_profile", "build_profile", "adjust_ready", "apply")):
            raise RuntimeError("Denoise control images are missing or unreadable.")

    def _launch(self, command, name, **kwargs):
        log = (self.directory / name).open("wb")
        self.logs.append(log)
        environment = child_environment(self.directory, kwargs.pop("env", None))
        process = subprocess.Popen(child_command(command), env=environment, stderr=log, start_new_session=True, **kwargs)
        self.processes.append(process)
        return process

    def __enter__(self):
        try:
            from Xlib import display
            self._load_templates()
            cookie = os.urandom(16)

            def write_authority(number):
                fields = (socket.gethostname().encode(), number.encode(), b"MIT-MAGIC-COOKIE-1", cookie)
                content = struct.pack(">H", 256) + b"".join(struct.pack(">H", len(value)) + value for value in fields)
                with open(os.open(self.authority, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as stream:
                    stream.write(content)

            write_authority("")
            server = self._launch([self.tools["Xvfb"], "-displayfd", "1", "-screen", "0", "1600x1000x24",
                                   "-nolisten", "tcp", "-auth", str(self.authority)], "xvfb.log", stdout=subprocess.PIPE)
            # A unique display number is allocated by the server, never guessed.
            with selectors.DefaultSelector() as selector:
                selector.register(server.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 15
                while not selector.select(.1):
                    self._check()
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("The private Xvfb display could not start; see xvfb.log.")
                number = server.stdout.readline().decode("ascii").strip()
            if not number.isdigit():
                raise RuntimeError("Xvfb returned an invalid display number.")
            self.number = ":" + number
            self.notify({"stage": "debug", "source": "Display", "message": f"Private Xvfb ready on {self.number}; starting Openbox."})
            write_authority(number)
            old_auth = os.environ.get("XAUTHORITY")
            try:
                os.environ["XAUTHORITY"] = str(self.authority)
                self.connection = display.Display(self.number)
            finally:
                if old_auth is None:
                    os.environ.pop("XAUTHORITY", None)
                else:
                    os.environ["XAUTHORITY"] = old_auth
            environment = dict(os.environ, DISPLAY=self.number, XAUTHORITY=str(self.authority))
            command = [self.tools["openbox"]]
            self._launch(command, "openbox.log", stdout=subprocess.DEVNULL, env=environment)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _check(self):
        if self.cancelled():
            raise DenoiseCancelled("Denoise cancelled.")
        if any(process.poll() is not None for process in self.processes):
            raise RuntimeError("The private display stopped unexpectedly; see the display logs.")

    def windows(self):
        from Xlib import X
        result = []

        def visit(parent, depth=0):
            if depth > 4:
                return
            for window in parent.query_tree().children:
                if window.get_attributes().map_state != X.IsViewable:
                    continue
                name = window.get_wm_name()
                if name:
                    result.append((window, name))
                visit(window, depth + 1)

        visit(self.connection.screen().root)
        return result

    def screenshot(self, window):
        from Xlib import X
        import numpy as np
        size = window.get_geometry()
        raw = window.get_image(0, 0, size.width, size.height, X.ZPixmap, 0xffffffff)
        return np.frombuffer(raw.data, dtype=np.uint8).reshape(size.height, size.width, 4)[:, :, :3].copy()

    def _match(self, pixels, name):
        import cv2
        offset = max(0, pixels.shape[0] - 70) if name == "apply" else 0
        pixels = pixels[offset:] if name == "apply" else pixels[:130]
        variants = [self.templates[name]]
        if name == "apply" and "apply_hover" in self.templates:
            variants.append(self.templates["apply_hover"])
        for template in variants:
            if pixels.shape[0] < template.shape[0] or pixels.shape[1] < template.shape[1]:
                continue
            scores = cv2.matchTemplate(pixels, template, cv2.TM_SQDIFF_NORMED)
            score, _, point, _ = cv2.minMaxLoc(scores)
            if score <= .003:
                return point[0] + template.shape[1] // 2, offset + point[1] + template.shape[0] // 2
        return None

    def _click(self, window, point):
        from Xlib import X
        from Xlib.ext import xtest
        # translate_coords is called on the destination window in Python Xlib.
        origin = self.connection.screen().root.translate_coords(window, 0, 0)
        xtest.fake_input(self.connection, X.MotionNotify, x=origin.x + point[0], y=origin.y + point[1])
        xtest.fake_input(self.connection, X.ButtonPress, 1)
        xtest.fake_input(self.connection, X.ButtonRelease, 1)
        self.connection.sync()

    def poll_profile(self):
        """Return true only after a fresh Auto Profile result has been applied."""
        import cv2
        self._check()
        candidates = []
        for window, title in self.windows():
            if title.startswith("Neat Video "):
                if not title.startswith("Neat Video 6.1.2 "):
                    raise RuntimeError(f"Unsupported Neat Video version: {title}. Qualified version: 6.1.2.")
                candidates.append(window)
            elif self._unexpected_dialog(window):
                cv2.imwrite(str(self.directory / "unexpected-dialog.png"), self._dialog_screenshot(window))
                raise RuntimeError(f"Unexpected dialog during denoise: {title}. See unexpected-dialog.png.")
        if not candidates:
            return self.applied
        if len(candidates) != 1:
            raise RuntimeError("More than one Neat Video window appeared.")
        window = candidates[0]
        pixels = self.screenshot(window)
        cv2.imwrite(str(self.directory / "profile-window.png"), pixels)
        if not self.clicked_auto:
            point = self._match(pixels, "auto_profile")
            if point:
                self._click(window, point)
                self.clicked_auto = True
                self.notify({"stage": "auto_profiling", "message": "Clicked Auto Profile."})
            return False
        point = self._match(pixels, "apply")
        if point and self._match(pixels, "build_profile") and self._match(pixels, "adjust_ready"):
            # Require enabled controls to remain ready across multiple polls.
            # A disabled Apply button must reset the readiness interval too.
            now = time.monotonic()
            if self.ready_since is None:
                self.ready_since = now
            if now - self.ready_since >= .3:
                # The host refuses to save unless this fresh-profile receipt exists.
                (self.directory / "profile-confirmed").write_text("Auto Profile completed\n", encoding="utf-8")
                self._click(window, point)
                self.applied = True
                self.notify({"stage": "applying_profile", "message": "Fresh profile verified; clicked Apply."})
                return True
        else:
            self.ready_since = None
        return False

    def _unexpected_dialog(self, window):
        return (window.get_wm_transient_for() is not None
                and not any(value.casefold() in {"nuke", "nukex", "nukestudio"}
                            for value in (window.get_wm_class() or ())))

    def _dialog_screenshot(self, window):
        return self.screenshot(self.connection.screen().root)

    def __exit__(self, *_):
        stop_process(self.child)
        if self.connection:
            self.connection.close()
        for process in reversed(self.processes):
            stop_process(process)
        for log in self.logs:
            log.close()


def private_display(directory, cancelled, tools=None, notify=None):
    if sys.platform == "win32":
        from nuke_denoise_windows import WindowsDisplay
        return WindowsDisplay(directory, cancelled, notify=notify)
    return PrivateDisplay(directory, cancelled, tools, notify=notify)
