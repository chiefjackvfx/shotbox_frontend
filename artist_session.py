"""Resolve workstation configuration and activate a server-backed artist session."""
from concurrent.futures import Future
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import requests
from PyQt6.QtCore import QEventLoop, QPoint, QRect, QSettings, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from artist_picker import ProfileSplash
from nuke_lock_utils import local_machine_name
from settings import DEFAULT_SETTINGS, SettingsManager, get_settings_file_path, install_settings_manager
from themes import DEFAULT_THEME, apply_theme

# Paths can refer to machine-specific tools, mounted volumes or OCIO configs.
LOCAL_KEYS = {
    "nuke_exe_path", "threede_exe_path", "houdini_exe_path",
    "houdini_user_pref_dir", "deadline_client", "deadline_shared_roots",
    "preview_output_subdir",
}
PORTABLE_KEYS = DEFAULT_SETTINGS.keys() - LOCAL_KEYS - {"django_username", "server_url"}


def portable(values):
    result = {key: deepcopy(value) for key, value in values.items() if key in PORTABLE_KEYS}
    if "review_ocio" in result:
        result["review_ocio"].pop("file", None)
    return result


class StartupShots:
    """One read-only dashboard request owned by a specific server and artist."""

    def __init__(self, server, artist):
        self.url = server.rstrip("/") + "/api/"
        self.artist_id = artist["id"]
        self.username = artist["username"]
        self.started = monotonic()
        self.ready = Event()
        self.jobs = None
        Thread(target=self._fetch, name="Shotbox startup shots", daemon=True).start()

    def _fetch(self):
        try:
            with requests.get(self.url, headers={"X-ShotBox-User": self.username}, timeout=6) as response:
                response.raise_for_status()
                jobs = response.json()
                if isinstance(jobs, list):
                    self.jobs = jobs
        except (requests.RequestException, ValueError):
            pass  # The dashboard retries through its ordinary worker.
        finally:
            self.ready.set()

    def result_for(self, url, username):
        if url != self.url or username != self.username or monotonic() - self.started > 30:
            return None
        # Called on the dashboard worker thread, never the GUI thread.
        if self.ready.wait(timeout=6):
            return self.jobs
        return None


class ArtistSettings(SettingsManager):
    def __init__(self, server, artist, workstation, legacy, *, staging=False):
        self.staging = staging
        self.artist = artist
        self.workstation = workstation
        self.url = f"{server}/api/artist-profiles/{artist['id']}"
        self._settings_path = f"{server} · profiles/{artist['id']}.yaml"
        self.save_error = ""
        self.headers = {"X-ShotBox-User": artist["username"]}
        response = requests.get(self.url, headers=self.headers, timeout=5)
        response.raise_for_status()
        document = response.json()
        self.revision = document["revision"]
        self.saved = document["settings"]
        self._settings = deepcopy(DEFAULT_SETTINGS)
        if (self.revision is None and legacy and legacy.get("django_username") == artist["id"]
                and (legacy.get("server_url") or "").rstrip("/") == server):
            self._settings = self._deep_merge(self._settings, portable(legacy.get_all()))
        restored = portable(self.saved)
        self._settings = self._deep_merge(self._settings, restored)
        for key in LOCAL_KEYS | {"review_ocio.file"}:
            if workstation.contains(key):
                self.set(key, json.loads(workstation.value(key, type=str)), save=False)
            elif legacy:
                self.set(key, deepcopy(legacy.get(key)), save=False)
        self._settings["server_url"] = server
        self._settings["django_username"] = artist["id"]
        self.server = server
        self.next_server = server
        self.saved_server = server
        self.original = portable(self._settings)
        if self.revision is None and not staging:
            self._upload(self.original)
        self.saved_local = self.local_values()

    def confirm(self):
        if self.revision is None:
            self._upload(self.original)
        self.staging = False

    def local_values(self):
        return {key: deepcopy(self.get(key)) for key in LOCAL_KEYS | {"review_ocio.file"}}

    def reset_to_defaults(self):
        local = self.local_values()
        events = deepcopy(self.get("special_events"))
        self._settings = deepcopy(DEFAULT_SETTINGS)
        self._settings["special_events"] = events
        for key, value in local.items():
            self.set(key, value, save=False)
        self._save_settings()

    def _upload(self, values):
        response = requests.put(self.url, headers=self.headers,
                                json={"revision": self.revision, "settings": values}, timeout=5)
        if response.status_code == 409:
            raise ValueError("Another session changed this profile. Reopen Shotbox to load its latest settings.")
        response.raise_for_status()
        self.revision = response.json()["revision"]
        self.saved = deepcopy(values)

    def _save_settings(self):
        if self.staging:
            return True
        # Reset-to-defaults also passes here. Keep the session identity and server pinned.
        self._settings.update(django_username=self.artist["id"], server_url=self.server)
        try:
            values = portable(self._settings)
            if values != self.original:
                # Preserve fields from other client versions.
                preserved = {key: value for key, value in self.saved.items() if key not in PORTABLE_KEYS}
                self._upload({**preserved, **values})
                self.original = values
            local = self.local_values()
            if local != self.saved_local or self.next_server != self.saved_server:
                for key, value in local.items():
                    if value != self.saved_local[key]:
                        self.workstation.setValue(key, json.dumps(value))
                if self.next_server != self.saved_server:
                    self.workstation.setValue("server", self.next_server)
                self.workstation.sync()
                if self.workstation.status() != QSettings.Status.NoError:
                    raise OSError("Cannot write workstation settings.")
                self.saved_local = local
                self.saved_server = self.next_server
        except (requests.RequestException, KeyError, ValueError, OSError) as exc:
            self.save_error = str(exc)
            print(f"[Artist settings] {exc}")
            return False
        self.save_error = ""
        return True


