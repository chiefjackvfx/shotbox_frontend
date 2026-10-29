from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import sys
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
import timeline_import as module


def occurrence(**kwargs):
    values = dict(uid='1:0', name='camera.mxf', reference='file:///old/camera.mxf',
                  file_name='camera.mxf', reel='camera', track=1, timeline_start=0,
                  duration=20, fps=25, source_start=910, speed=1)
    values.update(kwargs)
    return module.TimelineClip(**values)


def document(tmp_path, clips=None):
    path = tmp_path / 'edit.xml'
    path.write_text('source remains unchanged')
    return module.TimelineDocument(str(path), 'edit', 25, clips or [occurrence()],
                                   signature=module.fingerprint(path))


def media_file(tmp_path, name='camera.mxf', frames=40, start=900, fps=25):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fake video for metadata tests')
    return str(path), module.MediaInfo(str(path), frames, fps, start, module.fingerprint(path))


def xml_text(*, path='file:///old/V1-0001_camera.mov', name='camera.mxf', file_tc='00:00:36:00',
             source_in=10, start=0, end=20, duration=999, filter_text=''):
    return f'''<xmeml version="5"><sequence><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
      <media><video><track><clipitem><name>{name}</name><start>{start}</start><end>{end}</end>
      <in>{source_in}</in><out>{source_in + end - start}</out><duration>{duration}</duration>
      <file id="source"><name>{name}</name><pathurl>{path}</pathurl><timecode><string>{file_tc}</string>
      <reel><name>camera</name></reel></timecode></file>{filter_text}</clipitem></track></video></media>
      </sequence></xmeml>'''


def test_discovery_is_generic_recursive_and_does_not_follow_symlinks(tmp_path):
    nested = tmp_path / 'arbitrary project' / 'media'
    nested.mkdir(parents=True)
    (nested / 'other.MXF').write_bytes(b'')
    (tmp_path / 'first.xml').write_text(xml_text())
    (tmp_path / 'second.OTIO').write_text('{}')
    (tmp_path / 'reference.DRT').write_bytes(b'')
    (tmp_path / 'unsupported.xml').write_text('<fcpxml/>')
    (tmp_path / 'broken.xml').write_text('broken')
    (tmp_path / 'link').symlink_to(nested, target_is_directory=True)
    found = module.discover(str(tmp_path))
    assert len(found.media) == 1
    assert len(found.timelines) == 3
    assert len(found.references) == 0
    assert len(found.warnings) == 2


def test_scan_can_cancel(tmp_path):
    with pytest.raises(module.Cancelled):
        module.discover(str(tmp_path), lambda: True)


def test_xml_uses_editorial_duration_and_camera_identity(tmp_path):
    path = tmp_path / 'timeline.xml'
    path.write_text(xml_text())
    before = path.read_bytes()
    result = module.read_xml(str(path))
    clip = result.clips[0]
    assert clip.duration == 20  # XML duration=999 is source/render length.
    assert clip.source_start == 910
    local, info = media_file(tmp_path)
    assert module.match_media(clip, {local: info}).path == local
    assert path.read_bytes() == before


def test_xml_resolves_shared_file_ids(tmp_path):
    path = tmp_path / 'edit.xml'
    text = xml_text()
    first_end = text.index('</clipitem>') + len('</clipitem>')
    second = '<clipitem><name>camera.mxf</name><start>20</start><end>30</end><in>25</in><file id="source"/></clipitem>'
    path.write_text(text[:first_end] + second + text[first_end:])
    clips = module.read_xml(str(path)).clips
    assert len(clips) == 2
    assert clips[1].reference == clips[0].reference
    assert clips[1].source_start == 925


def test_xml_retime_uses_graph_cut_anchor(tmp_path):
    effect = '''<filter><effect><effectid>timeremap</effectid>
      <parameter><parameterid>speed</parameterid><value>63.6156</value></parameter>
      <parameter><parameterid>graphdict</parameterid><keyframe><when>16</when>
      <value>10.0002</value><speedkfin>TRUE</speedkfin></keyframe></parameter></effect></filter>'''
    path = tmp_path / 'edit.xml'
    path.write_text(xml_text(source_in=16, filter_text=effect))
    clip = module.read_xml(str(path)).clips[0]
    assert clip.source_start == pytest.approx(910.0002)
    assert clip.speed == pytest.approx(0.636156)
    assert not clip.error


