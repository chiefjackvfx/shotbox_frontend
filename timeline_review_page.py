"""Independent native Timeline Review tab, using the existing Shotbox API."""
from __future__ import annotations

from copy import deepcopy
from functools import partial
import json
import threading
from urllib.parse import urljoin

from PyQt6.QtCore import Qt, QTimer, QThreadPool
from PyQt6.QtGui import QImage, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
    QScrollArea, QSplitter, QSlider, QCheckBox, QMessageBox, QColorDialog,
    QDialog,
)

import http_help
from input_behaviour import install_input_wheel_guard
from image_loader import ImageLoader
from task_create_dialog import STATUS_COLORS
from timeline_review_model import Edit, prepare_timeline, timecode, parse_timecode
from timeline_review_widgets import ReviewPicture, TimelineStrip
from timeline_review_media import MovieDecoder, Work
from timeline_review_sidebar import build_sidebar, sync_drawing_controls, task_card


STATUSES = [(key, key.replace("_", " ").capitalize()) for key in STATUS_COLORS]


class TimelineReviewPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("timeline_review_page")
        self.edit = Edit()
        self.frame = 0
        self.playing = False
        self.direction = 1
        self.playback_speed = 1
        self.jobs = []
        self.users = []
        self.draft = None
        self.suspended_draft = None
        self.selected_task = None
        self._loading_form = False
        self._saving = False
        self._closed = False
        self._started = False
        self._initial_job_id = None
        self._epoch = 0
        self._revision = 0
        self._load_cancel = threading.Event()
        self._load_ticket = 0
        self._decode_ticket = 0
        self._decode_busy = False
        self._display_key = None
        self._requested_key = None
        self._polling = False
        self._pending_tasks = set()
        self._thumbnail_pending = set()
        self._thumbnail_failed = set()
        self._thumbnail_epoch = 0
        self._shot_dirty = False
        self._details_id = None
        self._workers = {}
        self._serial = 0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(3)
        self._media_pool = QThreadPool(self)
        self._media_pool.setMaxThreadCount(1)
        self.decoder = MovieDecoder()
        self._presentation = None
        self._settings = None
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self.refresh_tasks)
        self._poll_timer.start()
        self._shortcuts()
        QApplication.instance().focusChanged.connect(self._shortcut_focus_changed)
        install_input_wheel_guard(QApplication.instance())
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    def _button(self, text, fn, layout=None):
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False: fn())
        if layout is not None:
            layout.addWidget(button)
        return button

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        header = QHBoxLayout()
        header.addWidget(QLabel("Job"))
        self.job_combo = QComboBox()
        self.job_combo.setMinimumWidth(140)
        header.addWidget(self.job_combo, 1)
        header.addWidget(QLabel("Timeline"))
        self.timeline_combo = QComboBox()
        self.timeline_combo.setMinimumWidth(140)
        header.addWidget(self.timeline_combo, 1)
        self.refresh_button = self._button("Refresh previews", self.refresh, header)
        self.left_toggle = self._button("Shot details", lambda: self.left.setVisible(not self.left.isVisible()), header)
        self.right_toggle = self._button("Comments & tasks", lambda: self.right.setVisible(not self.right.isVisible()), header)
        root.addLayout(header)
        self.status = QLabel("Choose a job and timeline. Silent review.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.splitter = QSplitter()
        root.addWidget(self.splitter, 1)
        self.left = QScrollArea()
        self.left.setWidgetResizable(True)
        self.left.setMinimumWidth(180)
        shot_widget = QWidget()
        shot_layout = QVBoxLayout(shot_widget)
        self.shot_title = QLabel("No shot selected")
        self.shot_title.setWordWrap(True)
        shot_layout.addWidget(self.shot_title)
        self.shot_notes = QPlainTextEdit()
        self.shot_notes.setPlaceholderText("Shot notes")
        shot_layout.addWidget(self.shot_notes)
        self.shot_colour = QLineEdit()
        self.shot_colour.setMaxLength(50)
        shot_layout.addWidget(QLabel("Colour code"))
        shot_layout.addWidget(self.shot_colour)
        self._button("Save shot details", self.save_shot, shot_layout)
        self.visibility_button = self._button("Hide shot (H)", self.toggle_hidden, shot_layout)
        self._button("Refresh details", self.refresh_tasks, shot_layout)
        self.metadata = QPlainTextEdit()
        self.metadata.setReadOnly(True)
        shot_layout.addWidget(self.metadata, 1)
        self.left.setWidget(shot_widget)
        self.splitter.addWidget(self.left)
        self.center = QWidget()
        middle = QVBoxLayout(self.center)
        middle.setContentsMargins(0, 0, 0, 0)
        self.picture = ReviewPicture()
        middle.addWidget(self.picture, 1)
        transport = QHBoxLayout()
        self._button("│←", lambda: self.jump(-1), transport).setToolTip("Previous shot (Left)")
        self._button("◀", lambda: self.step(-1), transport).setToolTip("Previous frame (,)")
        self.play_button = self._button("Play", self.toggle_play, transport)
        self.play_button.setToolTip("Space: play/pause · J: reverse · K: stop · L: forward · press J/L twice for 2×")
        self._button("▶", lambda: self.step(1), transport).setToolTip("Next frame (.)")
        self._button("→│", lambda: self.jump(1), transport).setToolTip("Next shot (Right)")
        self.repeat = QCheckBox("Repeat shot")
        transport.addWidget(self.repeat)
        self.repeat_mode = QComboBox()
        self.repeat_mode.addItems(["Repeat", "Ping-pong"])
        transport.addWidget(self.repeat_mode)
        self._button("Fullscreen", self.fullscreen, transport)
        middle.addLayout(transport)
        versions = QHBoxLayout()
        self.current_label = QLabel("No shot selected")
        versions.addWidget(self.current_label, 1)
        self._button("↑ Older", lambda: self.change_version(-1), versions)
        self.version_combo = QComboBox()
        versions.addWidget(self.version_combo)
        self._button("↓ Newer", lambda: self.change_version(1), versions)
        self._button("Latest", lambda: self.change_version("latest"), versions)
        self.zoom_label = QLabel("100%")
        versions.addWidget(self.zoom_label)
        self._button("Fit picture", self.picture.fit, versions)
        middle.addLayout(versions)
        quality = QHBoxLayout()
        quality.addWidget(QLabel("Resolution"))
        self.resolution = QComboBox()
        for label, scale in (("Full", 1), ("Half", 2), ("Quarter", 4), ("Eighth", 8)):
            self.resolution.addItem(label, scale)
        quality.addWidget(self.resolution)
        quality.addWidget(QLabel("Cache RAM"))
        self.cache_limit = QComboBox()
        for mb in (256, 512, 1024, 2048):
            self.cache_limit.addItem(f"{mb} MB", mb)
        quality.addWidget(self.cache_limit)
        self.cache_label = QLabel("0 MB")
        quality.addWidget(self.cache_label, 1)
        self._button("Clear frame cache", self.clear_frame_cache, quality)
        middle.addLayout(quality)
        controls = QHBoxLayout()
        self.timecode = QLineEdit("00:00:00:00")
        self.timecode.setMaximumWidth(125)
        self.timecode.setToolTip("Timecode relative to the start of this timeline (non-drop-frame)")
        controls.addWidget(self.timecode)
        self._button("Go", self.go_timecode, controls)
        self.duration_label = QLabel("—")
        controls.addWidget(self.duration_label, 1)
        self.tasks_toggle = QCheckBox("Tasks")
        self.tasks_toggle.setChecked(True)
        controls.addWidget(self.tasks_toggle)
        controls.addWidget(QLabel("Zoom"))
        self.timeline_zoom = QSlider(Qt.Orientation.Horizontal)
        self.timeline_zoom.setRange(1, 64)
        self.timeline_zoom.setValue(4)
        self.timeline_zoom.setMaximumWidth(160)
        controls.addWidget(self.timeline_zoom)
        self._button("Fit", lambda: self.timeline_zoom.setValue(4), controls)
        middle.addLayout(controls)
        self.timeline_scroll = QScrollArea()
        self.timeline_scroll.setWidgetResizable(False)
        self.timeline_scroll.setFixedHeight(195)
        self.timeline_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.strip = TimelineStrip()
        self.timeline_scroll.setWidget(self.strip)
        middle.addWidget(self.timeline_scroll)
        self.splitter.addWidget(self.center)
        build_sidebar(self, STATUSES)
        self.job_combo.currentIndexChanged.connect(self._job_changed)
        self.timeline_combo.currentIndexChanged.connect(self._timeline_changed)
        self.version_combo.activated.connect(self._version_selected)
        self.picture.clicked.connect(self.toggle_play)
        self.picture.drawing_started.connect(self._begin_drawing)
        self.picture.drawing_changed.connect(self._drawing_changed)
        self.picture.zoom_changed.connect(lambda n: self.zoom_label.setText(f"{n}%"))
        self.tool.currentIndexChanged.connect(self._tool_changed)
        self.stroke_size.currentIndexChanged.connect(lambda: setattr(self.picture, "stroke_size", self.stroke_size.currentData()))
        self.strip.seek.connect(self.scrub)
        self.strip.task_selected.connect(self.select_task)
        self.strip.add_task.connect(self.new_task_at)
        self.strip.thumbnail_needed.connect(self._load_thumbnail)
        self.timeline_zoom.valueChanged.connect(self._layout_strip)
        self.tasks_toggle.toggled.connect(self._toggle_tasks)
        self.comment_filter.currentIndexChanged.connect(self._render_comments)
        self.timecode.returnPressed.connect(self.go_timecode)
        self.resolution.currentIndexChanged.connect(self._quality_changed)
        self.cache_limit.currentIndexChanged.connect(self._cache_changed)
        self.shot_notes.textChanged.connect(self._mark_shot_dirty)
        self.shot_colour.textEdited.connect(self._mark_shot_dirty)
        self.title.textEdited.connect(self._pin_draft)
        self.notes.textChanged.connect(self._pin_draft)
        for widget in (self.scope, self.task_status, self.artist):
            widget.currentIndexChanged.connect(self._form_changed)
        for widget in (self.range_start, self.range_end, self.budget, self.priority):
            widget.valueChanged.connect(self._form_changed)
        # Presets also pin a draft when they set a value programmatically.
        self.title.textChanged.connect(self._pin_draft)
        self._render_comments()
        self._restore_form({})

    def _submit(self, fn, done=None, *, pool=None, error=None):
        if self._closed:
            return
        self._serial += 1
        key = self._serial
        work = Work(key, fn)
        self._workers[key] = (work, self._epoch, done, error)
        work.signals.finished.connect(self._work_finished, Qt.ConnectionType.QueuedConnection)
        (pool or self._pool).start(work)

    def _work_finished(self, key, result, message):
        item = self._workers.pop(key, None)
        if item is None or self._closed or item[1] != self._epoch:
            return
        if message:
            if item[3]:
                item[3](message)
            else:
                self.status.setText(message)
        elif item[2]:
            item[2](result)

    def showEvent(self, event):
        super().showEvent(event)
        self._layout_strip()
        if not self._started:
            self._started = True
            self.refresh()

    def hideEvent(self, event):
        self.pause()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "strip"):
            self._layout_strip()

    def set_settings_manager(self, settings):
        self._settings = settings
        self.cache_limit.setCurrentIndex(max(0, self.cache_limit.findData(settings.get("timeline_review_cache_mb", 256))))
        self.resolution.setCurrentIndex(max(0, self.resolution.findData(settings.get("timeline_review_scale", 1))))

    def set_initial_job(self, job):
        if not self._started:
            self._initial_job_id = job.get("id")

    def refresh(self):
        self.pause()
        self._load_ticket += 1
        ticket = self._load_ticket
        self.refresh_button.setEnabled(False)
        self.status.setText("Loading jobs and local previews…")
        api = http_help.DjangoAPI()
        def load():
            return api.get_jobs(), api.get_users()
        def ready(result):
            if ticket != self._load_ticket:
                return
            self.refresh_button.setEnabled(True)
            job_id = self.job_combo.currentData() or self._initial_job_id
            timeline_id = self.timeline_combo.currentData()
            self.jobs, self.users = result
            self.jobs = [j for j in self.jobs if not j.get("hidden")]
            self._populate_artists()
            self.job_combo.blockSignals(True)
            self.job_combo.clear()
            for job in self.jobs:
                self.job_combo.addItem(job["title"], job["id"])
            self.job_combo.setCurrentIndex(max(0, self.job_combo.findData(job_id)))
            self.job_combo.blockSignals(False)
            self._job_changed(preferred=timeline_id, preserve=True)
        self._submit(load, ready, error=lambda msg: self._load_error(ticket, msg))

    def _load_error(self, ticket, message):
        if ticket == self._load_ticket:
            self.refresh_button.setEnabled(True)
            self.status.setText(f"Unable to load review: {message}. Use Refresh previews to retry.")

    def _populate_artists(self):
        selected = self.artist.currentData()
        self.artist.blockSignals(True)
        self.artist.clear()
        self.artist.addItem("Unassigned", None)
        for user in self.users:
            self.artist.addItem(user.get("first_name") or user["username"], user["id"])
        self.artist.setCurrentIndex(max(0, self.artist.findData(selected)))
        self.artist.blockSignals(False)

    def _job_changed(self, *_args, preferred=None, preserve=False):
        job = next((j for j in self.jobs if j["id"] == self.job_combo.currentData()), {})
        self.timeline_combo.blockSignals(True)
        self.timeline_combo.clear()
        for timeline in sorted(job.get("timelines", []), key=lambda t: t["title"]):
            self.timeline_combo.addItem(timeline["title"], timeline["id"])
        self.timeline_combo.setCurrentIndex(max(0, self.timeline_combo.findData(preferred)))
        self.timeline_combo.blockSignals(False)
        self._timeline_changed(preserve=preserve)

    def _timeline_changed(self, *_args, preserve=False):
        self.pause()
        self._load_cancel.set()
        self._load_cancel = threading.Event()
        cancelled = self._load_cancel
        self._load_ticket += 1
        self._thumbnail_pending.clear()
        self._thumbnail_failed.clear()
        ticket = self._load_ticket
        timeline_id = self.timeline_combo.currentData()
        old_frame = self.frame
        old_id = self.edit.timeline.get("id")
        old_versions = {c.shot["id"]: c.media.get("version") for c in self.edit.cuts}
        self._invalidate_media()
        self.edit = Edit()
        self._details_id = None
        self.strip.edit = self.edit
        self.strip.update()
        self.picture.set_image(QImage())
        self.picture.overlay_text = ""
        self.picture.message = "Loading timeline…" if timeline_id else "No timelines available."
        self._render_comments()
        if timeline_id is None:
            return
        api = http_help.DjangoAPI()
        def ready(data):
            if ticket != self._load_ticket:
                return
            if preserve and old_id == timeline_id:
                for shot in data.get("shots", []):
                    shot["preview"] = next((m for m in shot.get("versions", [])
                                            if m["version"] == old_versions.get(shot["id"])), shot.get("preview"))
            self.edit = Edit(data)
            self.strip.edit = self.edit
            self._layout_strip()
            self.duration_label.setText(f"{timecode(self.edit.end - self.edit.start, self.edit.fps)} · {self.edit.fps:g} fps")
            self.status.setText(f"{len(self.edit.cuts)} shots · Silent review" + (" · Sequential timing fallback" if self.edit.sequential else ""))
            self.seek(old_frame if preserve and old_id == timeline_id else self.edit.start)
            self._render_comments()
        self._submit(lambda: prepare_timeline(api.get_timeline(timeline_id), cancelled), ready,
                     error=lambda msg: self._load_error(ticket, msg))

    def _invalidate_media(self):
        self._decode_ticket += 1
        old = self.decoder
        old.cancel()
        self._submit(old.close, pool=self._media_pool)
        self.decoder = MovieDecoder()
        self.decoder.limit = self.cache_limit.currentData() * 1024**2
        self._display_key = self._requested_key = None
        self._decode_busy = False

    def _layout_strip(self, *_args):
        self.strip.resize(max(1, round(self.timeline_scroll.viewport().width() * self.timeline_zoom.value() / 4)), self.strip.height())
        self.strip.update()

    def _toggle_tasks(self, checked):
        self.strip.show_tasks = checked
        self.strip.update()

    def seek(self, frame):
        if not self.edit.cuts:
            self._show_shot(None)
            self.picture.message = "No shots in this timeline."
            self.picture.update()
            return
        previous = self.edit.at(self.frame)
        self.frame = max(self.edit.start, min(self.edit.end - 1, int(frame)))
        cut = self.edit.at(self.frame)
        self.strip.frame = self.frame
        self.strip.update()
        if not self.timecode.hasFocus():
            self.timecode.setText(timecode(self.frame - self.edit.start, self.edit.fps))
        self.current_label.setText(f"{cut.shot['title']} · Frame {cut.working_frame(self.frame)}" if cut else "Editorial gap")
        if cut is not previous or self._details_id != (cut.shot["id"] if cut else None):
            self._render_comments()
            self._show_shot(cut)
        self._sync_versions(cut)
        self._sync_drawing_context()
        self._request_picture()

    def _request_picture(self):
        cut = self.edit.at(self.frame)
        media = cut.media if cut else {}
        if not media or media.get("error"):
            self._requested_key = None
            self._display_key = None
            self.picture.message = (media.get("error") or "Preview not available yet") if cut else "Editorial gap"
            thumbnail = self.strip.thumbnails.get(cut.shot["id"]) if cut else None
            self.picture.overlay_text = "Preview unavailable · Shot thumbnail" if thumbnail is not None else ""
            self.picture.set_image(thumbnail if thumbnail is not None else QImage())
            if cut:
                self._load_thumbnail(cut.shot)
            if cut and self.playing:
                self.pause()
                self.status.setText(f"{cut.shot['title']}: preview unavailable. Choose another shot or refresh previews.")
            return
        index = cut.source_frame(self.frame)
        scale = self.resolution.currentData()
        key = (media["path"], media.get("identity"), index, scale)
        self._requested_key = key
        if self._display_key == key or self._decode_busy:
            return
        self._decode_busy = True
        ticket, decoder = self._decode_ticket, self.decoder
        def ready(image):
            if ticket != self._decode_ticket:
                return
            self._decode_busy = False
            if key == self._requested_key and image is not None:
                self._display_key = key
                self.picture.overlay_text = ""
                self.picture.set_image(image)
                self.cache_label.setText(f"{decoder.bytes / 1024**2:.0f} MB")
            self._request_picture()
        def failed(message):
            if ticket != self._decode_ticket:
                return
            self._decode_busy = False
            self.pause()
            if key == self._requested_key:
                self.picture.message = "Unable to decode this preview."
                self.picture.set_image(QImage())
                self.status.setText(message)
            else:
                self._request_picture()
        direction = self.direction if self.playing else 1
        next_cut = (next((c for c in self.edit.cuts if c.start >= cut.end), None) if direction > 0 else
                    next((c for c in reversed(self.edit.cuts) if c.end <= cut.start), None))
        near_cut = cut.end - self.frame <= 5 if direction > 0 else self.frame - cut.start < 5
        def decode():
            image = decoder.read(media, index, scale)
            for offset in range(1, 4):
                following = index + direction * offset
                if decoder._cancelled.is_set() or not 0 <= following < media.get("frame_count", 0):
                    break
                try:
                    decoder.read(media, following, scale)
                except Exception:
                    break
            if near_cut and next_cut and next_cut.media and not next_cut.media.get("error"):
                try:
                    decoder.read(next_cut.media, next_cut.source_frame(next_cut.start if direction > 0 else next_cut.end - 1), scale)
                except Exception:
                    pass  # A speculative load never fails the currently visible frame.
            return image
        self._submit(decode, ready, pool=self._media_pool, error=failed)

    def _load_thumbnail(self, shot):
        shot_id = shot["id"]
        url = shot.get("thumbnail")
        if not url or shot_id in self._thumbnail_pending or shot_id in self._thumbnail_failed or shot_id in self.strip.thumbnails:
            return
        self._thumbnail_pending.add(shot_id)
        epoch, ticket, generation = self._epoch, self._load_ticket, self._thumbnail_epoch
        def ready(pixmap):
            if self._closed or epoch != self._epoch or ticket != self._load_ticket or generation != self._thumbnail_epoch:
                return
            self._thumbnail_pending.discard(shot_id)
            if pixmap is None:
                self._thumbnail_failed.add(shot_id)
                return
            image = pixmap.toImage().scaled(256, 144, Qt.AspectRatioMode.KeepAspectRatio,
                                            Qt.TransformationMode.SmoothTransformation)
            self.strip.thumbnails[shot_id] = image
            while len(self.strip.thumbnails) > 200:
                self.strip.thumbnails.popitem(last=False)
            self.strip.update()
            cut = self.edit.at(self.frame)
            if cut and cut.shot["id"] == shot_id and (not cut.media or cut.media.get("error")):
                self._request_picture()
        ImageLoader.instance().load(urljoin(http_help.DjangoAPI._default_base_url, url), ready)

    def scrub(self, frame):
        self.pause()
        self.seek(frame)

    def toggle_play(self):
        if self.playing:
            self.pause()
            return
        self._start_playback()

    def shuttle(self, direction):
        """J/L start normal playback; another press in that direction selects 2×."""
        speed = 2 if self.playing and self.direction == direction else 1
        self._start_playback(direction, speed)

    def _start_playback(self, direction=1, speed=1):
        if self.picture.tool or (self.draft and self.draft.get("annotations")):
            self.status.setText("Save or clear the drawing before resuming playback.")
            return
        if not self.edit.cuts:
            return
        self.playing = True
        self.direction = direction
        self.playback_speed = speed
        self._update_play_label()
        self._timer.start(max(1, round(1000 / (self.edit.fps * speed))))

    def _update_play_label(self):
        self.play_button.setText(f"Pause {'→' if self.direction > 0 else '←'} {self.playback_speed}×")

    def pause(self):
        self.playing = False
        self.playback_speed = 1
        if hasattr(self, "_timer"):
            self._timer.stop()
        self.play_button.setText("Play")

    def _tick(self):
        if self._decode_busy or self._requested_key != self._display_key:
            return
        cut = self.edit.at(self.frame)
        start, end = (cut.start, cut.end) if self.repeat.isChecked() and cut else (self.edit.start, self.edit.end)
        target = self.frame + self.direction
        if target < start or target >= end:
            if self.repeat.isChecked() and cut:
                if self.repeat_mode.currentIndex() == 1:
                    self.direction *= -1
                    self._update_play_label()
                    target = max(start, min(end - 1, self.frame + self.direction))
                else:
                    target = start if self.direction > 0 else end - 1
            else:
                self.pause()
                return
        self.seek(target)
        self.timeline_scroll.ensureVisible(round(self.strip.frame_x(self.frame)), 50, 50, 0)

    def step(self, direction):
        self.pause()
        self.seek(self.frame + direction)

    def jump(self, direction):
        candidates = [c.start for c in self.edit.cuts if c.start > self.frame] if direction > 0 else [c.start for c in self.edit.cuts if c.start < self.frame]
        if candidates:
            self.seek(min(candidates) if direction > 0 else max(candidates))

    def go_timecode(self):
        try:
            value = parse_timecode(self.timecode.text(), self.edit.fps)
            if not 0 <= value < self.edit.end - self.edit.start:
                raise ValueError("Timecode is outside this timeline.")
            self.scrub(self.edit.start + value)
            self.timecode.clearFocus()
        except ValueError as exc:
            self.status.setText(str(exc))

    def _sync_versions(self, cut):
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        if cut:
            for media in cut.shot.get("versions", []):
                self.version_combo.addItem(f"v{media['version']:03}", media["version"])
            self.version_combo.setCurrentIndex(self.version_combo.findData(cut.media.get("version")))
        self.version_combo.blockSignals(False)

    def change_version(self, direction):
        cut = self.edit.at(self.frame)
        if not cut:
            return
        versions = cut.shot.get("versions", [])
        current = cut.media.get("version", 0)
        candidates = [m for m in versions if m["version"] < current] if direction == -1 else [m for m in versions if m["version"] > current]
        media = versions[0] if direction == "latest" and versions else (
            max(candidates, key=lambda m: m["version"]) if direction == -1 and candidates else
            min(candidates, key=lambda m: m["version"]) if candidates else None)
        if media:
            cut.shot["preview"] = media
            self._sync_versions(cut)
            self._render_comments()
            self._sync_drawing_context()
            self._request_picture()
            self.strip.update()

    def _version_selected(self, index):
        cut = self.edit.at(self.frame)
        if cut:
            media = next((m for m in cut.shot.get("versions", []) if m["version"] == self.version_combo.itemData(index)), None)
            if media:
                cut.shot["preview"] = media
                self._render_comments()
                self._sync_drawing_context()
                self._request_picture()
                self.strip.update()

    def _quality_changed(self):
        if self._settings:
            self._settings.set("timeline_review_scale", self.resolution.currentData())
        self._request_picture()

    def _cache_changed(self):
        if self._settings:
            self._settings.set("timeline_review_cache_mb", self.cache_limit.currentData())
        # Swap the decoder rather than mutate a cache owned by a worker thread.
        self.clear_frame_cache()

    def clear_frame_cache(self):
        self._thumbnail_epoch += 1
        self._thumbnail_pending.clear()
        self.strip.thumbnails.clear()
        self._thumbnail_failed.clear()
        self.strip.update()
        self._invalidate_media()
        self.cache_label.setText("0 MB")
        if self.isVisible():
            self._request_picture()

    def fullscreen(self):
        if self._presentation:
            self._presentation.close()
            return
        dialog = QDialog(self.window())
        dialog.setWindowTitle("Shotbox · Timeline Review")
        layout = QVBoxLayout(dialog)
        self._fullscreen_sizes = self.splitter.sizes()
        layout.addWidget(self.center)
        self._button("Exit fullscreen (Escape)", dialog.close, layout)
        self._presentation = dialog
        self._shortcuts(dialog)
        dialog.finished.connect(self._end_fullscreen)
        dialog.showFullScreen()

    def _end_fullscreen(self):
        dialog = self._presentation
        self._presentation = None
        if dialog:
            dialog.layout().removeWidget(self.center)
            self.splitter.insertWidget(1, self.center)
            self.splitter.setSizes(self._fullscreen_sizes)
            self.center.show()
            dialog.deleteLater()

    def _shortcuts(self, parent=None):
        parent = parent or self
        parent._timeline_keys = []
        for key, fn in (("Space", self.toggle_play), ("Left", lambda: self.jump(-1)),
                        ("J", lambda: self.shuttle(-1)), ("K", self.pause), ("L", lambda: self.shuttle(1)),
                        ("Right", lambda: self.jump(1)), ("Up", lambda: self.change_version(-1)),
                        ("Down", lambda: self.change_version(1)), (",", lambda: self.step(-1)),
                        (".", lambda: self.step(1)), ("H", self.toggle_hidden),
                        ("Home", lambda: self.scrub(self.edit.start)),
                        ("End", lambda: self.scrub(self.edit.end - 1)), ("F11", self.fullscreen)):
            shortcut = QShortcut(QKeySequence(key), parent)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            if key in ("Space", "J", "K", "L"):
                shortcut.setAutoRepeat(False)
            shortcut.activated.connect(partial(self._key_action, fn))
            parent._timeline_keys.append(shortcut)
        self._shortcut_focus_changed(None, QApplication.focusWidget())

    def _shortcut_focus_changed(self, _previous, focus):
        editing = isinstance(focus, (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox))
        for parent in (self, self._presentation):
            for shortcut in getattr(parent, "_timeline_keys", []):
                shortcut.setEnabled(not editing)

    def _key_action(self, fn):
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox)):
            return
        fn()

    def _pin_draft(self, *_args):
        if self._loading_form or self._saving or self.draft:
            return
        cut = self.edit.at(self.frame)
        if not cut:
            return
        self.draft = {"cut": deepcopy(cut), "timeline": self.edit.timeline.get("id"),
                      "frame": self.frame, "version": cut.media.get("version"),
                      "task": None, "annotations": None, "server": http_help.DjangoAPI._default_base_url}
        self.range_start.blockSignals(True)
        self.range_end.blockSignals(True)
        self.range_start.setValue(cut.working_frame(self.frame))
        self.range_end.setValue(cut.working_frame(cut.end - 1))
        self.range_start.blockSignals(False)
        self.range_end.blockSignals(False)
        self._draft_caption()

    def _form_changed(self, *_args):
        self._pin_draft()
        self.range_start.setEnabled(self.scope.currentIndex() in (1, 2, 3))
        self.range_end.setEnabled(self.scope.currentIndex() == 3)
        self._draft_caption()
        self._sync_drawing_context()

    def _draft_caption(self):
        if self.draft:
            cut = self.draft["cut"]
            version = self.draft["version"]
            self.draft_label.setText(f"{cut.shot['title']} · " + (f"v{version:03}" if version is not None else "No preview")
                                     + f" · Frame {cut.working_frame(self.draft['frame'], self.draft.get('task'))}")
        else:
            self.draft_label.setText("New task · starts on the current shot")

    def _reference(self):
        if self.scope.currentIndex() == 0:
            return None
        start = self.range_start.value()
        if self.scope.currentIndex() == 1:
            return start
        end = (self.draft["cut"].working_frame(self.draft["cut"].end - 1, self.draft.get("task"))
               if self.scope.currentIndex() == 2 else self.range_end.value())
        if end < start:
            raise ValueError("Range end must be at or after its start.")
        return [start, end]

    def set_range_here(self, end):
        self._pin_draft()
        if not self.draft:
            return
        cut = self.edit.at(self.frame)
        if not cut or cut.shot["id"] != self.draft["cut"].shot["id"]:
            self.status.setText("Return to the draft shot before setting a frame boundary.")
            return
        self.scope.setCurrentIndex(3)
        (self.range_end if end else self.range_start).setValue(cut.working_frame(self.frame, self.draft.get("task")))

    def _tool_changed(self):
        value = self.tool.currentData()
        if value:
            self.pause()
            self._pin_draft()
            if not self.draft:
                self.tool.setCurrentIndex(0)
                return
            self.return_to_draft()
            if self.scope.currentIndex() == 0:
                self.scope.setCurrentIndex(1)
        self.picture.tool = value
        self.save_button.setEnabled(not value and not self._saving)
        sync_drawing_controls(self)

    def _begin_drawing(self):
        self.pause()
        self._pin_draft()

    def _drawing_changed(self):
        cut = self.edit.at(self.frame)
        if (self.draft and not self._loading_form and cut
                and cut.shot["id"] == self.draft["cut"].shot["id"]
                and self.edit.timeline.get("id") == self.draft["timeline"]
                and cut.media.get("version") == self.draft["version"]):
            self.draft["annotations"] = self.picture.annotation()
        self.save_button.setEnabled(not self.picture.tool and not self._saving)
        sync_drawing_controls(self)

    def choose_colour(self):
        from PyQt6.QtGui import QColor
        colour = QColorDialog.getColor(QColor(self.picture.color), self, "Drawing colour")
        if colour.isValid():
            self.set_colour(colour.name())

    def set_colour(self, color):
        self.picture.color = color
        sync_drawing_controls(self)

    def clear_drawing(self):
        self.picture.clear_drawing()
        self.tool.setCurrentIndex(0)
        if self.draft:
            self.draft["annotations"] = None

    def redo_drawing(self):
        self.return_to_draft()
        self.picture.redo()

    def done_drawing(self):
        self.tool.setCurrentIndex(0)
        self.right.show()
        self.left.setCurrentIndex(0)
        self.left.show()
        self.form_scroll.ensureWidgetVisible(self.title)
        self.title.setFocus()

    def _sync_drawing_context(self):
        if self.picture._stroke is not None:
            if self.draft:
                self.draft["annotations"] = self.picture.annotation()
            self.picture._stroke = None
        cut = self.edit.at(self.frame)
        draft_here = (self.draft and cut and self.draft["timeline"] == self.edit.timeline.get("id")
                      and self.draft["cut"].shot["id"] == cut.shot["id"]
                      and self.draft["version"] == cut.media.get("version"))
        show = False
        if draft_here:
            try:
                ref = self._reference()
            except ValueError:
                ref = None
            working = cut.working_frame(self.frame, self.draft.get("task"))
            show = ref is None or (ref[0] <= working <= ref[1] if isinstance(ref, list) else ref == working)
        annotation = self.draft.get("annotations") if show else None
        self.picture.shapes = deepcopy((annotation or {}).get("shapes", []))
        if not draft_here:
            self.picture.tool = None
            self.tool.blockSignals(True)
            self.tool.setCurrentIndex(0)
            self.tool.blockSignals(False)
            self.save_button.setEnabled(not self._saving)
        self.picture.saved_shapes = []
        if self.selected_task and cut:
            shot_id, task = self.selected_task
            target, clamped = cut.task_frame(task)
            ref = task.get("frame_reference")
            end = (target + ref[1] - ref[0] if isinstance(ref, list) else
                   cut.end - 1 if ref is None else target)
            if (shot_id == cut.shot["id"] and not clamped and target <= self.frame <= end
                    and task.get("reviewed_version") in (None, cut.media.get("version"))
                    and not (self.draft and (self.draft.get("task") or {}).get("id") == task["id"])):
                self.picture.saved_shapes = deepcopy((task.get("annotations") or {}).get("shapes", []))
        self.picture.update()
        sync_drawing_controls(self)

    def return_to_draft(self):
        if not self.draft:
            return
        if self.edit.timeline.get("id") != self.draft["timeline"]:
            self.status.setText("Select the draft's original timeline, then choose Return to draft shot.")
            return
        cut = self.edit.by_id(self.draft["cut"].shot["id"])
        if not cut:
            self.status.setText("The draft shot is no longer in this timeline.")
            return
        self.pause()
        desired = self.draft["version"]
        if desired is not None:
            media = next((m for m in cut.shot.get("versions", []) if m["version"] == desired), None)
            if media is None:
                self.status.setText(f"Draft preview v{desired:03} is unavailable. The draft remains attached to it.")
                return
            cut.shot["preview"] = media
        self.seek(self.draft["frame"])

    def _form_snapshot(self):
        return {"draft": deepcopy(self.draft), "title": self.title.text(), "notes": self.notes.toPlainText(),
                "scope": self.scope.currentIndex(), "start": self.range_start.value(), "end": self.range_end.value(),
                "status": self.task_status.currentData(), "artist": self.artist.currentData(),
                "budget": self.budget.value(), "priority": self.priority.value()}

    def _restore_form(self, data):
        self._loading_form = True
        self.draft = deepcopy(data.get("draft"))
        self.title.setText(data.get("title", ""))
        self.notes.setPlainText(data.get("notes", ""))
        self.scope.setCurrentIndex(data.get("scope", 0))
        self.range_start.setValue(data.get("start", 1001))
        self.range_end.setValue(data.get("end", 1001))
        for combo, value in ((self.task_status, data.get("status", "unassigned")), (self.artist, data.get("artist"))):
            if combo.findData(value) < 0:
                combo.addItem(str(value), value)
            combo.setCurrentIndex(max(0, combo.findData(value)))
        self.budget.setValue(float(data.get("budget") or 0))
        self.priority.setValue(data.get("priority", 5))
        self.tool.setCurrentIndex(0)
        self.picture.tool = None
        self.picture.redo_shapes = []
        self._loading_form = False
        self.range_start.setEnabled(self.scope.currentIndex() in (1, 2, 3))
        self.range_end.setEnabled(self.scope.currentIndex() == 3)
        self.save_button.setText("Save changes" if self.draft and self.draft.get("task") else "Create Task")
        self._draft_caption()
        self._sync_drawing_context()

    def cancel_task(self):
        if self._saving:
            return
        restore = self.suspended_draft
        self.suspended_draft = None
        self._restore_form(restore or {})

    def new_task_at(self, cut):
        if self.draft:
            self.status.setText("Save or clear the existing draft before starting another task.")
            return
        self.scrub(cut.start)
        self._pin_draft()
        self.right.show()
        self.title.setFocus()

    def edit_task(self, cut, task):
        if self._saving:
            return
        if self.draft and self.draft.get("task"):
            self.status.setText("Save or cancel the task being edited first.")
            return
        self.suspended_draft = self._form_snapshot() if self.draft else None
        self.pause()
        frame, _ = cut.task_frame(task)
        ref = task.get("frame_reference")
        draft = {"cut": deepcopy(cut), "timeline": self.edit.timeline.get("id"), "frame": frame,
                 "version": task.get("reviewed_version") if task.get("reviewed_version") is not None else cut.media.get("version"), "task": deepcopy(task),
                 "annotations": deepcopy(task.get("annotations")), "server": http_help.DjangoAPI._default_base_url}
        self._restore_form({"draft": draft, "title": task["title"], "notes": task.get("notes") or "",
                            "scope": 3 if isinstance(ref, list) else 1 if type(ref) is int else 0,
                            "start": ref[0] if isinstance(ref, list) else ref if type(ref) is int else cut.working_frame(frame, task),
                            "end": ref[1] if isinstance(ref, list) else cut.working_frame(cut.end - 1, task),
                            "status": task.get("status"), "artist": task.get("artist"),
                            "budget": task.get("budget_hours"), "priority": task.get("priority", 5)})
        self.return_to_draft()
        self.right.show()
        self.left.setCurrentIndex(0)
        self.left.show()

    def save_task(self):
        if self._saving or self.picture.tool:
            return
        self._pin_draft()
        if not self.draft or not self.title.text().strip():
            self.status.setText("Select a shot and enter a task name.")
            return
        try:
            reference = self._reference()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        draft = deepcopy(self.draft)
        payload = {"title": self.title.text().strip(), "notes": self.notes.toPlainText(),
                   "frame_reference": reference, "reviewed_version": draft["version"],
                   "annotations": draft.get("annotations"), "status": self.task_status.currentData(),
                   "artist": self.artist.currentData(), "budget_hours": f"{self.budget.value():.2f}",
                   "priority": self.priority.value()}
        api = http_help.DjangoAPI(base_url=draft["server"])
        self._saving = True
        self._revision += 1
        self.form_scroll.widget().setEnabled(False)
        self.task_details.setEnabled(False)
        self.status.setText("Saving task…")
        def done(saved):
            self._saving = False
            self.form_scroll.widget().setEnabled(True)
            self.task_details.setEnabled(True)
            self._put_task(draft["cut"].shot["id"], saved)
            restore = self.suspended_draft
            self.suspended_draft = None
            self._restore_form(restore or {})
            self.status.setText("Task saved.")
        def failed(message):
            self._saving = False
            self.form_scroll.widget().setEnabled(True)
            self.task_details.setEnabled(True)
            self.status.setText(f"Task was not saved: {message}. Your draft is retained.")
        task = draft.get("task")
        self._submit(lambda: api.update_task(task["id"], **payload) if task else
                     api.create_task(shot_id=draft["cut"].shot["id"], **payload), done, error=failed)

    def _put_task(self, shot_id, task):
        self._merge_editor_remote(task)
        cut = self.edit.by_id(shot_id)
        if cut:
            tasks = cut.shot.setdefault("tasks", [])
            previous = next((t for t in tasks if t["id"] == task["id"]), None)
            if previous is not None:
                previous.update(task)
            else:
                tasks.append(task)
            if self.selected_task and self.selected_task[1]["id"] == task["id"]:
                self.selected_task = (shot_id, task)
            self._render_comments()
            self.strip.update()
            self._sync_drawing_context()

    def _merge_editor_remote(self, task):
        original = (self.draft or {}).get("task")
        if not original or original["id"] != task["id"]:
            return
        self._loading_form = True
        for field, combo in (("status", self.task_status), ("artist", self.artist)):
            if combo.currentData() == original.get(field):
                index = combo.findData(task.get(field))
                if index >= 0:
                    combo.setCurrentIndex(index)
            original[field] = task.get(field)
        self._loading_form = False

    def select_task(self, cut, task):
        self.pause()
        self.selected_task = (cut.shot["id"], task)
        target, clamped = cut.task_frame(task)
        self.seek(target)
        self.status.setText("Referenced frame is outside this cut; showing the nearest visible frame." if clamped else
                            f"{task['title']} · {task.get('frame_reference') or 'Whole shot'}")
        self._render_comments()

    def load_task_version(self, cut, task):
        version = task.get("reviewed_version")
        media = next((m for m in cut.shot.get("versions", []) if m["version"] == version), None)
        if media is None:
            self.status.setText(f"Reviewed version v{version} is not available on this workstation.")
            return
        cut.shot["preview"] = media
        self.select_task(cut, task)

    def _render_comments(self, *_args):
        scroll = self.comments.verticalScrollBar().value()
        content = QWidget()
        content.setObjectName("review_comment_content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 14, 12, 14)
        layout.setSpacing(12)
        active = self.edit.at(self.frame)
        count = 0
        for cut in self.edit.cuts:
            if self.comment_filter.currentIndex() == 0 and cut is not active:
                continue
            for task in cut.shot.get("tasks", []):
                if task.get("hidden"):
                    continue
                count += 1
                layout.addWidget(task_card(self, cut, task, STATUSES))
        layout.addStretch()
        old = self.comments.takeWidget()
        self.comments.setWidget(content)
        if old:
            old.deleteLater()
        self.comments.verticalScrollBar().setValue(scroll)
        self.comment_count.setText(str(count))

    def _quick_field(self, shot_id, task, field, combo, _index):
        if task["id"] in self._pending_tasks:
            return
        api = http_help.DjangoAPI()
        value = combo.currentData()
        self._pending_tasks.add(task["id"])
        self._revision += 1
        self._render_comments()
        def ready(saved):
            self._pending_tasks.discard(task["id"])
            self._put_task(shot_id, saved)
            self.status.setText("Task updated.")
        def failed(message):
            self._pending_tasks.discard(task["id"])
            self._render_comments()
            self.status.setText(f"Task update failed: {message}")
        self._submit(lambda: api.update_task(task["id"], **{field: value}), ready, error=failed)

    def delete_task(self, cut, task):
        if task["id"] in self._pending_tasks:
            return
        if QMessageBox.question(self, "Delete task", f"Delete ‘{task['title']}’?") != QMessageBox.StandardButton.Yes:
            return
        api = http_help.DjangoAPI()
        self._pending_tasks.add(task["id"])
        self._revision += 1
        self._render_comments()
        def done(_):
            self._pending_tasks.discard(task["id"])
            current = self.edit.by_id(cut.shot["id"])
            if current:
                current.shot["tasks"] = [t for t in current.shot.get("tasks", []) if t["id"] != task["id"]]
            if self.selected_task and self.selected_task[1]["id"] == task["id"]:
                self.selected_task = None
            self._render_comments()
            self._sync_drawing_context()
            self.strip.update()
            self.status.setText("Task deleted.")
        def failed(message):
            self._pending_tasks.discard(task["id"])
            self._render_comments()
            self.status.setText(f"Delete failed: {message}")
        self._submit(lambda: api.delete_task(task["id"]), done, error=failed)

    def _mark_shot_dirty(self, *_args):
        if self._details_id is not None:
            self._shot_dirty = True
            if not hasattr(self, "_shot_drafts"):
                self._shot_drafts = {}
            self._shot_drafts[self._details_id] = {"notes": self.shot_notes.toPlainText(), "colour_code": self.shot_colour.text()}

    def _show_shot(self, cut):
        self._details_id = None
        shot = cut.shot if cut else {}
        pending = getattr(self, "_shot_drafts", {}).get(shot.get("id"), {})
        self.shot_title.setText(shot.get("title", "No shot selected"))
        self.shot_notes.setPlainText(pending.get("notes", shot.get("notes") or ""))
        self.shot_colour.setText(pending.get("colour_code", shot.get("colour_code") or "None"))
        self.metadata.setPlainText(json.dumps({k: v for k, v in shot.items() if k not in ("tasks", "versions", "preview")}, indent=2, default=str))
        self.visibility_button.setText("Show shot (H)" if shot.get("hidden") else "Hide shot (H)")
        self._details_id = shot.get("id")
        self._shot_dirty = bool(pending)

    def save_shot(self):
        cut = self.edit.at(self.frame)
        if not cut:
            return
        payload = {"notes": self.shot_notes.toPlainText(), "colour_code": self.shot_colour.text()}
        if not payload["colour_code"].strip():
            self.status.setText("Enter a colour code, or None.")
            return
        self._patch_shot(cut, payload)

    def toggle_hidden(self):
        cut = self.edit.at(self.frame)
        if cut:
            self._patch_shot(cut, {"hidden": not cut.shot.get("hidden", False)})

    def _patch_shot(self, cut, payload):
        self._revision += 1
        api = http_help.DjangoAPI()
        shot_id = cut.shot["id"]
        def ready(saved):
            current = self.edit.by_id(shot_id)
            if current:
                for key in payload:
                    current.shot[key] = saved.get(key, payload[key])
                pending = getattr(self, "_shot_drafts", {})
                if pending.get(shot_id) == payload:
                    pending.pop(shot_id, None)
                if self._details_id == shot_id:
                    self._show_shot(current)
                self.strip.update()
            self.status.setText("Shot saved.")
        self._submit(lambda: api.update_shot(shot_id, **payload), ready)

    def refresh_tasks(self):
        timeline_id = self.edit.timeline.get("id")
        if not timeline_id or self._polling or not self.isVisible() or self._pending_tasks or self._saving:
            return
        self._polling = True
        ticket = self._load_ticket
        revision = self._revision
        api = http_help.DjangoAPI()
        def ready(data):
            self._polling = False
            if ticket != self._load_ticket or revision != self._revision or self._saving or self._pending_tasks:
                return
            changed = False
            for shot in data.get("shots", []):
                cut = self.edit.by_id(shot["id"])
                if not cut:
                    continue
                if cut.shot.get("tasks") != shot.get("tasks"):
                    cut.shot["tasks"] = shot.get("tasks", [])
                    for task in cut.shot["tasks"]:
                        self._merge_editor_remote(task)
                    changed = True
                for key in ("notes", "colour_code", "hidden"):
                    cut.shot[key] = shot.get(key)
            if self.selected_task:
                cut = self.edit.by_id(self.selected_task[0])
                task = next((t for t in cut.shot.get("tasks", []) if t["id"] == self.selected_task[1]["id"]), None) if cut else None
                self.selected_task = (cut.shot["id"], task) if task else None
            if changed:
                self._render_comments()
                self.strip.update()
                self._sync_drawing_context()
            self._show_shot(self.edit.at(self.frame))
        def failed(message):
            self._polling = False
            self.status.setText(f"Task refresh failed: {message}. Local playback and drafts are retained.")
        self._submit(lambda: api.get_timeline(timeline_id), ready, error=failed)

    def reset_server(self):
        self.pause()
        self._epoch += 1
        self._load_ticket += 1
        self._polling = self._saving = False
        self._pending_tasks.clear()
        self.form_scroll.widget().setEnabled(True)
        self.task_details.setEnabled(True)
        self.suspended_draft = None
        self._restore_form({})
        self.selected_task = None
        self._shot_drafts = {}
        self.jobs = []
        self._initial_job_id = None
        for combo in (self.job_combo, self.timeline_combo):
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)
        self.edit = Edit()
        self.strip.edit = self.edit
        self._invalidate_media()
        self.picture.set_image(QImage())
        self.picture.message = "Loading server…"
        self._started = False
        if self.isVisible():
            self._started = True
            self.refresh()

    def shutdown(self):
        if self._closed:
            return
        self.pause()
        if self._presentation:
            self._presentation.close()
        self._poll_timer.stop()
        self._load_cancel.set()
        self.decoder.cancel()
        self._submit(self.decoder.close, pool=self._media_pool)
        self._closed = True
