"""Asset naming changes must not hide or duplicate existing project assets."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asset_paths import asset_path


@pytest.mark.parametrize('canonical,legacy', [('job_assets', 'Job_Assets'),
                                            ('timeline_assets', 'Timeline_Assets'),
                                            ('shot_assets', 'Shot_Assets')])
def test_new_folders_are_lowercase_and_existing_title_case_is_reused(tmp_path, canonical, legacy):
    assert asset_path(tmp_path, canonical) == tmp_path / canonical
    assert list(tmp_path.iterdir()) == []  # Resolving does not create directories.
    original = tmp_path / legacy
    original.mkdir()
    marker = original / 'artist_asset.txt'
    marker.write_text('keep me')
    assert asset_path(tmp_path, canonical) == original
    assert asset_path(tmp_path, canonical, 'new_file.txt') == original / 'new_file.txt'
    if (tmp_path / canonical).exists():
        assert (tmp_path / canonical).samefile(original)
    assert marker.read_text() == 'keep me'


def test_lowercase_wins_but_legacy_children_remain_discoverable(tmp_path):
    lower = tmp_path / 'shot_assets'
    legacy = tmp_path / 'Shot_Assets'
    lower.mkdir()
    legacy.mkdir(exist_ok=True)
    (legacy / 'matchmove').mkdir()
    (legacy / 'timeline_import.json').write_text('{"legacy": true}')
    assert asset_path(tmp_path, 'shot_assets') == lower
    assert asset_path(tmp_path, 'shot_assets', 'matchmove') == legacy / 'matchmove'
    assert asset_path(tmp_path, 'shot_assets', 'timeline_import.json') == legacy / 'timeline_import.json'
    (lower / 'timeline_import.json').write_text('{"lowercase": true}')
    assert asset_path(tmp_path, 'shot_assets', 'timeline_import.json') == lower / 'timeline_import.json'


@pytest.mark.parametrize('folder_name', ['shot_assets', 'Shot_Assets'])
def test_shot_metadata_and_matchmove_paths_support_both_spellings(tmp_path, folder_name):
    import matchmove_helpers
    import widgets
    from thumbnail_updates import _timeline_thumbnail_metadata

    root = tmp_path / 'shot'
    assets = root / folder_name
    (assets / 'matchmove').mkdir(parents=True)
    (assets / 'timeline_import.json').write_text('{"cut_duration": 20}')
    assert Path(matchmove_helpers.build_shot_assets_directory(str(root))) == assets
    assert Path(widgets.build_shot_assets_directory(str(root))) == assets
    assert Path(matchmove_helpers.build_shot_matchmove_directory(str(root))) == assets / 'matchmove'
    assert Path(widgets.build_shot_matchmove_directory(str(root))) == assets / 'matchmove'
    class Folders:
        def convert_path(self, path):
            return path
    assert _timeline_thumbnail_metadata({'base_path': str(root)}, Folders()) == {'cut_duration': 20}


def test_import_reuses_title_case_assets_without_creating_duplicate_folders(tmp_path):
    from import_xml_v2 import NukeScriptGenerator, ParsedClip, ParsedShot, XMLImportPage

    source = tmp_path / 'source.mxf'
    source.write_bytes(b'media')
    vfx = tmp_path / 'VFX'
    timeline = vfx / 'edit'
    shot_root = timeline / 'sho010'
    for assets in (vfx / 'Job_Assets', timeline / 'Timeline_Assets', shot_root / 'Shot_Assets'):
        assets.mkdir(parents=True)
        (assets / 'artist_asset.txt').write_text('keep me')
    legacy_metadata = shot_root / 'Shot_Assets' / 'timeline_import.json'
    legacy_metadata.write_text('{"legacy": true}')
    clip = ParsedClip('plate', str(source), 0, 20, 20, 0, 20, 1)
    shot = ParsedShot(0, 'sho010', [clip], duration=20, cut_duration=20)
    folder = NukeScriptGenerator().create_shot_folder(shot, str(tmp_path), 'edit')
    assert Path(folder, 'Shot_Assets', 'timeline_import.json').read_text() == '{"legacy": true}'
    page = XMLImportPage.__new__(XMLImportPage)
    single = Path(page._create_single_shot_folder_structure(str(timeline), 'sho020', None))
    assert (single / 'shot_assets').is_dir()
    assert not (single / 'shot_assets' / 'timeline_import.json').exists()
    for root, canonical, legacy in ((vfx, 'job_assets', 'Job_Assets'),
                                     (timeline, 'timeline_assets', 'Timeline_Assets'),
                                     (shot_root, 'shot_assets', 'Shot_Assets')):
        assert (root / legacy / 'artist_asset.txt').read_text() == 'keep me'
        if (root / canonical).exists():
            assert (root / canonical).samefile(root / legacy)  # Case-insensitive filesystems.