def fetch_roster(server, picker, on_finished):
    result = Future()

    def fetch():
        try:
            response = requests.get(f"{server}/api/artist-profiles", timeout=5)
            response.raise_for_status()
            result.set_result(response.json())
        except Exception as exc:
            result.set_exception(exc)

    poll = QTimer(QApplication.instance())

    def finish(completed):
        poll.stop()
        picker.rejected.disconnect(cancel)
        poll.deleteLater()
        on_finished(completed)

    def cancel():
        finish(None)

    def check_result():
        if result.done():
            finish(result)

    poll.timeout.connect(check_result)
    picker.rejected.connect(cancel)
    picker.set_load_progress("Loading artist profiles…")
    picker.show()
    Thread(target=fetch, name="Shotbox artist roster", daemon=True).start()
    poll.start(20)


def discard_staged_window(window):
    # Drain the old session's workers before installing a different API identity.
    while not window.close():
        loop = QEventLoop()
        QTimer.singleShot(100, loop.quit)
        loop.exec()
    window.deleteLater()


def choose_while_loading(window, dialog):
    dashboard = window.page_nukedash

    def ready():
        dialog.set_load_progress("Workspace ready · Choose your profile", 1, 1)

    def failed(message):
        dialog.set_load_progress("Could not load shots · Retry", 0, 1)
        dialog.progress.setToolTip(message)
        dialog.retry_button.show()

    def retry():
        dialog.retry_button.hide()
        dialog.set_load_progress("Fetching shots…")
        dashboard._request_api_refresh()

    dashboard.startup_progress.connect(dialog.set_load_progress)
    dashboard.startup_ready.connect(ready)
    dashboard.startup_failed.connect(failed)
    dialog.retry_button.clicked.connect(retry)
    dialog.set_load_progress("Loading shots…")
    if not dashboard._awaiting_workspace:
        ready()
    selected_id = dialog.select_for_startup()
    dashboard.startup_progress.disconnect(dialog.set_load_progress)
    dashboard.startup_ready.disconnect(ready)
    dashboard.startup_failed.disconnect(failed)
    dialog.retry_button.clicked.disconnect(retry)
    return selected_id


