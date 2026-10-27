from pathlib import Path
import struct
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arri_metadata import read_embedded_arri_metadata


def arri_header(*, lens='TRIBE7 TRIBE 7 BLACKWINGS 37 T 1', focal=37000, version=3):
    data = bytearray(4096)
    data[:8] = b'ARRI\x12\x34\x56\x78'
    struct.pack_into('<II', data, 8, 4096, version)
    data[0x2F8:0x2F8 + 20] = b'ARRI ALEXA Mini LF'.ljust(20, b'\0')
    data[0x398:0x398 + 32] = lens.encode('ascii')[:32].ljust(32, b'\0')
    struct.pack_into('<I', data, 0x37C, focal)
    struct.pack_into('<I', data, 0x74, 800)
    struct.pack_into('<I', data, 0x5C, 5600)
    return bytes(data)


def klv(value, key='060e2b340213010d0f01040201010100'):
    return bytes.fromhex(key) + b'\x83' + len(value).to_bytes(3, 'big') + value


def test_reads_preserved_header_at_any_metadata_offset_without_writing(tmp_path):
    path = tmp_path / 'renamed.MXF'
    original = klv(b'unrelated look data' * 100 + arri_header())
    path.write_bytes(original)
    before = path.stat().st_mtime_ns
    metadata = read_embedded_arri_metadata(path)
    assert metadata['camera_model'] == 'ALEXA Mini LF'
    assert metadata['lens_model'] == 'TRIBE7 TRIBE 7 BLACKWINGS 37 T 1'
    assert metadata['focal_length_mm'] == '37'
    assert metadata['exposure_index'] == '800'
    assert metadata['white_balance_kelvin'] == '5600'
    assert path.read_bytes() == original
    assert path.stat().st_mtime_ns == before


@pytest.mark.parametrize('data', [
    b'not MXF', klv(arri_header()[:-1]), klv(arri_header(version=99)),
    bytes.fromhex('060e2b340213010d0f01040201010100') + b'\x89',
    bytes.fromhex('060e2b340213010d0f01040201010100') + b'\x83\xff\xff\xff',
    klv(arri_header())[:-100],
])
def test_malformed_or_unknown_headers_are_optional(tmp_path, data):
    path = tmp_path / 'bad.mxf'
    path.write_bytes(data)
    assert read_embedded_arri_metadata(path) == {}


def test_does_not_scan_video_payload_for_camera_headers(tmp_path):
    path = tmp_path / 'video.mxf'
    path.write_bytes(klv(arri_header(), '060e2b34010201010d01030115011701'))
    assert read_embedded_arri_metadata(path) == {}


def test_conflicting_snapshots_are_not_guessed(tmp_path):
    path = tmp_path / 'mixed.mxf'
    path.write_bytes(klv(arri_header() + arri_header(lens='Different lens', focal=47000)))
    assert read_embedded_arri_metadata(path) == {}


@pytest.mark.parametrize('focal', [0, 0xFFFFFFFF])
def test_unavailable_focal_length_is_not_a_number_in_notes(tmp_path, focal):
    path = tmp_path / 'missing-focal.mxf'
    path.write_bytes(klv(arri_header(focal=focal)))
    metadata = read_embedded_arri_metadata(path)
    assert 'focal_length_mm' not in metadata
    assert 'lens_model' in metadata


def test_no_lens_library_needed_for_missing_metadata_or_cancellation(tmp_path):
    path = tmp_path / 'cancel.mxf'
    path.write_bytes(klv(arri_header()))
    assert read_embedded_arri_metadata(path, lambda: True) == {}
    assert read_embedded_arri_metadata(tmp_path / 'missing.mxf') == {}
    with mock.patch.object(Path, 'open', side_effect=AssertionError('Must not open non-MXF')):
        assert read_embedded_arri_metadata(tmp_path / 'clip.mov') == {}