def test_otio_adapter_preserves_gaps_active_reference_and_fractional_retime(tmp_path):
    otio = pytest.importorskip('opentimelineio')
    rt = otio.opentime.RationalTime
    tr = otio.opentime.TimeRange
    reference = otio.schema.ExternalReference(target_url='/original/camera.mxf', available_range=tr(rt(900, 25), rt(200, 25)))
    reference.name = 'camera.mxf'
    clip = otio.schema.Clip(name='display name', media_reference=reference,
                            source_range=tr(rt(910.2219, 25), rt(20, 25)),
                            effects=[otio.schema.LinearTimeWarp(time_scalar=0.6361556)])
    track = otio.schema.Track(kind=otio.schema.TrackKind.Video,
                             children=[otio.schema.Gap(duration=rt(7, 25)), clip])
    timeline = otio.schema.Timeline(tracks=[track], global_start_time=rt(90000, 25))
    path = tmp_path / 'edit.otio'
    otio.adapters.write_to_file(timeline, str(path))
    result = module.read_otio(str(path))
    assert result.clips[0].timeline_start == 7
    assert result.clips[0].duration == 20
    assert result.clips[0].source_start == pytest.approx(910.2219)
    assert result.clips[0].speed == pytest.approx(0.6361556)


def test_xml_without_frame_rate_does_not_assume_25(tmp_path):
    path = tmp_path / 'edit.xml'
    path.write_text(xml_text().replace('<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>', ''))
    with pytest.raises(ValueError, match='frame rate'):
        module.read_xml(str(path))


@pytest.mark.parametrize('tc,fps,expected', [('01:00:00:00', 25, 90000), ('01:00:00;00', 30000 / 1001, 107892)])
def test_timecode(tc, fps, expected):
    assert module.timecode_frames(tc, fps) == expected


def test_invalid_drop_frame_label():
    with pytest.raises(ValueError):
        module.timecode_frames('00:01:00;00', 30000 / 1001)


def test_duplicate_names_require_review_unless_coverage_is_unique(tmp_path):
    a, first = media_file(tmp_path / 'a', start=900)
    b, second = media_file(tmp_path / 'b', start=900)
    result = module.match_media(occurrence(), {a: first, b: second})
    assert result.path is None
    second.start = 2000
    result = module.match_media(occurrence(), {a: first, b: second})
    assert result.path == a
    assert 'timecode coverage' in result.reason


def test_conflicting_identity_hints_are_not_auto_selected(tmp_path):
    a, first = media_file(tmp_path, 'camera.mxf')
    b, second = media_file(tmp_path, 'different.mov')
    match = module.match_media(occurrence(reference='/source/different.mov'), {a: first, b: second})
    assert match.path is None
    assert 'Conflicting' in match.reason


def test_url_and_windows_basenames():
    assert module.basename('file:///D:/old/my%20camera.mxf') == 'my camera.mxf'
    assert module.basename(r'D:\original\camera.mxf') == 'camera.mxf'


def test_stem_suggestion_needs_confirmation_and_missing_never_uses_original(tmp_path):
    path, info = media_file(tmp_path, 'camera.mov')
    match = module.match_media(occurrence(), {path: info})
    assert match.path is None and match.candidates == [path]
    doc = document(tmp_path)
    prepared = module.prepare_import(doc, {}, handles=0)
    assert not prepared.shots
    assert any('Missing local' in error for error in prepared.errors)


def test_unique_case_insensitive_match(tmp_path):
    path, info = media_file(tmp_path, 'CAMERA.MXF')
    match = module.match_media(occurrence(), {path: info})
    assert match.path == path and match.reason.startswith('Case-insensitive')


