from pathlib import Path
from types import SimpleNamespace
import json
import os
import sys
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import av
import numpy as np
import pytest
from PyQt6.QtGui import QImage

import filesIO
import thumbnail_updates
from thumbnail_colour import thumbnail_display_processor


@pytest.mark.parametrize('idt,expected', [('ARRI LogC3 (EI800)', 171), ('ARRI LogC4', 228)])
def test_idt_and_aces_display_transform_are_applied_before_eight_bit_output(idt, expected):
    # 55% encoded neutral maps to different display values under these IDTs.
    frame = av.VideoFrame.from_ndarray(np.full((8, 16, 3), round(.55 * 65535), dtype=np.uint16), format='rgb48le')
    container = mock.Mock()
    container.streams.video = [SimpleNamespace(start_time=0, duration=1, time_base=1, frames=1)]
    container.decode.return_value = iter([frame])
    with mock.patch.object(av, 'open') as open_video:
        open_video.return_value.__enter__.return_value = container
        image = thumbnail_updates.extract_preview_thumbnail('plate.mxf', frame_index=0, input_colourspace=idt)
    assert image.pixelColor(0, 0).red() == pytest.approx(expected, abs=1)
    assert (image.width(), image.height()) == (16, 8)


def test_unknown_idt_is_reported_instead_of_using_a_different_transform():
    with pytest.raises(ValueError, match='not available.*Imaginary camera space'):
        thumbnail_display_processor('Imaginary camera space')


def test_generator_detects_camera_idt_but_honours_reviewed_override(tmp_path):
    import timeline_import
    from import_xml_v2 import ThumbnailGenerator

    source = tmp_path / 'plate.mov'
    source.touch()
    metadata = {'camera_make': 'ARRI', 'camera_model': 'ALEXA Mini LF'}
    for override, expected in ((None, 'ARRI LogC3 (EI800)'), ('ARRI LogC4', 'ARRI LogC4')):
        image = QImage(64, 32, QImage.Format.Format_RGB888)
        image.fill(0x808080)
        with mock.patch.object(timeline_import, 'probe_media', return_value=SimpleNamespace(metadata=metadata)) as probe, \
                mock.patch.object(thumbnail_updates, 'extract_preview_thumbnail', return_value=image) as extract:
            result = ThumbnailGenerator.generate(str(source), str(tmp_path / 'thumbnail.jpeg'), input_colourspace=override)
        assert result
        extract.assert_called_once_with(str(source), frame_index=1, input_colourspace=expected)
        assert probe.call_count == (0 if override else 1)


@pytest.mark.parametrize('assets_name', ['shot_assets', 'Shot_Assets'])
def test_dashboard_uses_primary_plate_idt_and_never_double_transforms_renders(tmp_path, assets_name):
    primary = tmp_path / 'plates' / 'shared.mxf'
    primary.parent.mkdir()
    primary.touch()
    secondary = primary.parent / 'secondary.mxf'
    secondary.touch()
    assets = tmp_path / assets_name
    assets.mkdir()
    metadata = {'cut_duration': 11, 'plates': [
        {'track': 1, 'copied_path': str(primary), 'cut_start': 10, 'speed': 1, 'input_colourspace': 'ARRI LogC3 (EI800)'},
        {'track': 2, 'copied_path': str(secondary), 'input_colourspace': 'ARRI LogC4'},
    ]}
    (assets / 'timeline_import.json').write_text(json.dumps(metadata))
    shot = {'id': 1, 'base_path': str(tmp_path)}
    image = QImage(64, 32, QImage.Format.Format_RGB888)
    image.fill(0x808080)
    render = tmp_path / 'renders' / 'shared.mxf'  # Same basename must not identify it as the plate.
    render.parent.mkdir()
    render.touch()
    for path in (primary, secondary, render):
        worker = thumbnail_updates.BatchThumbnailWorker([shot], mock.Mock(), filesIO.Folders())
        with mock.patch.object(thumbnail_updates, 'latest_thumbnail_preview', return_value=str(path)), \
                mock.patch.object(thumbnail_updates, 'extract_preview_thumbnail', return_value=image) as extract, \
                mock.patch.object(thumbnail_updates, 'upload_thumbnail_image', return_value={'thumbnail': 'new.png'}):
            worker.run()
        if path == primary:
            assert extract.call_args.kwargs['input_colourspace'] == 'ARRI LogC3 (EI800)'
            assert extract.call_args.kwargs['frame_index'] == 15
        elif path == secondary:
            assert extract.call_args.kwargs['input_colourspace'] == 'ARRI LogC4'
        else:
            assert 'input_colourspace' not in extract.call_args.kwargs
            assert 'frame_index' not in extract.call_args.kwargs


def test_failed_colour_transform_does_not_write_an_uncorrected_thumbnail(tmp_path):
    import import_xml_v2
    source = tmp_path / 'plate.mxf'
    source.touch()
    output = tmp_path / 'thumbnail.jpeg'
    with mock.patch.object(thumbnail_updates, 'extract_preview_thumbnail', side_effect=ValueError('Unsupported IDT')):
        assert import_xml_v2.ThumbnailGenerator.generate(str(source), str(output), input_colourspace='Unsupported IDT') is None
    assert not output.exists()
