"""Folder-based timeline importer, sharing XML Import's shot creation pipeline."""
from __future__ import annotations

from os.path import abspath
from pathlib import Path
import re

from PyQt6.QtCore import QEvent, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSpinBox, QTableWidgetItem,
    QTextEdit, QWidget,
)

from import_xml_v2 import XMLImportPage
from colourspace_defaults import COLOURSPACE_LIST
from timeline_import import Cancelled, Discovery, RAW_RELINK_HANDLES, MEDIA_EXTENSIONS, candidate_media_paths, discover, fingerprint, prepare_import, probe_media, read_timeline
from timeline_reference_finder import calculate_reference_start, offline_roots, find_references


class ReadWorker(QThread):
    """Cooperative cancellation for scanning/probing; no filesystem writes."""
    result = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, work, parent=None):
        super().__init__(parent)
        self.work = work

    def run(self):
        try:
            value = self.work(self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.result.emit(value)
        except Cancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class ImportTimelinePage(XMLImportPage):
    def __init__(self, parent=None):
        self.discovery = None
        self.media = {}
        self.document = None
        self.prepared = None
        self.path_overrides = {}
        self.offset_overrides = {}
        self.accepted_handles = {}
        self.excluded = set()
        self.colourspace_overrides = {}
        self._read_worker = None
        self._read_cancelled = False
        self._pending_timeline = None
        self._busy = False
        self._refreshing = False
        self._raw_media_root = ''
        self._managed_handle_settings = None
        self._online_reference_info = None
        super().__init__(parent)
        self.create_tasks = False
        self.checkBoxCreateTasks.setChecked(False)
        self.setAcceptDrops(True)
        # Capture URL drops over child editors/table viewports as well as the
        # page background. Separate dialogs keep their own drop behavior.
        QApplication.instance().installEventFilter(self)
        QApplication.instance().aboutToQuit.connect(self._shutdown_workers)

    def _create_ui(self):
        super()._create_ui()
        self.detect_colourspace_check = QCheckBox('Detect input transform from metadata')
        self.detect_colourspace_check.setChecked(True)
        self.colourspaceComboBox.setEnabled(False)
        self.colourspace_detection_label = QLabel('Detected after media is loaded')
        self.colourspace_detection_label.setWordWrap(True)
        colour_row = self.layout().itemAt(1).layout()
        colour_row.insertWidget(0, self.detect_colourspace_check)
        colour_row.insertWidget(3, self.colourspace_detection_label)
        self.detect_colourspace_check.toggled.connect(self._colourspace_mode_changed)
        self.colourspaceComboBox.currentTextChanged.connect(self._manual_colourspace_changed)
        self.detect_handles_check = QCheckBox('Detect handles from media')
        self.detect_handles_check.setChecked(True)
        self.detect_handles_check.setToolTip('Use the measured head/tail frames available on every layer. Counts remain editable per shot.')
        self.spinboxHandles.setEnabled(False)
        self.spinboxHandles.setToolTip(
            'Frames before and after the cut. Raw relinks default to 10 at each end. '
            'The Nuke working range starts at 1001 and includes the cut plus these handles. '
            'Disable automatic detection to edit this value.')
        self.layout().itemAt(2).layout().insertWidget(0, self.detect_handles_check)
        self.detect_handles_check.toggled.connect(self._handle_mode_changed)
        self.btnSelectXML.setText('Select Folder')
        self.btnNukeAll.setText('Import Timeline')
        self.btnShotTable.setText('Refresh Preview')
        self.btnMakeThumbs.hide()  # The shared import worker makes cut thumbnails.
        self.layout().insertWidget(0, QLabel(
            'Drop a media-managed folder anywhere here, or select one. '
            'Review local matches, timing and available handles before creating shots.'))
        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText('Media-managed folder')
        folder_row.addWidget(self.folder_edit)
        self.cancel_scan_button = QPushButton('Cancel scan')
        self.cancel_scan_button.setEnabled(False)
        self.cancel_scan_button.clicked.connect(self._cancel_read)
        folder_row.addWidget(self.cancel_scan_button)
        self.layout().insertLayout(1, folder_row)
        loading_row = QHBoxLayout()
        self.loading_label = QLabel()
        self.loading_label.setWordWrap(True)
        self.loading_folder_label = QLabel()
        self.loading_folder_label.setWordWrap(True)
        self.loading_folder_label.setTextFormat(Qt.TextFormat.PlainText)
        self.loading_folder_label.hide()
        loading_row.addWidget(self.loading_label, 1)
        self.loading_progress = QProgressBar()
        self.loading_progress.setRange(0, 0)
        self.loading_progress.setTextVisible(False)
        self.loading_progress.setMaximumWidth(180)
        loading_row.addWidget(self.loading_progress)
        self.loading_label.hide()
        self.loading_progress.hide()
        self.layout().insertLayout(2, loading_row)
        timeline_row = QHBoxLayout()
        timeline_row.addWidget(QLabel('Timeline:'))
        self.timeline_name_edit = QLineEdit()
        self.timeline_name_edit.setPlaceholderText("Name for the imported timeline")
        self.timeline_name_edit.textChanged.connect(self._timeline_name_changed)
        self.timeline_combo = QComboBox()
        timeline_row.addWidget(self.timeline_combo, 1)
        self.layout().insertLayout(3, timeline_row)
        action_row = QHBoxLayout()
        self.accept_handles_button = QPushButton('Reset to detected handles')
        self.accept_handles_button.clicked.connect(self._accept_available_handles)
        action_row.addWidget(self.accept_handles_button)
        self.layout().insertLayout(self.layout().count() - 1, action_row)
        self.validation_text = QTextEdit()
        self.validation_text.setReadOnly(True)
        self.validation_text.setMaximumHeight(140)
        self.layout().addWidget(self.validation_text)
        self.tableWidgetTemp.setColumnCount(14)
        self.tableWidgetTemp.setHorizontalHeaderLabels([
            'Import', 'Shot / track', 'Original reference', 'Local media', 'Match',
            'Timeline source', 'Local cut start (0-based)', 'Speed', 'Cut frames',
            'Detected head / tail', 'Use head / tail', 'Timeline edit in / out', 'Status',
            'Input transform / IDT',
        ])
        self.tableWidgetTemp.horizontalHeaderItem(11).setToolTip(
            'Timeline-relative edit frames, with an exclusive out point, matching XML Import V2. '
            'Nuke uses its own cut range inside the handles.')
        self.tableWidgetTemp.setWordWrap(True)
        self.timeline_combo.currentIndexChanged.connect(self._timeline_changed)
        self.spinboxHandles.valueChanged.connect(self._handles_changed)
        self.folder_edit.returnPressed.connect(self._on_select_xml)
        self.raw_folder_edit = QLineEdit()
        self.raw_folder_button = QPushButton('Select Raw Folder')
        self.raw_media_status = QLabel('No raw media folder selected')
        self.raw_media_status.setWordWrap(True)
        self.raw_folder_button.clicked.connect(self._select_raw_folder)
        self.raw_folder_edit.returnPressed.connect(self._select_raw_folder)
        self.online_reference_widget = QWidget()
        self.online_reference_widget.setAcceptDrops(True)
        self.online_reference_edit = QLineEdit()
        self.online_reference_edit.setPlaceholderText('Optional full reference edit — drop a video here')
        self.online_reference_button = QPushButton('Browse Reference')
        self.online_reference_clear = QPushButton('Clear')
        self.online_reference_find = QPushButton('Find Reference')
        self.online_reference_find.setToolTip('Find the matching edit in Exports/Offline, preferring the XML date folder.')
        self.online_reference_find.clicked.connect(self._find_reference)
        self.online_reference_matches = QComboBox()
        self.online_reference_matches.setMinimumWidth(0)
        self.online_reference_matches.hide()
        self.online_reference_matches.activated.connect(self._choose_found_reference)
        self.online_reference_start = QSpinBox()
        self._reference_start_manual = False
        self.online_reference_start.setRange(-100000000, 100000000)
        self.online_reference_start.setToolTip(
            'Timeline frame corresponding to the first frame of the reference movie. '
            'Use 0 for a full edit; a negative value accounts for a leading slate.')
        self.online_reference_status = QLabel('Reference cut points follow the timeline; handles align at frame 1001.')
        self.online_reference_status.setWordWrap(True)
        self.online_reference_button.clicked.connect(self._select_online_reference)
        self.online_reference_edit.returnPressed.connect(self._select_online_reference)
        self.online_reference_edit.textEdited.connect(self._reference_path_edited)
        self.online_reference_clear.clicked.connect(self._clear_online_reference)
        self.online_reference_start.valueChanged.connect(self._reference_start_changed)
        from timeline_import_ui import arrange_timeline_page
        arrange_timeline_page(self)

    def _set_review_status(self, text, tone='neutral'):
        self.import_status_label.setText(text)
        self.import_status_label.setProperty('tone', tone)
        self.import_status_label.style().unpolish(self.import_status_label)
        self.import_status_label.style().polish(self.import_status_label)

    def eventFilter(self, watched, event):
        if (event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop)
                and isinstance(watched, QWidget)
                and (watched is self or self.isAncestorOf(watched))
                and watched.window() is self.window()
                and event.mimeData().hasUrls()):
            urls = event.mimeData().urls()
            folder = urls[0].toLocalFile() if len(urls) == 1 and urls[0].isLocalFile() else ''
            raw_drop = watched is self.raw_media_card or self.raw_media_card.isAncestorOf(watched)
            reference_drop = watched is self.online_reference_widget or self.online_reference_widget.isAncestorOf(watched)
            try:
                valid = bool(folder) and (Path(folder).is_dir() or
                    (Path(folder).is_file() and Path(folder).suffix.lower() == '.xml'))
                if reference_drop:
                    valid = bool(folder) and Path(folder).is_file() and Path(folder).suffix.lower() in MEDIA_EXTENSIONS
            except (OSError, ValueError):
                valid = False
            if not self.isEnabled() or self._busy or not valid:
                event.ignore()
                return True
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            if event.type() == QEvent.Type.Drop:
                if reference_drop:
                    self._load_online_reference(folder)
                elif Path(folder).is_file():
                    self._load_xml_file(folder)
                elif raw_drop:
                    self._load_raw_folder(folder)
                else:
                    self._load_folder(folder)
            return True
        return super().eventFilter(watched, event)

    def _start_read(self, work, callback, message='Reading timeline/media metadata…', on_failure=None):
        if self._read_worker and self._read_worker.isRunning():
            return
        self._busy = True
        self._read_cancelled = False
        self._set_read_controls(False)
        self.validation_text.setPlainText(message)
        self.loading_label.setText(message)
        self.loading_folder_label.clear()
        self.loading_folder_label.setToolTip('')
        self.loading_label.show()
        self.loading_progress.show()
        self.cancel_scan_button.show()
        self._set_review_status('Reading timeline and media…')
        worker = ReadWorker(work, self)
        self._read_worker = worker
        worker.result.connect(callback)
        worker.progress.connect(self._show_scan_folder)
        worker.failed.connect(on_failure or self._read_failed)
        worker.finished.connect(self._read_finished)
        worker.start()

    def _show_scan_folder(self, folder):
        if self._read_cancelled or not self._busy:
            return
        self.loading_folder_label.setText(folder)
        self.loading_folder_label.setToolTip(folder)
        self.loading_folder_label.show()

    def _scan_media_folder(self, folder, cancel, *, include_timelines=True, document=None):
        progress = self._read_worker.progress.emit
        found = discover(folder, cancel, include_timelines=include_timelines, progress=progress)
        media = {}
        last_folder = None
        paths = candidate_media_paths(document, found.media) if document else found.media
        for path in paths:
            current_folder = str(Path(path).parent)
            if current_folder != last_folder:
                progress(current_folder)
                last_folder = current_folder
            media[path] = probe_media(path, cancel)
        return found, media

    def _set_read_controls(self, enabled):
        for widget in (self.btnSelectXML, self.folder_edit, self.timeline_combo, self.timeline_name_edit,
                       self.updateBtn, self.accept_handles_button, self.tableWidgetTemp,
                       self.detect_colourspace_check, self.raw_folder_edit, self.raw_folder_button,
                       self.online_reference_widget):
            widget.setEnabled(enabled)
        self.colourspaceComboBox.setEnabled(enabled and not self.detect_colourspace_check.isChecked())
        self.cancel_scan_button.setEnabled(not enabled)
        self.btnNukeAll.setEnabled(False)

    def _cancel_read(self):
        if self._read_worker:
            if self.raw_media_status.text().startswith('Scanning raw media'):
                self.raw_media_status.setText('Raw media scan cancelled. Previous media selection retained.')
            self._read_cancelled = True
            self._read_worker.requestInterruption()
            self.loading_label.setText('Cancelling…')
            self.cancel_scan_button.setEnabled(False)

    def _read_finished(self):
        # QThread resets its interruption flag before this queued slot runs.
        cancelled = self._read_cancelled
        self._busy = False
        self._set_read_controls(True)
        self.loading_label.hide()
        self.loading_progress.hide()
        self.loading_folder_label.hide()
        self.cancel_scan_button.hide()
        if cancelled:
            self._pending_timeline = None
            self.validation_text.setPlainText('Scan cancelled. Select a folder/timeline to continue.')
            self.prepared = None
            self._set_review_status('Scan cancelled')
        else:
            preferred = self._pending_timeline
            self._pending_timeline = None
            if preferred:
                self.timeline_combo.setCurrentIndex(self.timeline_combo.findData(preferred))
            elif self.document:
                self._refresh_preview()

    def _read_failed(self, message):
        self.prepared = None
        self.document = None
        self.validation_text.setPlainText(message)
        self._set_review_status('Unable to read timeline', 'error')
        self.btnNukeAll.setEnabled(False)

    def _on_select_xml(self):
        folder = self.folder_edit.text().strip()
        if self.sender() is not self.btnSelectXML and Path(folder).is_file() and Path(folder).suffix.lower() == '.xml':
            self._load_xml_file(folder)
            return
        if self.sender() is self.btnSelectXML or not folder:
            folder = QFileDialog.getExistingDirectory(self, 'Select media-managed folder', folder)
        if not folder:
            return
        self._load_folder(folder)

    def _load_xml_file(self, path):
        """Load only the dropped XML; media can be linked during review."""
        if self._busy or not self.isEnabled():
            return
        self._set_destination_from_folder(Path(abspath(path)).parent)
        path = str(Path(path).resolve())
        self.folder_edit.setText(path)
        self._pending_timeline = None
        self.discovery = Discovery(str(Path(path).parent), timelines=[path])
        if not self._raw_media_root:
            self.media = {}
        self.timeline_combo.blockSignals(True)
        self.timeline_combo.clear()
        self.timeline_combo.addItem(Path(path).name, path)
        self.timeline_combo.setCurrentIndex(0)
        self.timeline_combo.blockSignals(False)
        self._timeline_changed()

    def _reference_path_edited(self):
        self._online_reference_info = None
        self._refresh_preview()

    def _choose_found_reference(self, index):
        path = self.online_reference_matches.itemData(index)
        if path:
            self._load_online_reference(path)

    def _reference_search_result(self, result):
        paths, info = result
        self.online_reference_matches.clear()
        self.online_reference_matches.hide()
        if info is not None:
            self.online_reference_edit.setText(info.path)
            self._online_reference_info = info
            self.online_reference_status.setText(info.error or f'Found matching reference: {Path(info.path).name}')
        elif paths:
            self.online_reference_matches.addItem('Multiple matching exports — choose a reference', None)
            for path in paths:
                self.online_reference_matches.addItem(str(Path(path).parent.name + '/' + Path(path).name), path)
                self.online_reference_matches.setItemData(self.online_reference_matches.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
            self.online_reference_matches.show()
            self.online_reference_status.setText('Multiple matching reference exports found. Choose one below.')
        else:
            self.online_reference_status.setText('No exact filename match found. Use Browse Reference to choose an edit.')

    @staticmethod
    def _search_reference(path, roots, cancel):
        paths = find_references(path, roots, cancel)
        return paths, probe_media(paths[0], cancel) if len(paths) == 1 else None

    def _find_reference(self):
        if self._busy or self.document is None:
            return
        path = self.document.path
        roots = offline_roots(path, self.parser.project_root)
        if not roots:
            root = QFileDialog.getExistingDirectory(self, 'Choose Offline reference export folder')
            if not root:
                return
            roots = [root]
        def loaded(result):
            self._reference_search_result(result)
            self._refresh_preview()
        self._start_read(lambda cancel: self._search_reference(path, roots, cancel), loaded,
                         'Finding reference edit…',
                         on_failure=lambda error: self.online_reference_status.setText(error))

    def _reference_start_changed(self):
        self._reference_start_manual = True
        self._refresh_preview()

    def _clear_online_reference(self):
        self.online_reference_edit.clear()
        self._online_reference_info = None
        self.online_reference_status.setText('No online reference selected')
        self._refresh_preview()

    def _select_online_reference(self):
        path = self.online_reference_edit.text().strip()
        if self.sender() is self.online_reference_button or not path:
            path, _ = QFileDialog.getOpenFileName(self, 'Select online reference edit', path,
                                                  'Video (*.mov *.mp4 *.mxf *.avi *.mkv);;All files (*)')
        if path:
            self._load_online_reference(path)

    def _load_online_reference(self, path):
        if self._busy:
            return
        path = str(Path(path).resolve())
        self.online_reference_edit.setText(path)
        self._online_reference_info = None
        def loaded(info):
            self._online_reference_info = info
            self.online_reference_status.setText(info.error or f'{info.frames} frames · {info.fps:g} fps')
            self._refresh_preview()
        self._start_read(lambda cancel: probe_media(path, cancel), loaded, 'Reading online reference…',
                         on_failure=lambda error: self.online_reference_status.setText(error))

    def _select_raw_folder(self):
        folder = self.raw_folder_edit.text().strip()
        if self.sender() is self.raw_folder_button or not folder:
            folder = QFileDialog.getExistingDirectory(self, 'Select raw media folder', folder)
        if folder:
            self._load_raw_folder(folder)

    def _load_raw_folder(self, folder):
        if self._busy or not self.isEnabled():
            return
        folder = str(Path(folder).resolve())
        self.raw_media_status.setText('Scanning raw media, including subfolders…')
        def work(cancel):
            return self._scan_media_folder(folder, cancel, include_timelines=False, document=self.document)
        def loaded(value):
            found, self.media = value
            if not self._raw_media_root:
                self._managed_handle_settings = (self.detect_handles_check.isChecked(), self.spinboxHandles.value())
                self._set_handle_preferences(False, RAW_RELINK_HANDLES)
            self._raw_media_root = folder
            self.raw_folder_edit.setText(folder)
            self._reset_choices()
            self.raw_media_status.setText(
                f'{len(found.media)} media files found; {len(self.media)} checked for timeline matches.'
                + ('\n' + '\n'.join(found.warnings) if found.warnings else ''))
            if self.document:
                self._refresh_preview()
            else:
                self._set_review_status('Raw media ready — drop an XML timeline')
        self._start_read(work, loaded, 'Scanning raw media and reading metadata…',
                         on_failure=lambda message: self.raw_media_status.setText(f'Unable to load raw media: {message}'))

    def _load_folder(self, folder):
        if self._busy or not self.isEnabled():
            return
        if self._managed_handle_settings is not None:
            self._set_handle_preferences(*self._managed_handle_settings)
            self._managed_handle_settings = None
        self._raw_media_root = ''
        self.raw_folder_edit.clear()
        self.raw_media_status.setText('No raw media folder selected')
        self.folder_edit.setText(folder)
        self._set_destination_from_folder(Path(abspath(folder)))
        self._pending_timeline = None
        self.document = self.prepared = None
        self.shots = []
        self.tableWidgetTemp.setRowCount(0)
        self.review_stack.setCurrentIndex(0)
        self.review_summary_label.setText('No timeline loaded')
        self._reset_choices()
        def work(cancel):
            return self._scan_media_folder(folder, cancel)
        self._start_read(work, self._folder_loaded, 'Scanning folder and reading media metadata…')

    def _folder_loaded(self, value):
        self.discovery, self.media = value
        self.timeline_combo.blockSignals(True)
        self.timeline_combo.clear()
        self.timeline_combo.addItem('Choose a timeline…', None)
        paths = sorted(self.discovery.timelines, key=lambda p: ({'.drt': 0, '.xml': 1, '.otio': 2}.get(Path(p).suffix.lower(), 3), p.casefold()))
        for path in paths:
            self.timeline_combo.addItem(str(Path(path).relative_to(self.discovery.folder)), path)
        self.timeline_combo.blockSignals(False)
        self.btnSelecRoot.setEnabled(True)
        self.btnShotTable.setEnabled(True)
        xmls = [path for path in paths if Path(path).suffix.lower() == '.xml']
        drts = [path for path in paths if Path(path).suffix.lower() == '.drt']
        # Prefer DRT only when the other exports represent the same named edit.
        if drts:
            preferred = drts[0] if len(drts) == 1 and len({Path(p).stem.casefold() for p in paths}) == 1 else None
        else:
            preferred = xmls[0] if len(xmls) == 1 else paths[0] if len(paths) == 1 else None
        if preferred:
            # A fast scan may already have emitted finished before this queued
            # result is handled; let the permanent completion slot select it.
            self._pending_timeline = preferred
        elif not self.discovery.timelines:
            self.validation_text.setPlainText('No supported DRT/XML/OTIO timelines found.')
            self._set_review_status('No supported timeline found', 'warning')
        else:
            self.validation_text.setPlainText(f'{len(self.discovery.media)} media files found. Choose one timeline.\n' + '\n'.join(self.discovery.warnings))
            self._set_review_status('Choose a timeline to continue')

    def _reset_choices(self):
        self.path_overrides.clear()
        self.offset_overrides.clear()
        self.accepted_handles.clear()
        self.excluded.clear()
        self.colourspace_overrides.clear()

    def _timeline_changed(self):
        path = self.timeline_combo.currentData()
        self.document = self.prepared = None
        self.shots = []
        self.tableWidgetTemp.setRowCount(0)
        self.review_stack.setCurrentIndex(0)
        self.review_summary_label.setText('No timeline loaded')
        self._reset_choices()
        if path:
            self._load_documents()
        else:
            self.btnNukeAll.setEnabled(False)

    def _load_documents(self):
        path = self.timeline_combo.currentData()
        if not path:
            return
        self.document = None
        self.shots = []
        self.tableWidgetTemp.setRowCount(0)
        self.review_stack.setCurrentIndex(0)
        self.prepared = None
        message = f'Parsing {Path(path).suffix[1:].upper()} timeline: {Path(path).name}'
        raw_root = self._raw_media_root
        reference_roots = (offline_roots(path, self.parser.project_root)
                           if not self.online_reference_edit.text().strip() else [])
        def work(cancel):
            document = read_timeline(path)
            scanned = self._scan_media_folder(raw_root, cancel, include_timelines=False, document=document) if raw_root else None
            reference = self._search_reference(path, reference_roots, cancel) if reference_roots else None
            return document, scanned, reference
        def loaded(value):
            document, scanned, reference = value
            if reference is not None:
                self._reference_search_result(reference)
            if scanned is not None:
                found, self.media = scanned
                self.raw_media_status.setText(
                    f'{len(found.media)} media files found; {len(self.media)} checked for timeline matches.')
            self._documents_loaded(document)
        self._start_read(
            work, loaded, message)

    def _documents_loaded(self, value):
        self.document = value
        self.btnSelecRoot.setEnabled(True)
        self.btnShotTable.setEnabled(True)
        self.timeline_name_edit.setText(self.document.name)
        self._refresh_preview()

    def _timeline_name_changed(self):
        self.parser.edit_name = self.timeline_name_edit.text().strip()
        self._refresh_preview()

    def _validated_timeline_name(self):
        name = self.timeline_name_edit.text().strip()
        if not name:
            raise ValueError('Enter a timeline name in Import settings.')
        if (name in ('.', '..') or name.endswith('.')
                or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in name)):
            raise ValueError('Timeline name must be a valid folder name without path separators.')
        return name

    def _on_show_table(self):
        self._refresh_preview()

    def _handles_changed(self):
        self.accepted_handles.clear()
        self._refresh_preview()

    def _set_handle_preferences(self, detected, count):
        """Switch workflow defaults without rebuilding an intermediate preview."""
        self.detect_handles_check.blockSignals(True)
        self.spinboxHandles.blockSignals(True)
        self.detect_handles_check.setChecked(detected)
        self.spinboxHandles.setValue(count)
        self.detect_handles_check.blockSignals(False)
        self.spinboxHandles.blockSignals(False)
        self.spinboxHandles.setEnabled(not detected)
        self.accept_handles_button.setText('Reset to detected handles' if detected else 'Accept available handles for all shots')

    def _handle_mode_changed(self, detected):
        self.spinboxHandles.setEnabled(not detected)
        self.accept_handles_button.setText('Reset to detected handles' if detected else 'Accept available handles for all shots')
        self.accepted_handles.clear()
        self._refresh_preview()

    def _colourspace_mode_changed(self, automatic):
        self.colourspaceComboBox.setEnabled(not automatic)
        self.colourspace_overrides.clear()
        self._refresh_preview()

    def _manual_colourspace_changed(self):
        if not self.detect_colourspace_check.isChecked():
            self._refresh_preview()

    def _colourspace_chosen(self, uid, value):
        if value:
            self.colourspace_overrides[uid] = value
        else:
            self.colourspace_overrides.pop(uid, None)
        self._refresh_preview()

    def _show_colourspace_detection(self):
        choices = [row.input_colourspace for row in self.prepared.rows if row.input_colourspace]
        if not self.detect_colourspace_check.isChecked():
            self.colourspace_detection_label.setText('Manual input transform for all clips')
            return
        if choices:
            self.colourspaceComboBox.setCurrentText(choices[0])
            self.colourspace = choices[0]
        if any(not row.input_colourspace for row in self.prepared.rows):
            text = 'Some input transforms are unknown; choose per clip or switch to manual'
        elif len(set(choices)) > 1:
            text = 'Mixed input transforms; see per-clip IDTs'
        else:
            text = '; '.join(dict.fromkeys('Manual per-clip transform override' if row.clip.uid in self.colourspace_overrides
                                          else row.colourspace_detection.reason for row in self.prepared.rows
                                          if row.colourspace_detection))
        self.colourspace_detection_label.setText(text)

    def _refresh_preview(self):
        if self._refreshing:
            return
        if self.document is None:
            self.btnNukeAll.setEnabled(False)
            return
        self._refreshing = True
        try:
            self.handles = self.spinboxHandles.value()
            self.prepared = prepare_import(
                self.document, self.media, path_overrides=self.path_overrides,
                offset_overrides=self.offset_overrides,
                handles=None if self.detect_handles_check.isChecked() else self.handles,
                accepted_handles=self.accepted_handles, excluded=self.excluded,
                auto_colourspace=self.detect_colourspace_check.isChecked(),
                relink_raw=bool(self._raw_media_root),
                colourspace_overrides=self.colourspace_overrides,
                manual_colourspace=self.colourspaceComboBox.currentText())
            try:
                self.parser.edit_name = self._validated_timeline_name()
            except ValueError as exc:
                self.prepared.errors.append(str(exc))
            self.shots = self.prepared.shots
            self._apply_generated_shot_names()
            reference = self.online_reference_edit.text().strip()
            if reference:
                info = self._online_reference_info
                if info is None:
                    self.prepared.errors.append('Load the online reference with Enter or Browse Reference.')
                elif info.error:
                    self.prepared.errors.append('Online reference: ' + info.error)
                elif abs(info.fps - self.document.fps) > 0.000001:
                    self.prepared.errors.append('Online reference frame rate differs from timeline.')
                else:
                    if not self._reference_start_manual:
                        inferred_start = calculate_reference_start(self.document, info.frames)
                        self.online_reference_start.blockSignals(True)
                        self.online_reference_start.setValue(inferred_start)
                        self.online_reference_start.blockSignals(False)
                        self.online_reference_status.setText(
                            f'{info.frames} frames · {info.fps:g} fps · '
                            f'Auto-aligned to timeline frame {inferred_start}'
                            if inferred_start else f'{info.frames} frames · {info.fps:g} fps')
                    start = self.online_reference_start.value()
                    if self._reference_start_manual:
                        self.online_reference_status.setText(
                            f'{info.frames} frames · {info.fps:g} fps · Manual timeline start: {start}')
                    for shot in self.shots:
                        if shot.edit_inpoint < start or shot.edit_outpoint > start + info.frames:
                            self.prepared.errors.append(
                                f'{shot.name}: online reference does not cover the cut '
                                f'({shot.edit_inpoint}–{shot.edit_outpoint}); reference covers '
                                f'{start}–{start + info.frames}. Check Reference starts at timeline frame.')
                        shot.online_reference = info.path
                        shot.online_reference_timing = dict(frames=info.frames, timeline_start=start)
            self._show_colourspace_detection()
            self._populate_review()
            self.timeline_preview.set_timeline(
                self.document, self.shots, self._online_reference_info,
                self.online_reference_start.value())
            warnings = list(self.discovery.warnings if self.discovery else []) + self.document.warnings
            self.review_summary_label.setText(
                f'{len(self.shots)} shots · {len(self.prepared.rows)} clips · {Path(self.document.path).suffix[1:].upper()}')
            messages = [f'{len(self.shots)} included shots, {len(self.prepared.rows)} media occurrences. Timing from {Path(self.document.path).name}.']
            if self.detect_handles_check.isChecked():
                messages.append('Handles detected from local ranges. Working media starts at 1001; edit in/out exclude handles. Shared files may contain additional usable media; reduce counts per shot if needed.')
            messages += self.prepared.errors
            if not self.parser.project_root:
                messages.append('Choose the destination using Change Project Path.')
            elif not self.prepared.errors:
                messages.append('All included shots validated. Ready to import.')
            messages += list(dict.fromkeys(warnings))
            self.validation_text.setPlainText('\n'.join(messages))
            self.btnNukeAll.setEnabled(not self._busy and not self.prepared.errors and bool(self.parser.project_root))
            if self.prepared.errors:
                self._set_review_status('Review required — resolve the issues below', 'warning')
            elif not self.parser.project_root:
                self._set_review_status('Choose a destination to continue')
            else:
                self._set_review_status('All included shots validated · Ready to import', 'success')
        except Exception as exc:
            self.prepared = None
            self.shots = []
            self.btnNukeAll.setEnabled(False)
            self.validation_text.setPlainText(str(exc))
            self._set_review_status('Validation failed — review the details below', 'error')
        finally:
            self._refreshing = False

    def _validated_shot_name(self, name):
        name = super()._validated_shot_name(name)
        if name in ('.', '..') or any(character in name for character in '/\\\x00:*?"<>|'):
            raise ValueError('Shot names must be valid folder names without path separators')
        return name

    def _on_select_root(self):
        super()._on_select_root()
        self._refresh_preview()

    def _set_destination_from_folder(self, folder):
        project = next((parent for parent in (folder, *folder.parents)
                        if re.match(r'[Cc][0-9]{4}(?![0-9])', parent.name)), None)
        self.parser.project_root = str(project) if project else ''
        self.lableRoot.setText(f'Project: {project}' if project else 'Project: (click Change Project Path)')

    def _populate_review(self):
        table = self.tableWidgetTemp
        # Include excluded rows so their checkboxes can restore them.
        clips = self.document.clips
        rows = {r.clip.uid: r for r in self.prepared.rows}
        primaries = [c for c in clips if c.track == 1]
        indices = {c.timeline_start: i for i, c in enumerate(primaries)}
        table.setRowCount(len(clips))
        self.review_stack.setCurrentIndex(1 if clips else 0)
        table.verticalHeader().setDefaultSectionSize(65)
        for number, clip in enumerate(clips):
            for column in range(table.columnCount()):
                table.removeCellWidget(number, column)
                table.setItem(number, column, QTableWidgetItem(''))
            index = indices[clip.timeline_start]
            row = rows.get(clip.uid)
            if clip.track == 1:
                include = QCheckBox()
                include.setChecked(index not in self.excluded)
                include.toggled.connect(lambda checked, i=index: self._include_shot(i, checked))
                table.setCellWidget(number, 0, include)
            table.setItem(number, 1, QTableWidgetItem(f'{self._shot_name(index + 1)} / V{clip.track}'))
            reference = QTableWidgetItem(clip.reference or clip.file_name)
            reference.setToolTip(clip.reference or clip.file_name)
            table.setItem(number, 2, reference)
            if row is None:
                table.setItem(number, 12, QTableWidgetItem('Excluded'))
                continue
            path_widget = QWidget()
            layout = QHBoxLayout(path_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            paths = QComboBox()
            paths.addItem('Choose local media…', None)
            for path in dict.fromkeys(([row.match.path] if row.match.path else []) + row.match.candidates):
                paths.addItem(f'{Path(path).name} — {Path(path).parent}', path)
                paths.setItemData(paths.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
            if row.match.path:
                paths.setCurrentIndex(paths.findData(row.match.path))
            paths.currentIndexChanged.connect(lambda _, uid=clip.uid, combo=paths: self._path_chosen(uid, combo.currentData()))
            layout.addWidget(paths)
            browse = QPushButton('Browse')
            browse.clicked.connect(lambda _, uid=clip.uid: self._browse_media(uid))
            layout.addWidget(browse)
            table.setCellWidget(number, 3, path_widget)
            table.setItem(number, 4, QTableWidgetItem(row.match.reason))
            table.setItem(number, 5, QTableWidgetItem(Path(row.timing_source).name))
            offset_widget = QWidget()
            offset_layout = QHBoxLayout(offset_widget)
            offset_layout.setContentsMargins(0, 0, 0, 0)
            offset = QDoubleSpinBox()
            offset.setDecimals(6)
            offset.setRange(-1, 1_000_000_000)
            offset.setSpecialValueText('Unknown')
            offset.setValue(row.local_cut_start if row.local_cut_start is not None else -1)
            original_offset = offset.value()
            offset.editingFinished.connect(lambda uid=clip.uid, spin=offset, original=original_offset:
                                           self._offset_chosen(uid, spin.value()) if spin.value() != original else None)
            offset_layout.addWidget(offset)
            reset = QPushButton('Auto')
            reset.clicked.connect(lambda _, uid=clip.uid: self._reset_offset(uid))
            offset_layout.addWidget(reset)
            table.setCellWidget(number, 6, offset_widget)
            table.setItem(number, 7, QTableWidgetItem(f'{clip.speed:.9g}'))
            table.setItem(number, 8, QTableWidgetItem(str(clip.duration)))
            table.setItem(number, 9, QTableWidgetItem(f'{row.available_head} / {row.available_tail}'))
            if clip.track == 1:
                widget = QWidget()
                layout = QHBoxLayout(widget)
                layout.setContentsMargins(0, 0, 0, 0)
                detected = self.prepared.detected_handles[index]
                accepted = self.accepted_handles.get(index, detected if self.detect_handles_check.isChecked() else (self.handles, self.handles))
                spins = []
                for side, value in enumerate(accepted):
                    spin = QSpinBox()
                    spin.setRange(0, max(detected[side], value) if self.detect_handles_check.isChecked() else self.handles)
                    spin.setValue(value)
                    spins.append(spin)
                    layout.addWidget(spin)
                for spin in spins:
                    spin.editingFinished.connect(lambda i=index, controls=spins, original=accepted:
                                                 self._accept_handles(i, tuple(s.value() for s in controls))
                                                 if tuple(s.value() for s in controls) != original else None)
                table.setCellWidget(number, 10, widget)
            shot = next((s for s in self.shots if s.index == index), None)
            if shot:
                table.setItem(number, 11, QTableWidgetItem(f'{shot.edit_inpoint} / {shot.edit_outpoint}'))
            status = QTableWidgetItem('; '.join(row.errors) or 'Matched')
            status.setForeground(QColor('#f1b384' if row.errors else '#8bd5b0'))
            status.setToolTip(status.text())
            table.setItem(number, 12, status)
            if self.detect_colourspace_check.isChecked():
                transform = QComboBox()
                detection = row.colourspace_detection
                transform.addItem(f'Auto: {detection.colourspace if detection and detection.colourspace else "Not identified"}', None)
                for choice in COLOURSPACE_LIST:
                    transform.addItem(choice, choice)
                if clip.uid in self.colourspace_overrides:
                    transform.setCurrentIndex(transform.findData(self.colourspace_overrides[clip.uid]))
                transform.setToolTip(detection.reason if detection else 'Choose local media first')
                transform.currentIndexChanged.connect(lambda _, uid=clip.uid, combo=transform: self._colourspace_chosen(uid, combo.currentData()))
                table.setCellWidget(number, 13, transform)
            else:
                table.setItem(number, 13, QTableWidgetItem(row.input_colourspace or ''))
        for col, width in ((0, 50), (1, 110), (2, 220), (3, 350), (4, 150), (5, 310), (6, 200), (7, 90), (8, 75), (9, 120), (10, 150), (11, 140), (12, 220), (13, 260)):
            table.setColumnWidth(col, width)

    def _include_shot(self, index, checked):
        if self._refreshing:
            return
        self.excluded.discard(index) if checked else self.excluded.add(index)
        self._refresh_preview()

    def _path_chosen(self, uid, path):
        if path:
            self.path_overrides[uid] = path
        else:
            self.path_overrides.pop(uid, None)
        self.accepted_handles.clear()
        self.colourspace_overrides.pop(uid, None)
        self._refresh_preview()

    def _browse_media(self, uid):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose local media', self.discovery.folder)
        if not path:
            return
        path = str(Path(path).resolve())
        def loaded(info):
            self.media[path] = info
            self.path_overrides[uid] = path
            self.accepted_handles.clear()
            self.colourspace_overrides.pop(uid, None)
        self._start_read(lambda cancel: probe_media(path, cancel), loaded)

    def _offset_chosen(self, uid, value):
        if value >= 0 and not self._refreshing:
            self.offset_overrides[uid] = value
            self.accepted_handles.clear()
            self._refresh_preview()

    def _reset_offset(self, uid):
        self.offset_overrides.pop(uid, None)
        self.accepted_handles.clear()
        self._refresh_preview()

    def _accept_handles(self, index, values):
        if self._refreshing:
            return
        self.accepted_handles[index] = values
        self._refresh_preview()

    def _accept_available_handles(self):
        if not self.prepared:
            return
        for index in {r.shot_index for r in self.prepared.rows}:
            detected = self.prepared.detected_handles[index]
            self.accepted_handles[index] = detected if self.detect_handles_check.isChecked() else tuple(min(self.handles, side) for side in detected)
        self._refresh_preview()

    def _on_import_all(self):
        self._on_update_settings()
        self._refresh_preview()
        if not self.prepared or self.prepared.errors or not self.parser.project_root:
            return
        # Shared worker accepts a preflight, validating every input before writes.
        from import_xml_v2 import ImportWorker, LoadingDialog
        from PyQt6.QtCore import QThread
        self._import_dialog = LoadingDialog(self, 'Importing Timeline')
        self._import_dialog.show()
        self._import_worker = ImportWorker(
            self.shots, self.parser.project_root, self.parser.edit_name,
            self.colourspace, self.handles, self.local_work, self.add_to_db,
            self.create_tasks, self.skip_activity_logging, self._current_nuke_path(),
            preflight=self._preflight)
        self._import_thread = QThread(self)
        self._import_worker.moveToThread(self._import_thread)
        self._import_thread.started.connect(self._import_worker.run)
        self._import_worker.progress.connect(self._import_dialog.update_progress)
        self._import_worker.finished.connect(self._on_import_finished)
        self._import_worker.error.connect(self._on_import_error)
        self._import_worker.finished.connect(self._import_thread.quit)
        self._import_worker.error.connect(self._import_thread.quit)
        self._import_thread.finished.connect(lambda: self.setEnabled(True))
        self.setEnabled(False)
        self._import_thread.start()

    def _preflight(self):
        self.prepared.revalidate()
        if self._online_reference_info is not None and self.online_reference_edit.text().strip():
            info = self._online_reference_info
            if fingerprint(info.path) != info.signature:
                raise ValueError('Online reference changed since review. Load it again.')
        # A pre-existing script may describe different plates or timing. Do not
        # let the legacy skip-existing behavior silently accept a stale script.
        for shot in self.shots:
            scripts = Path(self.parser.project_root, 'VFX', self.parser.edit_name, shot.name, 'scripts')
            if scripts.exists() and any(scripts.glob('*.nk')):
                raise ValueError(f'{shot.name}: scripts already exist at {scripts}. Choose another destination or shot prefix.')

    def _on_import_finished(self, stats):
        self._import_dialog.close()
        self.validation_text.setPlainText(
            f'Imported {stats.shots_processed} shots. Created {stats.nuke_scripts_created} Nuke scripts.\n' + '\n'.join(stats.errors))
        if stats.errors:
            QMessageBox.warning(self, 'Import completed with errors', '\n'.join(stats.errors))

    def _on_import_error(self, error):
        self._import_dialog.close()
        self.validation_text.setPlainText(error)
        QMessageBox.warning(self, 'Import failed', error)

    def closeEvent(self, event):
        self._shutdown_workers()
        super().closeEvent(event)

    def _shutdown_workers(self):
        self.timeline_preview.shutdown()
        if self._read_worker and self._read_worker.isRunning():
            self._read_worker.requestInterruption()
            self._read_worker.wait()
        if self._import_thread and self._import_thread.isRunning():
            # The import worker emits quit on completion. Request exit directly
            # as well so shutdown does not depend on queued GUI delivery.
            self._import_thread.quit()
            self._import_thread.wait()