def test_trimmed_source_offsets_and_uneven_handles(tmp_path):
    path, info = media_file(tmp_path, frames=52, start=1661)
    doc = document(tmp_path, [occurrence(source_start=1671, duration=32)])
    preview = module.prepare_import(doc, {path: info}, handles=25)
    assert preview.errors  # Requesting 25/25 cannot silently clamp.
    row = preview.rows[0]
    assert (row.local_cut_start, row.available_head, row.available_tail) == (10, 10, 10)
    prepared = module.prepare_import(doc, {path: info}, handles=25, accepted_handles={0: (5, 10)})
    assert not prepared.errors
    shot = prepared.shots[0]
    assert (shot.cut_duration, shot.duration, shot.head_handles, shot.tail_handles) == (32, 47, 5, 10)
    assert shot.clips[0].filepath == path
    assert shot.clips[0].media_timing['cut_start'] == 10


def test_reused_media_has_independent_occurrence_ranges(tmp_path):
    path, info = media_file(tmp_path, frames=87)
    clips = [occurrence(duration=33), occurrence(uid='1:1', timeline_start=33, source_start=950, duration=27)]
    prepared = module.prepare_import(document(tmp_path, clips), {path: info}, handles=0)
    assert not prepared.errors
    assert [s.clips[0].media_timing['cut_start'] for s in prepared.shots] == [10, 50]


def test_layer_limits_available_handles_and_own_source_mapping(tmp_path):
    path, info = media_file(tmp_path)
    second, extra = media_file(tmp_path, 'layer.mxf', frames=30, start=905)
    clips = [occurrence(), occurrence(uid='2:0', track=2, reference='/source/layer.mxf', file_name='layer.mxf')]
    doc = document(tmp_path, clips)
    prepared = module.prepare_import(doc, {path: info, second: extra}, accepted_handles={0: (5, 5)})
    assert not prepared.errors
    assert len(prepared.shots[0].clips) == 2
    assert prepared.shots[0].clips[1].media_timing['cut_start'] == 5
    assert module.prepare_import(doc, {path: info, second: extra}, accepted_handles={0: (10, 10)}).errors


@pytest.mark.parametrize('choice,expected', [('selected', 10), ('companion', 10.221967)])
def test_timing_conflict_requires_user_choice_and_preserves_selected_mapping(tmp_path, choice, expected):
    path, info = media_file(tmp_path)
    first = occurrence(speed=0.636156)
    doc = document(tmp_path, [first])
    other_path = tmp_path / 'edit.otio'
    other_path.write_text('{}')
    companion = replace(doc, path=str(other_path), clips=[replace(first, source_start=910.221967, speed=0.6361556)])
    result = module.prepare_import(doc, {path: info}, companion=companion, handles=0)
    assert any('Choose XML or OTIO' in e for e in result.errors)
    result = module.prepare_import(doc, {path: info}, companion=companion, handles=0, timing_choices={first.uid: choice})
    assert not result.errors
    assert result.shots[0].clips[0].media_timing['cut_start'] == pytest.approx(expected)
    assert result.rows[0].timing_source == (doc.path if choice == 'selected' else companion.path)


def test_float_noise_does_not_create_conflict(tmp_path):
    doc = document(tmp_path)
    companion = replace(doc, clips=[replace(doc.clips[0], source_start=910 + 1e-9)])
    assert not module.timing_conflicts(doc, companion)


def test_companion_structure_mismatch_is_explicit(tmp_path):
    doc = document(tmp_path)
    with pytest.raises(ValueError, match='does not align'):
        module.timing_conflicts(doc, replace(doc, clips=[occurrence(timeline_start=1)]))


def test_missing_timecodes_need_manual_offset(tmp_path):
    path, info = media_file(tmp_path, start=None)
    doc = document(tmp_path)
    assert module.prepare_import(doc, {path: info}, handles=0).errors
    result = module.prepare_import(doc, {path: info}, handles=0, offset_overrides={'1:0': 5})
    assert not result.errors
    assert result.shots[0].clips[0].media_timing['cut_start'] == 5


def test_out_of_bounds_rate_mismatch_and_bad_probes_block(tmp_path):
    path, info = media_file(tmp_path)
    doc = document(tmp_path)
    info.fps = 24
    assert any('rate differs' in e for e in module.prepare_import(doc, {path: info}).errors)
    info.fps = 25
    assert any('outside' in e for e in module.prepare_import(doc, {path: info}, offset_overrides={'1:0': 100}).errors)
    info.error = 'Unreadable video'
    assert any('Unreadable' in e for e in module.prepare_import(doc, {path: info}).errors)


