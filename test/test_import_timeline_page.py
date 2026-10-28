from __future__ import annotations

import os
from pathlib import Path
import sys
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt6.QtCore import QElapsedTimer, QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QCheckBox, QDialog, QLineEdit

from import_timeline_page import ImportTimelinePage
import import_xml_v2
from test_timeline_import import document, media_file, xml_text
from timeline_import import Discovery, read_timeline


@pytest.fixture(scope='session')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(app):
    widget = ImportTimelinePage()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def load(page, tmp_path):
    path, info = media_file(tmp_path)
    info.metadata = {'format.company_name': 'ARRI', 'format.product_name': 'ALEXA Mini LF'}
    page.discovery = Discovery(str(tmp_path), [path])
    page.media = {path: info}
    page.document = document(tmp_path)
    page.timeline_name_edit.setText(page.document.name)
    page.parser.project_root = str(tmp_path / 'destination')
    page._refresh_preview()


def wait_read(page, app):
    clock = QElapsedTimer()
    clock.start()
    while page._busy and clock.elapsed() < 5000:
        app.processEvents()
        QTest.qWait(5)
    app.processEvents()
    assert not page._busy


def drop_urls(target, urls):
    mime = QMimeData()
    mime.setUrls(urls)
    enter = QDragEnterEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, enter)
    drop = QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(target, drop)
    return enter.isAccepted(), drop.isAccepted()


def write_otio(path):
    otio = pytest.importorskip('opentimelineio')
    rt, tr = otio.opentime.RationalTime, otio.opentime.TimeRange
    reference = otio.schema.ExternalReference(target_url='/original/camera.mxf',
                                              available_range=tr(rt(900, 25), rt(200, 25)))
    reference.name = 'camera.mxf'
    clip = otio.schema.Clip(name='camera.mxf', media_reference=reference,
                            source_range=tr(rt(910.2219, 25), rt(20, 25)))
    timeline = otio.schema.Timeline(tracks=[otio.schema.Track(kind=otio.schema.TrackKind.Video, children=[clip])],
                                    global_start_time=rt(90000, 25))
    otio.adapters.write_to_file(timeline, str(path))


@pytest.mark.parametrize('target_name', ['background', 'folder', 'table', 'validation'])
def test_folder_drop_across_panel_starts_scan_and_xml_parsing(page, tmp_path, app, target_name):
    (tmp_path / 'edit.xml').write_text(xml_text())
    target = {'background': page, 'folder': page.folder_edit,
              'table': page.tableWidgetTemp.viewport(), 'validation': page.validation_text.viewport()}[target_name]
    assert drop_urls(target, [QUrl.fromLocalFile(str(tmp_path))]) == (True, True)
    wait_read(page, app)
    wait_read(page, app)
    assert page.folder_edit.text() == str(tmp_path)
    assert page.document.path == str(tmp_path / 'edit.xml')


def test_unique_xml_is_default_and_only_selected_timeline_is_parsed(page, tmp_path, app):
    (tmp_path / 'zzz.xml').write_text(xml_text())
    write_otio(tmp_path / 'aaa.otio')
    with mock.patch('import_timeline_page.read_timeline', wraps=read_timeline) as read:
        page._load_folder(str(tmp_path))
        wait_read(page, app)
        wait_read(page, app)
    read.assert_called_once_with(str(tmp_path / 'zzz.xml'))
    assert page.timeline_combo.currentData() == str(tmp_path / 'zzz.xml')
    assert page.document.path == str(tmp_path / 'zzz.xml')
    assert not hasattr(page, 'companion_combo')


def test_single_otio_still_loads_when_no_xml_is_present(page, tmp_path, app):
    write_otio(tmp_path / 'edit.otio')
    page._load_folder(str(tmp_path))
    wait_read(page, app)
    wait_read(page, app)
    assert page.document.path == str(tmp_path / 'edit.otio')


def test_folder_drop_rejects_files_multiple_folders_remote_urls_and_busy_scans(page, tmp_path):
    path = tmp_path / 'edit.xml'
    path.write_text(xml_text())
    page.folder_edit.setText('keep current folder')
    for urls in ([QUrl.fromLocalFile(str(path))], [QUrl.fromLocalFile(str(tmp_path))] * 2,
                 [QUrl('https://example.com/folder')]):
        assert drop_urls(page.folder_edit, urls) == (False, False)
        assert page.folder_edit.text() == 'keep current folder'
    page._busy = True
    assert drop_urls(page, [QUrl.fromLocalFile(str(tmp_path))]) == (False, False)
    assert page._read_worker is None
    page._busy = False


def test_folder_drop_does_not_override_separate_child_dialogs(page, tmp_path):
    dialog = QDialog(page)
    editor = QLineEdit(dialog)
    with mock.patch.object(page, '_load_folder') as load_folder:
        drop_urls(editor, [QUrl.fromLocalFile(str(tmp_path))])
    load_folder.assert_not_called()
    dialog.close()


