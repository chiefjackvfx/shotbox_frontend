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


@pytest.mark.parametrize('target_name', ['background', 'folder', 'table', 'validation'])
def test_xml_drop_loads_only_timeline_and_clears_previous_media(page, tmp_path, app, target_name):
    load(page, tmp_path)
    path = tmp_path / 'edit.XML'
    path.write_text(xml_text())
    page.path_overrides['old'] = 'old.mov'
    target = {'background': page, 'folder': page.folder_edit,
              'table': page.tableWidgetTemp.viewport(), 'validation': page.validation_text.viewport()}[target_name]
    with mock.patch('import_timeline_page.discover') as discover, mock.patch('import_timeline_page.probe_media') as probe:
        assert drop_urls(target, [QUrl.fromLocalFile(str(path))]) == (True, True)
        wait_read(page, app)
    discover.assert_not_called()
    probe.assert_not_called()
    assert page.document.path == str(path)
    assert page.timeline_combo.currentData() == str(path)
    assert page.media == {}
    assert page.path_overrides == {}
    assert page.prepared.rows
    assert not page.btnNukeAll.isEnabled()


def test_invalid_xml_drop_reports_error(page, tmp_path, app):
    path = tmp_path / 'bad.xml'
    path.write_text('<unrelated/>')
    assert drop_urls(page, [QUrl.fromLocalFile(str(path))]) == (True, True)
    wait_read(page, app)
    assert page.document is None
    assert 'xmeml' in page.validation_text.toPlainText()
    assert not page.btnNukeAll.isEnabled()


def test_folder_drop_rejects_other_files_multiple_folders_remote_urls_and_busy_scans(page, tmp_path):
    path = tmp_path / 'edit.txt'
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
    assert not page.checkBoxCreateTasks.isChecked()
    assert not page.create_tasks
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


@pytest.mark.parametrize('target_name', ['card', 'field', 'button'])
def test_raw_folder_drop_preserves_timeline_and_replaces_media(page, tmp_path, app, target_name):
    load(page, tmp_path)
    original = page.document
    raw = tmp_path / 'raw'
    path, info = media_file(raw / 'card1')
    (raw / 'unrelated.xml').write_text('not a timeline')
    target = {'card': page.raw_media_card, 'field': page.raw_folder_edit,
              'button': page.raw_folder_button}[target_name]
    with mock.patch('import_timeline_page.probe_media', return_value=info) as probe, \
            mock.patch.object(page, '_show_scan_folder', wraps=page._show_scan_folder) as progress:
        assert drop_urls(target, [QUrl.fromLocalFile(str(raw))]) == (True, True)
        wait_read(page, app)
    probe.assert_called_once()
    progress.assert_any_call(str(raw))
    progress.assert_any_call(str(raw / 'card1'))
    assert page.loading_folder_label.isHidden()
    assert page.document is original
    assert list(page.media) == [path]
    assert page.prepared.rows[0].match.path == path
    assert page.raw_folder_edit.text() == str(raw)
    assert '1 media files' in page.raw_media_status.text()
    assert 'unsupported XML' not in page.raw_media_status.text()


def test_raw_folder_before_xml_is_retained(page, tmp_path, app):
    raw = tmp_path / 'raw'
    path, info = media_file(raw)
    with mock.patch('import_timeline_page.probe_media', return_value=info):
        page._load_raw_folder(str(raw))
        wait_read(page, app)
    xml = tmp_path / 'edit.xml'
    xml.write_text(xml_text())
    page._load_xml_file(str(xml))
    wait_read(page, app)
    assert page.prepared.rows[0].match.path == path


def test_raw_folder_error_preserves_loaded_timeline_and_media(page, tmp_path, app):
    load(page, tmp_path)
    original, media = page.document, page.media
    page._load_raw_folder(str(tmp_path / 'missing'))
    wait_read(page, app)
    assert page.document is original
    assert page.media is media
    assert 'Unable to load raw media' in page.raw_media_status.text()


def test_raw_section_rejects_busy_drops(page, tmp_path):
    page._busy = True
    assert drop_urls(page.raw_media_card, [QUrl.fromLocalFile(str(tmp_path))]) == (False, False)
    page._busy = False


def test_raw_scan_only_probes_timeline_candidates(page, tmp_path, app):
    load(page, tmp_path)
    raw = tmp_path / 'raw'
    path, info = media_file(raw)
    media_file(raw, name='unrelated.mxf')
    with mock.patch('import_timeline_page.probe_media', return_value=info) as probe:
        page._load_raw_folder(str(raw))
        wait_read(page, app)
    assert probe.call_count == 1
    assert probe.call_args.args[0] == path
    assert '2 media files found; 1 checked' in page.raw_media_status.text()


def test_raw_workflow_defaults_to_ten_handles_and_preserves_user_edits(page, tmp_path, app):
    load(page, tmp_path)
    path, info = media_file(tmp_path / 'raw', frames=1000, start=800)
    with mock.patch('import_timeline_page.probe_media', return_value=info):
        page._load_raw_folder(str(tmp_path / 'raw'))
        wait_read(page, app)
        assert not page.detect_handles_check.isChecked()
        assert page.spinboxHandles.value() == 10
        assert page.prepared.shots[0].duration == 40
        page.spinboxHandles.setValue(5)
        page._load_raw_folder(str(tmp_path / 'raw'))
        wait_read(page, app)
    assert page.spinboxHandles.value() == 5
    assert page.prepared.shots[0].duration == 30