def test_shot_exclusion_keeps_numbering_and_blocks_empty_import(tmp_path):
    path, info = media_file(tmp_path)
    doc = document(tmp_path, [occurrence(), occurrence(uid='1:1', timeline_start=20)])
    result = module.prepare_import(doc, {path: info}, excluded={0}, handles=0)
    assert not result.errors
    assert result.shots[0].index == 1
    assert module.prepare_import(doc, {path: info}, excluded={0, 1}, handles=0).errors == ['No included shots']


def test_preflight_detects_changed_timelines_and_media(tmp_path):
    path, info = media_file(tmp_path)
    doc = document(tmp_path)
    result = module.prepare_import(doc, {path: info}, handles=0)
    with mock.patch.object(module, 'probe_media', return_value=info):
        result.revalidate()
    with mock.patch.object(module, 'probe_media', return_value=replace(info, frames=1)):
        with pytest.raises(ValueError, match='Media changed'):
            result.revalidate()
    Path(doc.path).write_text('changed input')
    with pytest.raises(ValueError, match='Timeline changed'):
        result.revalidate()


def test_manual_replacement_can_use_explicit_external_path(tmp_path):
    path, info = media_file(tmp_path / 'another folder', 'renamed.mov')
    doc = document(tmp_path)
    result = module.prepare_import(doc, {path: info}, handles=0, path_overrides={'1:0': path})
    assert not result.errors
    assert result.rows[0].match.reason == 'Manual selection'


def test_probe_uses_stream_timebase_and_does_not_require_avg_frame_rate(tmp_path):
    import json
    path, _ = media_file(tmp_path)
    output = json.dumps({'streams': [{'r_frame_rate': '25/1', 'avg_frame_rate': '0/0', 'time_base': '1/25',
                                     'duration_ts': 52, 'tags': {'timecode': '00:00:36:00'}}]})
    process = mock.MagicMock()
    process.__enter__.return_value = process
    process.communicate.return_value = (output, '')
    process.returncode = 0
    process.poll.return_value = 0
    with mock.patch.object(module.shutil, 'which', return_value='/usr/bin/ffprobe'), mock.patch.object(module.subprocess, 'Popen', return_value=process):
        result = module.probe_media(path)
    assert not result.error
    assert (result.frames, result.fps, result.start) == (52, 25, 900)


def test_probe_dependency_failure_is_actionable(tmp_path):
    path, _ = media_file(tmp_path)
    with mock.patch.object(module.shutil, 'which', return_value=None):
        assert 'ffprobe' in module.probe_media(path).error


def test_missing_otio_identifies_the_app_interpreter_and_setup_script(tmp_path):
    import builtins
    real_import = builtins.__import__

    def missing_otio(name, *args, **kwargs):
        if name == 'opentimelineio':
            raise ModuleNotFoundError("No module named 'opentimelineio'")
        return real_import(name, *args, **kwargs)

    with mock.patch('builtins.__import__', side_effect=missing_otio):
        with pytest.raises(ValueError) as error:
            module.read_otio(str(tmp_path / 'edit.otio'))
    assert sys.executable in str(error.value)
    assert 'opentimelineio' in str(error.value)
    assert ('setup_runtime.ps1' if os.name == 'nt' else 'setup_runtime.sh') in str(error.value)