def test_new_page_preserves_existing_settings_and_single_shot_action(page):
    assert page.btnSelectXML.text() == 'Select Folder'
    assert page.btnMakeShot.text() == 'Make Single Shot'
    assert page.spinboxHandles.value() == 25
    assert page.detect_handles_check.isChecked()
    assert not page.spinboxHandles.isEnabled()
    assert page.detect_colourspace_check.isChecked()
    assert page.checkBoxAddToDB.isChecked()
    assert page.checkBoxCreateTasks.isChecked()
    assert not page.btnNukeAll.isEnabled()


def test_camera_metadata_selects_input_transform_automatically(page, tmp_path):
    load(page, tmp_path)
    assert page.colourspaceComboBox.currentText() == 'ARRI LogC3 (EI800)'
    assert 'Inferred from ALEXA Mini LF' in page.colourspace_detection_label.text()
    assert not page.colourspaceComboBox.isEnabled()
    assert page.shots[0].primary_clip.media_timing['input_colourspace'] == 'ARRI LogC3 (EI800)'


def test_unknown_input_can_be_resolved_per_clip_or_manually(page, tmp_path):
    load(page, tmp_path)
    next(iter(page.media.values())).metadata = {}
    page._refresh_preview()
    assert not page.btnNukeAll.isEnabled()
    page._colourspace_chosen('1:0', 'ARRI LogC4')
    assert page.btnNukeAll.isEnabled()
    assert page.shots[0].primary_clip.media_timing['input_colourspace'] == 'ARRI LogC4'
    page.detect_colourspace_check.setChecked(False)
    page.colourspaceComboBox.setCurrentText('ACES - ACEScg')
    assert page.btnNukeAll.isEnabled()
    assert page.shots[0].primary_clip.media_timing['input_colourspace'] == 'ACES - ACEScg'


def test_shortages_need_explicit_acceptance(page, tmp_path):
    load(page, tmp_path)
    page.detect_handles_check.setChecked(False)
    assert not page.btnNukeAll.isEnabled()
    page._accept_available_handles()
    assert page.btnNukeAll.isEnabled()
    assert page.accepted_handles == {0: (10, 10)}
    assert page.shots[0].duration == 40


def test_handles_are_detected_and_used_by_default(page, tmp_path):
    load(page, tmp_path)
    assert page.btnNukeAll.isEnabled()
    assert (page.shots[0].head_handles, page.shots[0].tail_handles) == (10, 10)
    assert (page.shots[0].edit_inpoint, page.shots[0].edit_outpoint) == (0, 20)
    assert (page.shots[0].cut_first, page.shots[0].cut_last) == (1011, 1030)
    assert page.tableWidgetTemp.item(0, 11).text() == '0 / 20'
    assert page.accepted_handles == {}


def test_selecting_xml_or_otio_uses_that_files_own_timing(page, tmp_path, app):
    (tmp_path / 'edit.xml').write_text(xml_text())
    write_otio(tmp_path / 'edit.otio')
    _, info = media_file(tmp_path)
    info.metadata = {'format.company_name': 'ARRI', 'format.product_name': 'ALEXA Mini LF'}
    with mock.patch('import_timeline_page.probe_media', return_value=info):
        page._load_folder(str(tmp_path))
        wait_read(page, app)
        wait_read(page, app)
    page.parser.project_root = str(tmp_path / 'destination')
    for suffix, offset in (('xml', 10), ('otio', 10.2219), ('xml', 10)):
        selected = str(tmp_path / f'edit.{suffix}')
        page.timeline_combo.setCurrentIndex(page.timeline_combo.findData(selected))
        wait_read(page, app)
        page._refresh_preview()
        assert page.btnNukeAll.isEnabled(), page.validation_text.toPlainText()
        assert page.shots[0].primary_clip.media_timing['cut_start'] == pytest.approx(offset)
        assert all(row.timing_source == selected and row.alternative is None for row in page.prepared.rows)


def test_excluded_shot_can_be_restored(page, tmp_path):
    load(page, tmp_path)
    checkbox = page.tableWidgetTemp.cellWidget(0, 0)
    checkbox.setChecked(False)
    assert page.excluded == {0}
    assert not page.btnNukeAll.isEnabled()
    restored = page.tableWidgetTemp.cellWidget(0, 0)
    assert isinstance(restored, QCheckBox)
    restored.setChecked(True)
    assert not page.excluded
    assert len(page.prepared.rows) == 1


def test_missing_destination_blocks_import(page, tmp_path):
    load(page, tmp_path)
    page.parser.project_root = ''
    page._accept_available_handles()
    assert not page.btnNukeAll.isEnabled()
    assert 'destination' in page.validation_text.toPlainText()


def test_revised_handles_invalidate_shortage_acceptance(page, tmp_path):
    load(page, tmp_path)
    page.detect_handles_check.setChecked(False)
    page._accept_available_handles()
    page.spinboxHandles.setValue(5)
    assert page.accepted_handles == {}
    assert page.btnNukeAll.isEnabled()