def test_online_reference_drop_keeps_timeline_and_validates_coverage(page, tmp_path, app):
    load(page, tmp_path)
    timeline = page.document
    path, info = media_file(tmp_path, name='online.mov', frames=100, start=0)
    with mock.patch('import_timeline_page.probe_media', return_value=info):
        assert drop_urls(page.online_reference_edit, [QUrl.fromLocalFile(path)]) == (True, True)
        wait_read(page, app)
    assert page.document is timeline
    shot = page.prepared.shots[0]
    assert shot.online_reference == path
    assert shot.online_reference_timing == {'frames': 100, 'timeline_start': 0}
    page.online_reference_start.setValue(10)
    assert any('does not cover the cut' in e for e in page.prepared.errors)
    page._clear_online_reference()
    assert page.prepared.shots[0].online_reference == ''


def test_reference_export_without_leader_aligns_to_editorial_span(page, tmp_path, app):
    load(page, tmp_path)
    page.document.clips[0].timeline_start = 250
    path, info = media_file(tmp_path, name='trimmed.mov', frames=20, start=0)
    with mock.patch('import_timeline_page.probe_media', return_value=info):
        page._load_online_reference(path)
        wait_read(page, app)
    assert page.online_reference_start.value() == 250
    assert page.prepared.shots[0].online_reference_timing == {'frames': 20, 'timeline_start': 250}
    assert not any('does not cover the cut' in e for e in page.prepared.errors)
    page.online_reference_start.setValue(0)
    page._refresh_preview()
    assert page.online_reference_start.value() == 0
    assert any('250–270' in e and '0–20' in e for e in page.prepared.errors)
    assert not any(e.startswith(':') for e in page.prepared.errors)


def test_online_reference_rejects_wrong_rate_and_unloaded_path(page, tmp_path, app):
    load(page, tmp_path)
    path, info = media_file(tmp_path, name='online.mov', fps=24)
    with mock.patch('import_timeline_page.probe_media', return_value=info):
        page._load_online_reference(path)
        wait_read(page, app)
    assert any('frame rate differs' in e for e in page.prepared.errors)
    page._reference_path_edited()
    assert any('Load the online reference' in e for e in page.prepared.errors)


def test_collapsible_reference_preview_maps_scrubbing_and_stops_when_hidden(page, tmp_path, app):
    from PyQt6.QtGui import QImage
    from timeline_review_media import MovieDecoder
    load(page, tmp_path)
    page.document.clips[0].timeline_start = 250
    path, info = media_file(tmp_path, name='reference.mov', frames=20, start=0)
    page._online_reference_info = info
    page.online_reference_edit.setText(path)
    page._refresh_preview()
    preview = page.timeline_preview
    assert preview.body.isHidden()
    assert preview.edit.start == 250
    assert preview.reference_start == 250
    image = QImage(16, 16, QImage.Format.Format_RGB888)
    image.fill(Qt.GlobalColor.red)
    with mock.patch.object(MovieDecoder, 'read', return_value=image) as read:
        page.show()
        preview.toggle.setChecked(True)
        preview.seek(260)
        timer = QElapsedTimer()
        timer.start()
        while preview._work is not None:
            app.processEvents()
            QTest.qWait(5)
            assert timer.elapsed() < 3000
        assert preview._displayed == (path, info.signature, 10)
        assert not preview.picture.image.isNull()
        assert read.call_args.args[1] == 10
        preview.play_pause()
        assert preview.timer.isActive()
        preview.toggle.setChecked(False)
        assert not preview.timer.isActive()
        assert preview._wanted is None
        preview.toggle.setChecked(True)
        preview.play_pause()
        page.hide()
        assert not preview.timer.isActive()
    page._clear_online_reference()
    assert preview.reference is None
    assert preview.picture.image.isNull()


def test_find_reference_loads_unique_match_and_leaves_ambiguous_choices(page, tmp_path, app):
    load(page, tmp_path)
    root = tmp_path / 'destination/Exports/Offline/2026-11-16'
    path, info = media_file(root, name='edit.mp4', frames=20, start=0)
    with mock.patch('import_timeline_page.probe_media', return_value=info):
        page._find_reference()
        wait_read(page, app)
    assert page.online_reference_edit.text() == path
    assert page._online_reference_info is info
    page._clear_online_reference()
    other, _ = media_file(root, name='edit.mov', frames=20, start=0)
    with mock.patch('import_timeline_page.probe_media') as probe:
        page._find_reference()
        wait_read(page, app)
    probe.assert_not_called()
    assert page.online_reference_edit.text() == ''
    assert page.online_reference_matches.count() == 3
    assert not page.online_reference_matches.isHidden()
    assert {page.online_reference_matches.itemData(i) for i in (1, 2)} == {path, other}