def test_import_omits_sidecar_and_preserves_uneven_handles_and_database_payload(tmp_path):
    from import_xml_v2 import DjangoImporter, NukeScriptGenerator

    path, info = media_file(tmp_path)
    prepared = module.prepare_import(document(tmp_path, [occurrence(timeline_start=200)]), {path: info}, handles=25, accepted_handles={0: (5, 7)})
    assert not prepared.errors
    shot = prepared.shots[0]
    shot.name = 'sho010'
    folder = NukeScriptGenerator().create_shot_folder(shot, str(tmp_path / 'output'), 'edit')
    assert not list(Path(folder).rglob('timeline_import.json'))
    assert (shot.head_handles, shot.tail_handles, shot.requested_handles) == (5, 7, 25)
    assert shot.cut_duration == 20
    assert (shot.cut_first, shot.cut_last) == (1006, 1025)
    assert (shot.timeline_inpoint, shot.timeline_outpoint) == (200, 220)
    assert (shot.edit_inpoint, shot.edit_outpoint) == (200, 220)
    assert shot.clips[0].filepath == path
    assert Path(shot.clips[0].copied_filepath).read_bytes() == Path(path).read_bytes()
    api = mock.Mock()
    api.get_or_create_job.return_value = ({'id': 1}, True)
    api.get_or_create_timeline.return_value = ({'id': 2}, True)
    api.get_or_create_shot.return_value = ({'id': 3}, True)
    DjangoImporter(api).import_shot(shot, 'Job', 'edit', 'ARRI LogC4', 25)
    fields = api.update_shot.call_args.kwargs
    assert set(fields) == {'duration', 'edit_inpoint', 'edit_outpoint', 'colourspace', 'handles', 'original_clip', 'original_clips'}
    assert fields['handles'] == '25'
    assert fields['duration'] == '32'
    assert fields['original_clip'] == shot.clips[0].copied_filepath
    assert (fields['edit_inpoint'], fields['edit_outpoint']) == ('200', '220')
    with mock.patch.object(NukeScriptGenerator, '_get_video_format', return_value=(1920, 1080)):
        request = NukeScriptGenerator()._build_create_nk_request(shot, folder, str(Path(folder, 'scripts', 'test.nk')), 'Job')
    assert (request.edit_inpoint, request.edit_outpoint) == (200, 220)
    assert (request.cut_first, request.cut_last) == (1006, 1025)


def test_detects_handles_without_assuming_ten_or_twenty_five(tmp_path):
    path, info = media_file(tmp_path, frames=45)
    doc = document(tmp_path, [occurrence(source_start=907)])
    result = module.prepare_import(doc, {path: info}, handles=None)
    assert not result.errors
    assert result.detected_handles == {0: (7, 18)}
    shot = result.shots[0]
    assert (shot.head_handles, shot.tail_handles) == (7, 18)
    assert (shot.edit_inpoint, shot.edit_outpoint, shot.duration) == (0, 20, 45)
    assert (shot.cut_first, shot.cut_last) == (1008, 1027)
    assert shot.requested_handles is None
    reduced = module.prepare_import(doc, {path: info}, handles=None, accepted_handles={0: (3, 11)})
    assert not reduced.errors
    assert reduced.shots[0].edit_inpoint == 0
    assert reduced.shots[0].edit_outpoint == 20
    assert reduced.shots[0].cut_first == 1004


def test_detected_handles_are_limited_by_every_layer(tmp_path):
    path, info = media_file(tmp_path)
    second, extra = media_file(tmp_path, 'layer.mxf', frames=33, start=905)
    clips = [occurrence(), occurrence(uid='2:0', track=2, reference='/source/layer.mxf', file_name='layer.mxf')]
    result = module.prepare_import(document(tmp_path, clips), {path: info, second: extra}, handles=None)
    assert not result.errors
    assert result.detected_handles == {0: (5, 8)}
    assert (result.shots[0].edit_inpoint, result.shots[0].edit_outpoint) == (0, 20)
    assert (result.shots[0].cut_first, result.shots[0].cut_last) == (1006, 1025)


def test_changed_media_is_rejected_before_plate_copy(tmp_path):
    from import_xml_v2 import NukeScriptGenerator
    path, info = media_file(tmp_path)
    prepared = module.prepare_import(document(tmp_path), {path: info}, handles=0)
    shot = prepared.shots[0]
    shot.name = 'sho010'
    Path(path).write_bytes(b'changed media')
    with pytest.raises(ValueError, match='changed since timeline review'):
        NukeScriptGenerator().create_shot_folder(shot, str(tmp_path / 'out'), 'edit')


