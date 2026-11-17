"""Workstation-only offline reference overlay; never writes backend/media files."""
import math
from pathlib import Path

import av
from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QSlider, QSpinBox, QWidget

from path_mapping import convert_path
from timeline_review_media import MovieDecoder


def probe_reference(path):
    path = Path(path)
    stat = path.stat()
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.codec_context is None:
            raise ValueError('Video codec unavailable; choose a MOV or MP4 reference.')
        fps = float(stream.average_rate or stream.base_rate or 0)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('Reference has no usable video frame rate.')
        duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                    else float(container.duration or 0) / av.time_base)
        frames = stream.frames or round(duration * fps)
        if frames <= 0:
            raise ValueError('Reference has no usable video frames.')
        return {'path': str(path), 'identity': (stat.st_mtime_ns, stat.st_size),
                'fps': fps, 'frame_count': frames}


def reference_frame(frame, start, timeline_fps, media, offset=0):
    """Positive offset places reference frame zero later in the editorial timeline."""
    index = math.floor((frame - start - offset) * media['fps'] / timeline_fps + 1e-7)
    return index if 0 <= index < media['frame_count'] else None


class ReferenceOverlay:
    def __init__(self, page, layout):
        self.page = page
        self.media = None
        self._timeline_reference = None
        self.pool = QThreadPool(page)
        self.pool.setMaxThreadCount(1)
        self.decoder = self._decoder()
        self._generation = 0
        self._load_ticket = 0
        self._busy = False
        self._target = None
        self._displayed = None
        self._pending_primary = None
        self._failed = None
        self.waiting = False
        row_widget = QWidget()
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        page._button('Offline ref…', self.browse, row)
        self.enabled = QCheckBox('Reference')
        row.addWidget(self.enabled)
        row.addWidget(QLabel('Opacity'))
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(0, 100)
        self.opacity.setValue(50)
        self.opacity.setMaximumWidth(100)
        row.addWidget(self.opacity)
        self.opacity_label = QLabel('50%')
        row.addWidget(self.opacity_label)
        row.addWidget(QLabel('Offset'))
        self.offset = QSpinBox()
        self.offset.setRange(-1000000, 1000000)
        self.offset.setSuffix(' fr')
        self.offset.setToolTip('Timeline frames from edit start to reference frame zero. Positive delays the reference; negative skips its leader.')
        row.addWidget(self.offset)
        self.remove_button = page._button('Remove ref', self.clear, row)
        row.addStretch()
        layout.addWidget(row_widget)
        comparison = QHBoxLayout()
        comparison.addWidget(QLabel('Compare'))
        self.mode = QComboBox()
        self.mode.addItems(['Overlay', 'Wipe'])
        self.mode.setToolTip('W: toggle wipe. Drag the wipe centre to move; drag its outer handle to rotate.')
        comparison.addWidget(self.mode)
        comparison.addWidget(QLabel('Angle'))
        self.angle = QSpinBox()
        self.angle.setRange(-180, 180)
        self.angle.setSuffix('°')
        comparison.addWidget(self.angle)
        self.swap = page._button('Swap sides', self._swap_wipe, comparison)
        self.reset = page._button('Reset wipe', self._reset_wipe, comparison)
        comparison.addStretch()
        layout.addLayout(comparison)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.angle.valueChanged.connect(self._angle_changed)
        page.picture.wipe_changed.connect(self._sync_wipe_angle)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.enabled.toggled.connect(self._toggle)
        self.opacity.valueChanged.connect(self._opacity_changed)
        self.offset.valueChanged.connect(self._offset_changed)
        self._controls(False)

    @staticmethod
    def _decoder():
        decoder = MovieDecoder()
        decoder.limit = 64 * 1024**2
        return decoder

    def _controls(self, loaded):
        for widget in (self.enabled, self.offset, self.remove_button, self.mode):
            widget.setEnabled(loaded)
        self.opacity.setEnabled(loaded and not self.page.picture.reference_wipe)
        for widget in (self.angle, self.swap, self.reset):
            widget.setEnabled(loaded and self.page.picture.reference_wipe)

    def toggle_wipe(self):
        if self.mode.isEnabled():
            self.mode.setCurrentIndex(1 - self.mode.currentIndex())

    def _mode_changed(self):
        self.page.picture.reference_wipe = self.mode.currentIndex() == 1
        self._controls(self.media is not None)
        self.page.picture.update()
        self.request()

    def _angle_changed(self, value):
        self.page.picture.wipe_angle = value
        self.page.picture.update()

    def _sync_wipe_angle(self):
        self.angle.blockSignals(True)
        self.angle.setValue(round(self.page.picture.wipe_angle))
        self.angle.blockSignals(False)

    def _swap_wipe(self):
        self.page.picture.wipe_swapped = not self.page.picture.wipe_swapped
        self.page.picture.update()

    def _reset_wipe(self):
        from PyQt6.QtCore import QPointF
        self.page.picture.wipe_centre = QPointF(.5, .5)
        self.page.picture.wipe_angle = 0.
        self.page.picture.wipe_swapped = False
        self._sync_wipe_angle()
        self.page.picture.update()

    def _message(self, message):
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def follow_timeline(self, timeline):
        """Use the saved path once per timeline/path change; preserve manual overrides."""
        path = str(timeline.get('online_reference') or '').strip()
        key = (timeline.get('id'), path)
        if key == self._timeline_reference:
            return
        self._timeline_reference = key
        self.clear()
        if path:
            self.load(convert_path(path))

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self.page, 'Choose offline reference movie',
            self.media['path'] if self.media else '', 'Movies (*.mov *.mp4 *.mxf *.m4v *.avi *.mkv);;All files (*)')
        if path:
            self.load(path)

    def load(self, path):
        self._load_ticket += 1
        ticket = self._load_ticket
        self._message('Loading offline reference…')
        def ready(media):
            if ticket != self._load_ticket:
                return
            self.clear_cache()
            self.media = media
            self._controls(True)
            self.offset.blockSignals(True)
            self.offset.setValue(0)
            self.offset.blockSignals(False)
            self.enabled.setChecked(True)
            self.status.setToolTip(media['path'])
            self._message(Path(media['path']).name)
            self.request()
        def failed(error):
            if ticket == self._load_ticket:
                self._message(f'Could not load offline reference: {error}')
        self.page._submit(lambda: probe_reference(path), ready, pool=self.pool, error=failed)

    def _opacity_changed(self, value):
        self.page.picture.reference_opacity = value / 100
        self.opacity_label.setText(f'{value}%')
        self.page.picture.update()
        self.request()

    def _offset_changed(self):
        self._hide()
        self.request()

    def _toggle(self):
        if not self.enabled.isChecked():
            self.clear_cache()
        self.request()

    def present_primary(self, image):
        # Keep the previous complete picture until its matching reference is ready.
        self._pending_primary = (self.page._display_key, image)
        self.request()

    def _commit_primary(self):
        pending, self._pending_primary = self._pending_primary, None
        if pending is not None and pending[0] == self.page._requested_key:
            self.page.picture.set_image(pending[1])

    def _hide(self):
        self.page.picture.reference_image = QImage()
        self.page.picture.update()
        self._displayed = None
        self.waiting = False
        self._commit_primary()

    def clear_cache(self):
        self._generation += 1
        self.decoder.cancel()
        self.page._submit(self.decoder.close, pool=self.pool)
        self.decoder = self._decoder()
        self._busy = False
        self._target = self._failed = None
        self._hide()

    def clear(self):
        self._load_ticket += 1
        self.clear_cache()
        self.media = None
        self.enabled.blockSignals(True)
        self.enabled.setChecked(False)
        self.enabled.blockSignals(False)
        self._controls(False)
        self.status.setToolTip('')
        self._message('')

    def request(self):
        page = self.page
        if not self.media or not self.enabled.isChecked() or (not self.opacity.value() and not page.picture.reference_wipe) or not page.edit.cuts:
            self._target = None
            self._hide()
            return
        index = reference_frame(page.frame, page.edit.start, page.edit.fps, self.media, self.offset.value())
        if index is None:
            self._target = None
            self._hide()
            self._message(f"{Path(self.media['path']).name} · Outside reference range")
            return
        key = (index, page.resolution.currentData())
        self._target = key
        if key == self._displayed:
            self.waiting = False
            self._commit_primary()
            return
        if key == self._failed:
            self._hide()
            return
        self.waiting = True
        # Never blend a new reference position with an older primary frame.
        if page._display_key != page._requested_key or self._busy or key == self._failed:
            return
        self._busy = True
        generation, decoder, media = self._generation, self.decoder, self.media
        def ready(image):
            if generation != self._generation:
                return
            self._busy = False
            if key == self._target and image is not None:
                page.picture.reference_image = image
                self._displayed = key
                self.waiting = False
                self._commit_primary()
                self._message(f"{Path(media['path']).name} · Reference frame {index} · {media['fps']:g} fps")
                page.picture.update()
            else:
                self.request()
        def failed(error):
            if generation != self._generation:
                return
            self._busy = False
            if key == self._target:
                self._failed = key
                self._hide()
                self._message(f'Offline reference unavailable: {error}')
            else:
                self.request()
        self.page._submit(lambda: decoder.read(media, index, key[1]), ready, pool=self.pool, error=failed)

    def shutdown(self):
        self._load_ticket += 1
        self._generation += 1
        self.decoder.cancel()
        self.page._submit(self.decoder.close, pool=self.pool)
