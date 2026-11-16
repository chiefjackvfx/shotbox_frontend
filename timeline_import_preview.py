"""Import-time editorial preview using the shared Timeline Review surfaces."""
from PyQt6.QtCore import Qt, QThreadPool, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QPushButton, QLabel

from timeline_review_media import MovieDecoder, Work
from timeline_review_model import Edit, Cut, timecode
from timeline_review_widgets import ReviewPicture, TimelineStrip


class ImportTimelinePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.edit = Edit()
        self.reference = None
        self.reference_start = 0
        self.frame = 0
        self._closed = False
        self._work = None
        self._wanted = None
        self._displayed = None
        self.decoder = MovieDecoder()
        self.decoder.limit = 64 * 1024**2
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton()
        self.toggle.setObjectName('import_preview_toggle')
        self.toggle.setText('Timeline and reference preview')
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self._expand)
        layout.addWidget(self.toggle)
        self.body = QWidget()
        row = QHBoxLayout(self.body)
        row.setContentsMargins(0, 0, 0, 0)
        self.picture = ReviewPicture()
        self.picture.setMinimumHeight(200)
        self.picture.setMaximumHeight(260)
        self.picture.clicked.connect(self.play_pause)
        row.addWidget(self.picture, 1)
        right = QVBoxLayout()
        self.strip = TimelineStrip()
        self.strip.show_tasks = False
        self.strip.setFixedHeight(110)
        self.strip.seek.connect(self.seek)
        right.addWidget(self.strip)
        controls = QHBoxLayout()
        for title, callback in [('Previous frame', lambda: self.seek(self.frame - 1)),
                                ('Play', self.play_pause),
                                ('Next frame', lambda: self.seek(self.frame + 1)),
                                ('Fit picture', self.picture.fit)]:
            button = QPushButton(title)
            button.clicked.connect(callback)
            controls.addWidget(button)
            if title == 'Play':
                self.play_button = button
        right.addLayout(controls)
        self.status = QLabel('Load a timeline and choose a reference edit to preview.')
        self.status.setProperty('role', 'muted')
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        right.addStretch()
        row.addLayout(right, 3)
        layout.addWidget(self.body)
        self.body.hide()

    def set_timeline(self, document, shots, reference, start):
        self.pause()
        self.edit = Edit()
        names = {s.edit_inpoint: s.name for s in shots}
        if document:
            self.edit.fps = document.fps
            self.edit.cuts = [Cut(dict(id=c.uid, title=names.get(c.timeline_start) or c.name,
                                      preview={'label': 'Reference edit'} if reference else {}),
                                  c.timeline_start, c.duration, document.fps)
                              for c in sorted(document.clips, key=lambda c: c.timeline_start) if c.track == 1]
        self.edit.start = min((c.start for c in self.edit.cuts), default=0)
        self.edit.end = max((c.end for c in self.edit.cuts), default=0)
        self.strip.edit = self.edit
        self.reference = reference if reference and not reference.error else None
        self.reference_start = start
        self.seek(self.frame)

    def _expand(self, expanded):
        self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        if expanded:
            self.seek(self.frame)
        else:
            self.pause()
            self._wanted = None

    def pause(self):
        self.timer.stop()
        self.play_button.setText('Play')

    def play_pause(self):
        if self.timer.isActive():
            self.pause()
        elif self.reference and self.edit.cuts and self.body.isVisible():
            if self.frame >= self.edit.end - 1:
                self.seek(self.edit.start)
            self.play_button.setText('Pause')
            self.timer.start(max(1, round(1000 / self.edit.fps)))

    def _advance(self):
        # Wait for decoding instead of building a queue of obsolete frames.
        if self._work is not None:
            return
        if self.frame >= self.edit.end - 1:
            self.pause()
        else:
            self.seek(self.frame + 1)

    def seek(self, frame):
        self.frame = max(self.edit.start, min(max(self.edit.start, self.edit.end - 1), int(frame)))
        self.strip.frame = self.frame
        self.strip.update()
        cut = self.edit.at(self.frame)
        title = cut.shot['title'] if cut else 'Editorial gap'
        self.status.setText(f'{title} · Timeline frame {self.frame} · '
                            f'{timecode(self.frame - self.edit.start, self.edit.fps)}')
        info = self.reference
        index = self.frame - self.reference_start
        self._wanted = None
        if not info or not 0 <= index < info.frames or abs(info.fps - self.edit.fps) > 0.000001:
            self.pause()
            self._displayed = None
            self.picture.message = ('Choose a reference edit to preview.' if not info else
                                    'Reference unavailable at this timeline frame or frame rate.')
            self.picture.set_image(QImage())
            return
        if not self.body.isVisible() or self._closed:
            return
        self._wanted = (info.path, info.signature, index)
        self._request()

    def _request(self):
        if self._closed or self._work is not None or self._wanted is None or self._wanted == self._displayed:
            return
        path, identity, index = self._wanted
        media = dict(path=path, identity=identity, fps=self.reference.fps)
        decoder = self.decoder
        self._work = Work(self._wanted, lambda: decoder.read(media, index, 2))
        self._work.signals.finished.connect(self._ready, Qt.ConnectionType.QueuedConnection)
        self.pool.start(self._work)

    def _ready(self, key, image, error):
        self._work = None
        if self._closed:
            return
        if key == self._wanted:
            self._displayed = key
            if error:
                self.pause()
                self.picture.message = 'Unable to decode reference preview.'
                self.picture.set_image(QImage())
                self.status.setText(error)
            elif image is not None:
                self.picture.set_image(image)
        self._request()

    def hideEvent(self, event):
        self.pause()
        self._wanted = None
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.seek(self.frame)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self.pause()
        self.decoder.cancel()
        self.pool.waitForDone()
        # Cleanup runs on the same serial pool as all decoder access.
        cleanup = Work(None, self.decoder.close)
        self.pool.start(cleanup)
        self.pool.waitForDone()