def test_multiple_timelines_require_selection(page, tmp_path, app):
    (tmp_path / 'one.xml').write_text(xml_text())
    (tmp_path / 'two.xml').write_text(xml_text())
    page.folder_edit.setText(str(tmp_path))
    page._on_select_xml()
    wait_read(page, app)
    assert page.timeline_combo.currentData() is None
    assert page.document is None
    assert not page.btnNukeAll.isEnabled()
    page.timeline_combo.setCurrentIndex(1)
    wait_read(page, app)
    assert page.document.path == str(tmp_path / 'one.xml')


def test_single_timeline_is_selected_after_scan(page, tmp_path, app):
    (tmp_path / 'only.xml').write_text(xml_text())
    page.folder_edit.setText(str(tmp_path))
    page._on_select_xml()
    wait_read(page, app)
    # The second worker may have been queued by the first worker's completion.
    wait_read(page, app)
    assert page.document is not None
    assert page.document.path == str(tmp_path / 'only.xml')


def test_other_timelines_are_not_read_or_compared(page, tmp_path, app):
    (tmp_path / 'edit.xml').write_text(xml_text())
    (tmp_path / 'other.otio').write_text('{}')
    (tmp_path / 'another.otio').write_text('{}')
    page.folder_edit.setText(str(tmp_path))
    page._on_select_xml()
    wait_read(page, app)
    wait_read(page, app)
    assert page.document is not None
    assert page.document.path == str(tmp_path / 'edit.xml')
    assert all(row.alternative is None for row in page.prepared.rows)
    assert 'comparison' not in page.validation_text.toPlainText().lower()


def test_invalid_preview_cannot_launch_import_worker(page, tmp_path):
    load(page, tmp_path)
    page.media.clear()
    page._refresh_preview()
    with mock.patch.object(import_xml_v2, 'ImportWorker') as worker:
        page._on_import_all()
    worker.assert_not_called()


def test_preflight_blocks_existing_scripts(page, tmp_path):
    load(page, tmp_path)
    page._accept_available_handles()
    scripts = Path(page.parser.project_root, 'VFX', page.parser.edit_name, page.shots[0].name, 'scripts')
    scripts.mkdir(parents=True)
    (scripts / 'existing.nk').write_text('stale script')
    with mock.patch.object(page.prepared, 'revalidate'):
        with pytest.raises(ValueError, match='scripts already exist'):
            page._preflight()


def test_preflight_failure_happens_before_any_shot_writes(tmp_path):
    worker = import_xml_v2.ImportWorker([], str(tmp_path), 'edit', '', 25, False, False, False,
                                      preflight=mock.Mock(side_effect=ValueError('Input changed')))
    errors = []
    worker.error.connect(errors.append)
    worker.nuke_gen = mock.Mock()
    worker.run()
    assert errors == ['Input changed']
    worker.nuke_gen.create_shot_folder.assert_not_called()


def test_matching_drt_is_preferred(page, tmp_path, app):
    from test_drt_import import write_drt
    write_drt(tmp_path / 'edit.drt')
    (tmp_path / 'edit.xml').write_text(xml_text())
    page._load_folder(str(tmp_path))
    wait_read(page, app)
    wait_read(page, app)
    assert page.document.path == str(tmp_path / 'edit.drt')
    assert len(page.document.clips) == 27


def test_drt_with_different_edit_requires_selection(page, tmp_path, app):
    from test_drt_import import write_drt
    write_drt(tmp_path / 'edit.drt')
    (tmp_path / 'another.xml').write_text(xml_text())
    page._load_folder(str(tmp_path))
    wait_read(page, app)
    assert page.timeline_combo.currentData() is None
    assert page.document is None


def test_timeline_name_controls_import_destination_and_survives_refresh(page, tmp_path):
    load(page, tmp_path)
    original_name = page.document.name
    page.timeline_name_edit.setText('Client edit v2')
    page._on_update_settings()
    page._refresh_preview()
    assert page.parser.edit_name == 'Client edit v2'
    assert page.document.name == original_name
    scripts = Path(page.parser.project_root, 'VFX', 'Client edit v2', page.shots[0].name, 'scripts')
    scripts.mkdir(parents=True)
    (scripts / 'existing.nk').write_text('existing')
    with mock.patch.object(page.prepared, 'revalidate'):
        with pytest.raises(ValueError, match='scripts already exist'):
            page._preflight()


@pytest.mark.parametrize('name', ['', '   ', '../escape', 'bad/name', 'bad\\name'])
def test_invalid_timeline_name_blocks_import(page, tmp_path, name):
    load(page, tmp_path)
    page.timeline_name_edit.setText(name)
    assert page.prepared.errors
    assert not page.btnNukeAll.isEnabled()
    with mock.patch.object(import_xml_v2, 'ImportWorker') as worker:
        page._on_import_all()
    worker.assert_not_called()


def test_loading_timeline_initializes_editable_name(page, tmp_path):
    load(page, tmp_path)
    page.timeline_name_edit.setText('Custom name')
    replacement = document(tmp_path)
    replacement.name = 'Next edit'
    page._documents_loaded(replacement)
    assert page.timeline_name_edit.text() == 'Next edit'
    assert page.parser.edit_name == 'Next edit'
