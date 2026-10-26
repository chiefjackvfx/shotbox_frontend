"""Conservative file identities must include sources requested through retimes."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'plugins/nuke'))
from shotbox_nuke.freshness import _media_identities, compare  # noqa: E402


@pytest.mark.parametrize('pattern', ['plate_%04d.exr', 'plate_####.exr', 'plate_0005.exr'])
def test_sequence_identities_include_members_outside_capture_time(tmp_path, pattern):
    for frame in (-2, 1, 3):
        (tmp_path / ('plate_%04d.exr' % frame)).write_bytes(b'known source')
    # Frame 5 does not exist; a retime requests 3. Do not assume source time.
    identities = _media_identities(str(tmp_path / pattern), {})
    assert {Path(record['path']).name for record in identities} == {
        'plate_-002.exr', 'plate_0001.exr', 'plate_0003.exr'}
    consumed = tmp_path / 'plate_0003.exr'
    original = next(record for record in identities if record['path'] == str(consumed))
    consumed.write_bytes(b'changed consumed source')
    current = next(record for record in _media_identities(str(tmp_path / pattern), {})
                   if record['path'] == str(consumed))
    assert current != original


def test_sequence_scans_are_shared_per_fingerprint_but_not_between_checks(tmp_path):
    (tmp_path / 'capture.0001.exr').write_bytes(b'one')
    scanned = {}
    first = _media_identities(str(tmp_path / 'capture.%04d.exr'), scanned)
    assert _media_identities(str(tmp_path / 'capture.####.exr'), scanned) is first
    (tmp_path / 'capture.0002.exr').write_bytes(b'two')
    assert len(_media_identities(str(tmp_path / 'capture.%04d.exr'), {})) == 2


def test_missing_sequence_and_static_source_are_unresolvable(tmp_path):
    with pytest.raises(ValueError, match='No readable members'):
        _media_identities(str(tmp_path / 'missing.%04d.exr'), {})
    with pytest.raises(FileNotFoundError):
        _media_identities(str(tmp_path / 'missing.exr'), {})


def test_unresolved_dependencies_never_mean_matching_freshness():
    saved = {'digest': 'same', 'unresolved': []}
    assert compare(saved, dict(saved)) == 'no detected change'
    assert compare(saved, {'digest': 'new', 'unresolved': []}) == 'stale'
    assert compare(saved, {'digest': 'same', 'unresolved': ['Python expression']}) == 'unverifiable'
