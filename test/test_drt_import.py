"""Sanitized timing fixtures from a Resolve 21.1 DRT export."""
from pathlib import Path
import sys
import zipfile
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from timeline_import import read_timeline


def write_drt(path, change=None):
    fixtures = Path(__file__).parent / 'fixtures'
    pool = ET.parse(fixtures / 'drt_media_pool.xml').getroot()
    sequence = ET.parse(fixtures / 'drt_sequence.xml').getroot()
    if change:
        change(pool, sequence)
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('MediaPool/Master/MpFolder.xml', ET.tostring(pool))
        archive.writestr('SeqContainer/sequence.xml', ET.tostring(sequence))
    return path


def test_drt_timing_and_repeated_media(tmp_path):
    path = write_drt(tmp_path / 'edit.drt')
    before = path.read_bytes()
    result = read_timeline(str(path))
    assert result.fps == 25
    assert len(result.clips) == 27
    assert [sum(c.track == n for c in result.clips) for n in (1, 2, 3)] == [19, 6, 2]
    assert max(c.timeline_start + c.duration for c in result.clips) == 1234
    assert all(not c.error for c in result.clips)
    assert result.clips[17].timeline_start == 1043  # Preserve the one-frame gap.
    assert result.clips[0].source_start == pytest.approx(896660)
    retimed = [c for c in result.clips if c.speed != 1]
    assert [c.speed for c in retimed] == pytest.approx([0.6361556064, 0.7471910112])
    assert [c.source_start for c in retimed] == pytest.approx([1698851.221968, 1178290.112360])
    assert result.clips[11].reference == result.clips[12].reference
    assert result.clips[11].source_start != result.clips[12].source_start
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [('MediaTimemapBA', 'deadbeef'), ('Flags', '1')])
def test_unknown_timing_or_flags_block_clip(tmp_path, field, value):
    def change(pool, sequence):
        sequence.find('.//Sm2TiVideoClip/' + field).text = value
    result = read_timeline(str(write_drt(tmp_path / 'edit.drt', change)))
    assert result.clips[0].error


def test_missing_sequence_reference_rejected(tmp_path):
    def change(pool, sequence):
        sequence.find('.//Sm2TiTrack/Sequence').text = 'wrong'
    with pytest.raises(ValueError, match='different sequence'):
        read_timeline(str(write_drt(tmp_path / 'edit.drt', change)))


def test_corrupt_archive_rejected(tmp_path):
    path = tmp_path / 'bad.drt'
    path.write_bytes(b'not zip')
    with pytest.raises(ValueError, match='Cannot read DRT'):
        read_timeline(str(path))


def test_transition_rejected(tmp_path):
    def change(pool, sequence):
        sequence.find('.//Sm2TiVideoClip').tag = 'Sm2TiTransition'
    with pytest.raises(ValueError, match='transition'):
        read_timeline(str(write_drt(tmp_path / 'edit.drt', change)))


@pytest.mark.parametrize("extra", ["none", "empty", "occupied"])
def test_drt_keeps_sixteenth_layer(tmp_path, extra):
    import copy
    def change(pool, sequence):
        tracks = sequence.findall('VideoTrackVec/Element/Sm2TiTrack')
        tracks[15].find('Items').append(copy.deepcopy(tracks[0].find('Items/Element')))
        if extra != 'none':
            track = copy.deepcopy(sequence.find('VideoTrackVec/Element'))
            if extra == 'empty':
                track.find('Sm2TiTrack/Items').clear()
            sequence.find('VideoTrackVec').append(track)
    path = write_drt(tmp_path / 'layers.drt', change)
    if extra == 'occupied':
        with pytest.raises(ValueError, match='V17.*V16'):
            read_timeline(str(path))
        return
    result = read_timeline(str(path))
    assert result.clips[-1].track == 16
    assert not result.clips[-1].error