def test_detected_camera_colours_reach_each_plate_request_and_shot_notes(tmp_path):
    from import_xml_v2 import DjangoImporter, NukeScriptGenerator
    path, first = media_file(tmp_path)
    second_path, second = media_file(tmp_path, 'layer.mxf')
    first.metadata = {'company_name': 'ARRI', 'product_name': 'ALEXA Mini LF',
                      'lens_model': 'TRIBE7 TRIBE 7 BLACKWINGS 37 T 1', 'focal_length_mm': '37'}
    second.metadata = {'camera_make': 'ARRI', 'camera_model': 'ALEXA 35',
                       'lens_model': 'TRIBE 7 BLACKWINGS 47 1155', 'focal_length_mm': '47'}
    clips = [occurrence(), occurrence(uid='2:0', track=2, reference='/source/layer.mxf', file_name='layer.mxf', speed=0.5)]
    prepared = module.prepare_import(document(tmp_path, clips), {path: first, second_path: second}, auto_colourspace=True)
    assert not prepared.errors
    shot = prepared.shots[0]
    shot.name = 'sho010'
    for clip in shot.clips:
        clip.copied_filepath = clip.filepath
    generator = NukeScriptGenerator('ACES - ACEScg')
    with mock.patch.object(generator, '_get_video_format', return_value=(1920, 1080)):
        request = generator._build_create_nk_request(shot, str(tmp_path), str(tmp_path / 'script.nk'), 'Job')
    assert request.colourspace == 'ARRI LogC3 (EI800)'
    assert [timing['input_colourspace'] for timing in request.plate_timings] == ['ARRI LogC3 (EI800)', 'ARRI LogC4']
    assert 'Lens: TRIBE7 TRIBE 7 BLACKWINGS 37 T 1' in request.shot_metadata_lines
    assert 'Focal length: 37 mm' in request.shot_metadata_lines
    assert 'V2 Focal length: 47 mm' in request.shot_metadata_lines
    assert 'V2 Retime: 50%' in request.shot_metadata_lines
    assert not any(line == 'Retime: 100%' for line in request.shot_metadata_lines)
    api = mock.Mock()
    api.get_or_create_job.return_value = ({'id': 1}, True)
    api.get_or_create_timeline.return_value = ({'id': 2}, True)
    api.get_or_create_shot.return_value = ({'id': 3}, False)
    api.get_shot.return_value = {'id': 3, 'notes': 'Keep these existing artist notes.'}
    importer = DjangoImporter(api)
    importer.import_shot(shot, 'Job', 'Edit', 'ACES - ACEScg', 25)
    fields = api.update_shot.call_args.kwargs
    assert fields['colourspace'] == 'ARRI LogC3 (EI800)'
    assert fields['notes'].startswith('Keep these existing artist notes.\n\n')
    assert 'Camera: ARRI ALEXA Mini LF' in fields['notes']
    assert 'V2 Camera: ARRI ALEXA 35' in fields['notes']
    assert 'Lens: TRIBE7 TRIBE 7 BLACKWINGS 37 T 1' in fields['notes']
    assert 'Focal length: 37 mm' in fields['notes']
    assert 'V2 Focal length: 47 mm' in fields['notes']
    assert 'V2 Retime: 50%' in fields['notes']
    api.get_shot.return_value = {'id': 3, 'notes': fields['notes']}
    importer.import_shot(shot, 'Job', 'Edit', 'ACES - ACEScg', 25)
    assert 'notes' not in api.update_shot.call_args.kwargs  # No duplicate camera notes.


def test_probe_merges_embedded_lens_metadata_into_imported_shot(tmp_path):
    from test_arri_metadata import arri_header, klv
    import json
    path, _ = media_file(tmp_path)
    Path(path).write_bytes(klv(arri_header()))
    output = json.dumps({'streams': [{'r_frame_rate': '25/1', 'nb_frames': '40', 'tags': {'timecode': '00:00:36:00'}}]})
    process = mock.MagicMock()
    process.__enter__.return_value = process
    process.communicate.return_value = (output, '')
    process.returncode = 0
    process.poll.return_value = 0
    with mock.patch.object(module.shutil, 'which', return_value='/usr/bin/ffprobe'), mock.patch.object(module.subprocess, 'Popen', return_value=process):
        info = module.probe_media(path)
    assert not info.error
    prepared = module.prepare_import(document(tmp_path), {path: info}, auto_colourspace=True)
    assert not prepared.errors
    timing = prepared.shots[0].primary_clip.media_timing
    assert (timing['lens_model'], timing['focal_length_mm']) == ('TRIBE7 TRIBE 7 BLACKWINGS 37 T 1', '37')
    assert timing['camera_metadata_source'] == 'Embedded ARRI V3 header snapshot'


