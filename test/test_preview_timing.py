"""OG preview timing discovery and saved cut selection."""
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nuke_headless_tasks import _load_preview_timing, preview_timing_script


def test_only_original_plates_pick_latest_version(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ('shot_v009.nk', 'shot_v010.nk', 'autosave.nk'):
        (scripts / name).touch()
    assert preview_timing_script(tmp_path, 'plate_01') == str(scripts / 'shot_v010.nk')
    assert preview_timing_script(tmp_path, None) is None
    assert preview_timing_script(tmp_path / 'missing', 'plate_01') is None


def node(kind, **values):
    result = Mock()
    knobs = {key: Mock(value=Mock(return_value=value), evaluate=Mock(return_value=value),
                       toScript=Mock(return_value=str(value))) for key, value in values.items()}
    result.__getitem__ = Mock(side_effect=knobs.__getitem__)
    result.knobs.return_value = knobs
    result.Class.return_value = kind
    return result


def scene(tmp_path):
    read = node('Read', file='../plates/source.mov', frame_mode='expression', frame='frame + 0.25')
    secondary = node('Read', file='../plates/secondary.mov')
    retime = node('TimeWarp')
    retime.input.return_value = read
    retime.writeKnobs.return_value = 'lookup {{11.25 + (frame - 1004) * 0.5}}'
    viewer = node('Viewer', frame_range='1004-1023', frame_range_lock=True)
    nuke = Mock()
    pools = {'Read': [read, secondary], 'Viewer': [viewer]}
    nuke.allNodes.side_effect = lambda kind=None: pools.get(kind, [read, secondary, retime, viewer])
    nuke.root.return_value = node('Root', name='Root', first_frame=1001, last_frame=1030)
    return nuke, read, pools


def test_selects_source_by_path_and_preserves_fractional_mapping(tmp_path):
    nuke, read, pools = scene(tmp_path)
    timing = _load_preview_timing(nuke, str(tmp_path / 'scripts/shot_v001.nk'),
                                  str(tmp_path / 'plates/source.mov'))
    assert (timing['first'], timing['last']) == (1004, 1023)
    assert timing['node_class'] == 'TimeWarp'
    assert '11.25' in timing['knobs']
    assert timing['read_knobs']['frame'] == 'frame + 0.25'
    nuke.scriptClear.assert_called_once_with(ignoreUnsavedChanges=True)


def test_saved_relative_arriraw_is_absolute_before_nuke_loads_its_knobs(tmp_path, monkeypatch):
    import nuke_headless_tasks as module

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    plates = tmp_path / "plates"
    plates.mkdir()
    source = plates / "source clip.mxf"
    source.touch()
    (plates / "reference.mxf").touch()
    script = scripts / "shot_v001.nk"
    saved = (
        'Read {\n file "../plates/source clip.mxf"\n'
        ' arri_colorspace "AWG3 / D65 / LogC3"\n name Read1\n}\n'
        'Read {\n file ../plates/reference.mxf\n name Reference\n}\n'
    )
    script.write_text(saved)
    cache = tmp_path / "cache"
    monkeypatch.setattr(module.app_cache, "CACHE_ROOT", cache)
    nuke, read, _ = scene(tmp_path)
    read["file"].value.return_value = str(source)

    def open_script(path):
        copy = Path(path)
        assert copy.parent.parent == cache / "temp"
        assert copy.read_text() == saved.replace("../plates/source clip.mxf", source.resolve().as_posix())

    nuke.scriptOpen.side_effect = open_script
    timing = _load_preview_timing(nuke, str(script), str(source))

    assert (timing["first"], timing["last"]) == (1004, 1023)
    assert "11.25" in timing["knobs"]
    nuke.root()["name"].setValue.assert_called_once_with(script.as_posix())
    assert script.read_text() == saved
    assert not Path(nuke.scriptOpen.call_args.args[0]).exists()


def test_ambiguous_plate_fails_instead_of_using_wrong_timing(tmp_path):
    nuke, read, pools = scene(tmp_path)
    pools['Read'].append(node('Read', file='../other/source.mov'))
    with pytest.raises(ValueError, match='uniquely'):
        _load_preview_timing(nuke, str(tmp_path / 'scripts/shot_v001.nk'), '/different/mount/source.mov')


def test_conflicting_cut_ranges_fail(tmp_path):
    nuke, read, pools = scene(tmp_path)
    pools['Viewer'].append(node('Viewer', frame_range='1001-1030', frame_range_lock=True))
    with pytest.raises(ValueError, match='Conflicting'):
        _load_preview_timing(nuke, str(tmp_path / 'scripts/shot_v001.nk'), str(tmp_path / 'plates/source.mov'))
    timing = _load_preview_timing(nuke, str(tmp_path / 'scripts/shot_v001.nk'),
                                  str(tmp_path / 'plates/source.mov'), use_project_range=True)
    assert (timing['first'], timing['last']) == (1001, 1030)
    assert timing['node_class'] == 'TimeWarp'


def test_individual_preview_passes_saved_timing_to_headless_command(tmp_path):
    from unittest.mock import patch
    import nuke_headless_tasks as module
    executable, source = tmp_path / 'Nuke', tmp_path / 'source.mov'
    executable.touch()
    source.touch()
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    script = scripts / 'shot_v001.nk'
    script.touch()
    process = Mock(returncode=0)
    process.stdout.readline.return_value = ''
    process.poll.return_value = 0
    with patch.object(module.subprocess, 'Popen', return_value=process) as launch:
        result = module.PreviewGenerator(str(executable)).generate_preview(
            str(source), str(tmp_path), 'shot', plate_name='plate_01')
    assert result.success
    command = launch.call_args.args[0]
    assert command[command.index('--timing-script') + 1] == str(script)


def test_sequence_padding_styles_match_the_same_saved_read(tmp_path):
    nuke, read, pools = scene(tmp_path)
    read["file"].value.return_value = '../plates/source.%04d.exr'
    read["file"].evaluate.return_value = '../plates/source.1001.exr'
    timing = _load_preview_timing(nuke, str(tmp_path / 'scripts/shot_v001.nk'),
                                  str(tmp_path / 'plates/source.####.exr'))
    assert (timing['first'], timing['last']) == (1004, 1023)