def start_artist_session(window_factory, on_finished):
    workstation = QSettings("Shotbox", "Desktop")
    workstation.beginGroup("workstations/" + local_machine_name())
    legacy_path = get_settings_file_path()
    legacy = SettingsManager(legacy_path) if Path(legacy_path).is_file() else None
    initial_server = legacy.get("server_url") if legacy else DEFAULT_SETTINGS["server_url"]
    server = workstation.value("server", initial_server, type=str).rstrip("/")
    apply_theme(DEFAULT_THEME)

    def begin():
        memory = QSettings("Shotbox", "Desktop")
        memory.beginGroup(workstation.group() + "/picker/" + sha256(server.encode()).hexdigest())
        defaults = legacy or DEFAULT_SETTINGS
        initial_center = None
        if defaults.get("remember_window_size", True):
            initial_center = QRect(
                defaults.get("window_x"), defaults.get("window_y"),
                defaults.get("window_width"), defaults.get("window_height"),
            ).center()
        center = memory.value("workspace_center", initial_center)
        dialog = ProfileSplash(None, memory, center if isinstance(center, QPoint) else None)
        dialog.keep_open_after_choice = True
        fetch_roster(server, dialog, lambda result: open_workspace(dialog, memory, result))

    def open_workspace(dialog, memory, result):
        if result is None:
            on_finished(None)
            return
        window = None
        try:
            entries = result.result()
            artists = {str(item["id"]): item for item in entries}
            if not artists:
                raise ValueError("This server has no active VFX artists.")
            roster = []
            for key, item in artists.items():
                name = f"{item['first_name']} {item['last_name']}".strip()
                roster.append((key, item["username"], name or item["username"]))
            dialog.populate_profiles(roster)

            def build(artist_id):
                artist = artists[artist_id]
                preload = StartupShots(server, artist)
                manager = ArtistSettings(server, artist, workstation, legacy, staging=True)
                manager.startup_picker = dialog
                manager.startup_shots = preload
                install_settings_manager(manager)
                window = window_factory()
                center = window.geometry().center()
                if not manager.get("remember_window_size") or QApplication.screenAt(center) is None:
                    window.move(dialog.workspace_center - window.rect().center())
                dialog.center_on_workspace(window.geometry().center())
                return window

            if QApplication.platformName() == "windows":
                app = QApplication.instance()
                # Keep the top-level picker alive after the roster callback returns.
                app.pending_artist_picker = dialog

                def cancelled():
                    app.pending_artist_picker = None
                    on_finished(None)

                def chosen(selected_id):
                    if not dialog.isVisible():
                        return
                    dialog.rejected.disconnect(cancelled)
                    app.pending_artist_picker = None
                    window = None
                    try:
                        print("[ShotBox] Opening selected artist settings", flush=True)
                        window = build(selected_id)
                        print("[ShotBox] Dashboard constructed, activating session", flush=True)
                        confirm(window, memory, selected_id)
                    except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
                        recover(dialog, window, exc)
                        return
                    on_finished(window)

                dialog.rejected.connect(cancelled)
                dialog.profile_chosen.connect(chosen, Qt.ConnectionType.QueuedConnection)
                dialog.waiting_for_choice = True
                dialog.progress.hide()
                dialog.update_login_timer()
                return

            remembered = dialog.returning_artist
            staged_id = remembered.artist_id if remembered else roster[0][0]
            dialog.status_label.setText(f"Preparing {artists[staged_id]['username']}")
            dialog.status_label.show()
            dialog.set_load_progress("Loading artist workspace…")
            dialog.repaint()
            try:
                window = build(staged_id)
            except (requests.RequestException, ValueError) as exc:
                dialog.set_load_progress("Profile unavailable · Choose an artist", 0, 1)
                dialog.progress.setToolTip(str(exc))
                selected_id = dialog.select_for_startup()
            else:
                selected_id = choose_while_loading(window, dialog)
            if window is not None and selected_id != staged_id:
                discard_staged_window(window)
                window = None
            if selected_id is None:
                on_finished(None)
                return
            if window is None:
                dialog.retry_button.hide()
                window = build(selected_id)
            confirm(window, memory, selected_id)
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            recover(dialog, window, exc)
            return
        on_finished(window)

    def confirm(window, memory, selected_id):
        window._settings_manager.confirm()
        window.activate_staged_session()
        window.picker_memory = memory
        memory.setValue("workspace_center", window.geometry().center())
        memory.setValue("last_artist", selected_id)
        workstation.setValue("server", server)

    def recover(dialog, window, exc):
        nonlocal server
        if window is not None:
            discard_staged_window(window)
        dialog.close()
        dialog.deleteLater()
        box = QMessageBox()
        box.setWindowTitle("Shotbox · Artist profiles")
        box.setText(f"Could not open artist profiles on {server}.")
        box.setInformativeText(str(exc))
        retry = box.addButton("Retry", QMessageBox.ButtonRole.AcceptRole)
        change = box.addButton("Change server", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Exit", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is change:
            value, ok = QInputDialog.getText(None, "Shotbox server", "Server URL", text=server)
            if ok and value.strip():
                server = value.strip().rstrip("/")
        elif box.clickedButton() is not retry:
            on_finished(None)
            return
        QTimer.singleShot(0, begin)

    begin()