def test_unknown_idt_requires_override_in_automatic_mode(tmp_path):
    path, info = media_file(tmp_path)
    doc = document(tmp_path)
    result = module.prepare_import(doc, {path: info}, auto_colourspace=True)
    assert any('Input transform not identified' in error for error in result.errors)
    result = module.prepare_import(doc, {path: info}, auto_colourspace=True, colourspace_overrides={'1:0': 'ACES - ACEScg'})
    assert not result.errors
    assert result.shots[0].primary_clip.media_timing['colourspace_source'] == 'manual'


@pytest.mark.parametrize("extra", ["none", "empty", "occupied"])
def test_xml_and_legacy_parser_keep_sixteen_layers(tmp_path, extra):
    import copy
    import xml.etree.ElementTree as ET
    from import_xml_v2 import XMLTimelineParser
    root = ET.fromstring(xml_text())
    video = root.find('sequence/media/video')
    for _ in range(15):
        video.append(copy.deepcopy(video[0]))
    if extra != 'none':
        video.append(copy.deepcopy(video[0]) if extra == 'occupied' else ET.Element('track'))
    path = tmp_path / 'layers.xml'
    ET.ElementTree(root).write(path)
    if extra == 'occupied':
        with pytest.raises(ValueError, match='V17.*V16'):
            module.read_timeline(str(path))
        with pytest.raises(ValueError, match='V17.*V16'):
            XMLTimelineParser().parse(str(path), handles=0)
        return
    result = module.read_timeline(str(path))
    assert [c.track for c in result.clips] == list(range(1, 17))
    assert all(not c.error for c in result.clips)
    legacy = XMLTimelineParser().parse(str(path), handles=0)
    assert [c.track for c in legacy[0].clips] == list(range(1, 17))


def test_sparse_high_layers_survive_shot_preparation(tmp_path):
    path, info = media_file(tmp_path)
    tracks = [1, 6, 16]
    clips = [occurrence(uid=f'{n}:0', track=n) for n in tracks]
    result = module.prepare_import(document(tmp_path, clips), {path: info}, handles=0)
    assert not result.errors
    assert [c.track for c in result.shots[0].clips] == tracks
    assert all(c.media_timing['cut_start'] == 10 for c in result.shots[0].clips)


@pytest.mark.parametrize("extra", ["none", "empty", "occupied"])
def test_otio_keeps_sixteenth_video_track(tmp_path, extra):
    otio = pytest.importorskip('opentimelineio')
    rt = otio.opentime.RationalTime
    tr = otio.opentime.TimeRange
    tracks = []
    for n in range(16 if extra == "none" else 17):
        clips = []
        if n in (0, 15) or (n == 16 and extra == "occupied"):
            clips.append(otio.schema.Clip(name='camera.mxf',
                media_reference=otio.schema.ExternalReference(target_url='/old/camera.mxf'),
                source_range=tr(rt(910, 25), rt(20, 25))))
        tracks.append(otio.schema.Track(kind=otio.schema.TrackKind.Video, children=clips))
    path = tmp_path / 'layers.otio'
    otio.adapters.write_to_file(otio.schema.Timeline(tracks=tracks, global_start_time=rt(0, 25)), str(path))
    if extra == 'occupied':
        with pytest.raises(ValueError, match='V17.*V16'):
            module.read_timeline(str(path))
        return
    result = module.read_timeline(str(path))
    assert [c.track for c in result.clips] == [1, 16]
    assert all(not c.error for c in result.clips)


def test_sixteenth_layer_still_requires_alignment(tmp_path):
    doc = document(tmp_path, [occurrence(), occurrence(uid='16:0', track=16, timeline_start=1)])
    with pytest.raises(ValueError, match='not aligned'):
        module._validate_structure(doc)
