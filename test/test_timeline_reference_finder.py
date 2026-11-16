from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from timeline_reference_finder import find_references, offline_roots
from timeline_import import Cancelled


def touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'read only fixture')
    return str(path)


def test_date_and_identity_prevent_wrong_version_or_alt(tmp_path):
    exports = tmp_path / 'Exports'
    xml = touch(exports / 'XML/2026-11-16/Edit_v01_XML.xml')
    expected = touch(exports / 'Offline/2026-11-16/Edit_v01.mp4')
    touch(exports / 'Offline/2026-11-15/Edit_v01.mp4')
    touch(exports / 'Offline/2026-11-16/Edit_v01_ALT.mp4')
    touch(exports / 'Offline/2026-11-16/Edit_v02.mp4')
    before = {p: p.read_bytes() for p in exports.rglob('*') if p.is_file()}
    assert find_references(xml, offline_roots(xml)) == [expected]
    assert all(p.read_bytes() == content for p, content in before.items())


def test_download_copy_uses_project_and_keeps_ambiguous_dates(tmp_path):
    project = tmp_path / 'Project'
    first = touch(project / 'Exports/Offline/2026-11-15/Edit_v01.mp4')
    second = touch(project / 'Exports/Offline/2026-11-16/Edit_v01.mov')
    xml = touch(tmp_path / 'Downloads/Edit_v01_XML.xml')
    roots = offline_roots(xml, str(project))
    assert roots == [str(project / 'Exports/Offline')]
    assert find_references(xml, roots) == [first, second]
    with pytest.raises(Cancelled):
        find_references(xml, roots, lambda: True)
    assert find_references(str(tmp_path / 'Other_XML.xml'), roots) == []


@pytest.mark.parametrize('lead_in', [0, 1, 48, 137, 250, 1200])
@pytest.mark.parametrize('fps', [24, 25, 30])
def test_reference_start_is_calculated_for_any_lead_in(lead_in, fps):
    from types import SimpleNamespace
    from timeline_reference_finder import calculate_reference_start
    clips = [SimpleNamespace(track=1, timeline_start=lead_in, duration=80),
             SimpleNamespace(track=1, timeline_start=lead_in + 100, duration=50),
             SimpleNamespace(track=2, timeline_start=0, duration=2000)]
    document = SimpleNamespace(clips=clips, fps=fps)
    # Include gaps between cuts, but not unrelated upper tracks.
    assert calculate_reference_start(document, 150) == lead_in
    # An export retaining the timeline lead-in stays at frame zero.
    assert calculate_reference_start(document, lead_in + 150) == 0
    # Do not guess a new offset for a different-length export.
    assert calculate_reference_start(document, 149) == 0


def test_reference_start_without_cuts_is_zero():
    from types import SimpleNamespace
    from timeline_reference_finder import calculate_reference_start
    assert calculate_reference_start(SimpleNamespace(clips=[]), 500) == 0
