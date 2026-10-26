from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke import precomp_paths


def test_default_matches_precomp_switcher_location_and_name(tmp_path):
    script = tmp_path / "bam030" / "comp" / "bam030_comp_v001.nk"
    expected = tmp_path / "bam030/renders/precomp/hero_matte_v001/hero_matte_v001.####.exr"
    assert precomp_paths.preview(script, "hero matte", template=precomp_paths.DEFAULT_TEMPLATE) == str(expected)
    assert not expected.parent.exists()


def test_config_is_parsed_without_executing_code(tmp_path):
    config = tmp_path / "precomp_config.py"
    marker = tmp_path / "should_not_exist"
    template = "{script_dir}/custom/{precomp_name}{ver}/{shot_code}.{write_name}{ver}.####.exr"
    config.write_text(f"raise RuntimeError('not imported')\nWRITE_FILE_TEMPLATE = {template!r}\n"
                      f"open({str(marker)!r}, 'w').write('unsafe')\n")
    assert precomp_paths.config_template(config) == template
    assert not marker.exists()


def test_config_discovery_prefers_loaded_installation(tmp_path, monkeypatch):
    config = tmp_path / "precomp_config.py"
    config.write_text("WRITE_FILE_TEMPLATE = '/configured/{precomp_name}{ver}/file.####.exr'\n")
    monkeypatch.setitem(sys.modules, "precomp_config", SimpleNamespace(__file__=str(config)))
    assert precomp_paths.config_template() == "/configured/{precomp_name}{ver}/file.####.exr"


def test_config_discovery_checks_import_path_without_import(tmp_path, monkeypatch):
    config = tmp_path / "precomp_switcher/precomp_config.py"
    config.parent.mkdir()
    config.write_text("WRITE_FILE_TEMPLATE = '/from_path/{precomp_name}{ver}/file.####.exr'\n")
    monkeypatch.delitem(sys.modules, "precomp_config", raising=False)
    monkeypatch.delitem(sys.modules, "precomp_switcher.precomp_config", raising=False)
    monkeypatch.setattr(sys, "path", [str(tmp_path)])
    assert precomp_paths.config_template() == "/from_path/{precomp_name}{ver}/file.####.exr"


def test_missing_configuration_uses_shared_default(tmp_path, monkeypatch):
    monkeypatch.setattr(precomp_paths, "_config_candidates", lambda: iter([tmp_path / "absent"]))
    assert precomp_paths.config_template() == precomp_paths.DEFAULT_TEMPLATE
    config = tmp_path / "precomp_config.py"
    config.write_text("OTHER_SETTING = True\n")
    assert precomp_paths.config_template(config) == precomp_paths.DEFAULT_TEMPLATE


@pytest.mark.parametrize("source", ["WRITE_FILE_TEMPLATE = None", "WRITE_FILE_TEMPLATE = ''",
                                   "WRITE_FILE_TEMPLATE = get_template()", "WRITE_FILE_TEMPLATE = 123"])
def test_nonliteral_or_disabled_config_requires_override(tmp_path, source):
    config = tmp_path / "precomp_config.py"
    config.write_text(source)
    with pytest.raises(ValueError, match="template|TEMPLATE"):
        precomp_paths.config_template(config)


def test_allocate_skips_all_existing_versions_without_overwriting(tmp_path):
    script = tmp_path / "shot/comp/test.nk"
    first = Path(precomp_paths.preview(script, "hero", template=precomp_paths.DEFAULT_TEMPLATE)).parent
    first.mkdir(parents=True)
    marker = first / "existing_precomp.exr"
    marker.write_bytes(b"keep this PrecompSwitcher output")
    first.with_name("hero_v002").write_bytes(b"also occupied")
    allocation = precomp_paths.allocate(script, "hero", template=precomp_paths.DEFAULT_TEMPLATE)
    assert allocation == {"directory": first.with_name("hero_v003"),
                          "pattern": str(first.with_name("hero_v003") / "hero_v003.####.exr"),
                          "version": 3, "precomp_name": "hero", "template": precomp_paths.DEFAULT_TEMPLATE}
    assert allocation["directory"].is_dir()
    assert list(allocation["directory"].iterdir()) == []
    assert marker.read_bytes() == b"keep this PrecompSwitcher output"


