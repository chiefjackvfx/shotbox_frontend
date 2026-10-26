"""Naming control flow with fake Nuke; native dialog evidence is separate."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "plugins/nuke"))
from shotbox_nuke import naming, precomp_paths


class Knob:
    def __init__(self, value):
        self.saved = value
        self.writes = []

    def value(self):
        return self.saved

    def setValue(self, value):
        self.saved = value
        self.writes.append(value)


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    state = SimpleNamespace(accepted=True, answer="hero", title=None, inputs=[], messages=[], roots=0,
                            script=str(tmp_path / "bam030/comp/shot.nk"))

    class Dialog:
        def __init__(self, node, previous):
            state.title = "Name precomp"
            state.inputs.append(("Precomp name", previous))
            self.precomp_name = state.answer

        def exec(self):
            return state.accepted

    def root():
        state.roots += 1
        return SimpleNamespace(name=lambda: state.script)

    nuke = SimpleNamespace(GUI=True, root=root, message=state.messages.append)
    monkeypatch.setitem(sys.modules, "nuke", nuke)
    monkeypatch.setattr(naming, "make_dialog", Dialog)
    state.nuke = nuke
    state.node = {"sb_precomp_name": Knob(""), "sb_storage": Knob("Project precomp"),
                  "sb_precomp_template": Knob(precomp_paths.DEFAULT_TEMPLATE)}
    monkeypatch.setattr(precomp_paths, "allocate", lambda *args, **kwargs: pytest.fail("Naming allocated output"))
    return state


def test_new_node_prompts_blank_and_saves_normalized_artist_name(dialog, tmp_path):
    dialog.answer = "  hero foreground matte  "
    assert naming.prompt(dialog.node) is True
    assert dialog.title == "Name precomp"
    assert dialog.inputs == [("Precomp name", "")]
    assert dialog.node["sb_precomp_name"].value() == "hero_foreground_matte"
    assert not (tmp_path / "bam030").exists()


def test_regeneration_prefills_previous_artist_name(dialog):
    dialog.node["sb_precomp_name"].saved = "hero_foreground"
    dialog.answer = "hero_foreground"
    assert naming.prompt(dialog.node) is True
    assert dialog.inputs == [("Precomp name", "hero_foreground")]


def test_cancel_preserves_name_without_path_checks_or_allocation(dialog):
    dialog.accepted = False
    dialog.answer = "new name"
    dialog.node["sb_precomp_name"].saved = "previous"
    assert naming.prompt(dialog.node) is False
    assert dialog.node["sb_precomp_name"].value() == "previous"
    assert dialog.node["sb_precomp_name"].writes == []
    assert dialog.roots == 0
    assert dialog.messages == []


@pytest.mark.parametrize("answer", ["", "   ", "../hero", "hero/matte", "hero\\matte", ".."])
def test_invalid_names_report_and_preserve_previous_name(dialog, answer):
    dialog.node["sb_precomp_name"].saved = "previous"
    dialog.answer = answer
    assert naming.prompt(dialog.node) is False
    assert dialog.node["sb_precomp_name"].value() == "previous"
    assert dialog.node["sb_precomp_name"].writes == []
    assert len(dialog.messages) == 1
    assert dialog.roots == 0


@pytest.mark.parametrize("failure", ["unsaved", "invalid_template"])
def test_invalid_project_destination_reports_without_changing_name(dialog, failure):
    dialog.node["sb_precomp_name"].saved = "previous"
    if failure == "unsaved":
        dialog.script = "Root"
    else:
        dialog.node["sb_precomp_template"].saved = "{script_dir}/{precomp_name}/file.####.exr"
    assert naming.prompt(dialog.node) is False
    assert dialog.node["sb_precomp_name"].value() == "previous"
    assert dialog.node["sb_precomp_name"].writes == []
    assert len(dialog.messages) == 1


def test_custom_cache_accepts_name_without_saved_script(dialog):
    dialog.node["sb_storage"].saved = "Custom cache"
    dialog.script = "Root"
    assert naming.prompt(dialog.node) is True
    assert dialog.node["sb_precomp_name"].value() == "hero"
    assert dialog.roots == 0


def test_headless_generation_retains_configured_name_without_dialog(dialog):
    dialog.nuke.GUI = False
    dialog.node["sb_precomp_name"].saved = "scripted_name"
    assert naming.prompt(dialog.node) is True
    assert dialog.node["sb_precomp_name"].writes == []
    assert dialog.title is None
    assert dialog.roots == 0


@pytest.mark.parametrize(("text", "expected"), [
    ("hero foreground", "hero_foreground"), ("hero ", "hero_"),
    ("_hero_", "_hero_"), ("hero  matte", "hero__matte"),
    ("hero-matte.v2", "hero_matte_v2"), ("hero\tmatte", "hero_matte"),
])
def test_live_names_follow_precomp_switcher_rules(text, expected):
    assert naming.sanitize_live(text) == expected


def test_destination_previews_first_free_version_without_creating_it(dialog):
    first = Path(precomp_paths.preview(dialog.script, "hero", version=1))
    first.parent.mkdir(parents=True)
    info = naming.destination(dialog.node, "hero", dialog.script)
    assert info["version"] == 2
    assert info["pattern"] == precomp_paths.preview(dialog.script, "hero", version=2)
    assert not Path(info["pattern"]).parent.exists()