def test_atomic_allocations_do_not_share_a_version(tmp_path):
    def reserve(_):
        return precomp_paths.allocate(tmp_path / "shot/comp/test.nk", "hero", precomp_paths.DEFAULT_TEMPLATE)
    with ThreadPoolExecutor(max_workers=6) as pool:
        allocations = list(pool.map(reserve, range(12)))
    assert sorted(item["version"] for item in allocations) == list(range(1, 13))
    assert len({item["directory"] for item in allocations}) == 12


def test_shot_and_write_name_tokens(tmp_path):
    template = "{script_dir}/../../out/{shot_code}_{precomp_name}{ver}/{write_name}{ver}.####.{ext}"
    assert precomp_paths.preview(tmp_path / "bam 030/comp/test.nk", "matte", 102, template) == str(
        tmp_path / "out/bam_030_matte_v102/Write_matte_v102.####.exr")


@pytest.mark.parametrize("name", ["", ".", "..", "../hero", "hero/matte", "hero\\matte", "hero\x00matte"])
def test_traversal_and_separator_names_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="name"):
        precomp_paths.preview(tmp_path / "test.nk", name)


@pytest.mark.parametrize("script", [None, "", "Root"])
def test_save_required_for_script_relative_templates(script):
    with pytest.raises(ValueError, match="Save the Nuke script"):
        precomp_paths.preview(script, "hero", template=precomp_paths.DEFAULT_TEMPLATE)


def test_absolute_template_can_be_used_before_script_save(tmp_path):
    template = str(tmp_path) + "/{precomp_name}{ver}/{precomp_name}{ver}.####.exr"
    assert precomp_paths.preview("Root", "hero", template=template).startswith(str(tmp_path))


@pytest.mark.parametrize("template", [
    "{script_dir}/precomp/{precomp_name}/file{ver}.####.exr",
    "{script_dir}/precomp/{precomp_name}/file.####.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/file.####.png",
    "{script_dir}/precomp/{precomp_name}{ver}/file.%04d.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/file.####.####.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/{unknown}.####.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/{precomp_name.__class__}.####.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/{precomp_name!r}.####.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/{precomp_name:20}.####.exr",
    "[value root.name]/{precomp_name}{ver}/file.####.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/bad:name.####.exr",
    "{script_dir}/precomp/{precomp_name}{ver}/####.exr",
    "relative/{precomp_name}{ver}/file.####.exr",
])
def test_invalid_or_unsafe_templates_rejected(tmp_path, template):
    with pytest.raises(ValueError):
        precomp_paths.preview(tmp_path / "test.nk", "hero", template=template)


@pytest.mark.parametrize("version", [0, -1, True, "1", 1.2])
def test_invalid_versions_rejected(tmp_path, version):
    with pytest.raises(ValueError, match="positive integer"):
        precomp_paths.preview(tmp_path / "test.nk", "hero", version, precomp_paths.DEFAULT_TEMPLATE)


@pytest.mark.parametrize(("script", "expected"), [
    (r"C:\jobs\bam030\comp\shot.nk", "C:/jobs/bam030/renders/precomp/hero_v001/hero_v001.####.exr"),
    (r"\\studio\jobs\bam030\comp\shot.nk", "//studio/jobs/bam030/renders/precomp/hero_v001/hero_v001.####.exr"),
])
def test_windows_and_unc_preview_are_platform_independent(script, expected):
    assert precomp_paths.preview(script, "hero", template=precomp_paths.DEFAULT_TEMPLATE) == expected
