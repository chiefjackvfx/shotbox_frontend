"""Native layout migration preserves saved controls, animation and callbacks.

Run with Nuke -t; no inference or Qt UI is required.
"""
import argparse
import json
from pathlib import Path
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nuke  # noqa: E402
import shotbox_nuke  # noqa: E402
from shotbox_nuke import capture, node, ui  # noqa: E402

VALUE_KEYS = ("sb_start", "sb_end", "sb_height", "sb_width", "sb_color", "sb_output", "sb_cache_root",
              "sb_seed", "sb_offload", "sb_precision", "sb_temporal", "sb_low_vram", "sb_schema", "sb_version",
              "sb_uuid", "sb_active_manifest", "sb_active_attempt", "sb_active_start", "sb_active_end", "sb_pending_manifest",
              "sb_active_capture", "sb_storage", "sb_precomp_name", "sb_precomp_path", "sb_use_precomp", "sb_precomp_template")
BUTTON_KEYS = ("sb_generate", "sb_cancel", "sb_reload", "sb_validate", "sb_relink", "sb_folder", "sb_log", "sb_preferences", "sb_force")


def snapshot(group):
    return {"values": {name: group[name].toScript() for name in VALUE_KEYS},
            "commands": {name: group[name].toScript() for name in BUTTON_KEYS},
            "expressions": {name: group.node(name)["which"].toScript() for name in ("SBRange", "SBOutputChoice", "SBPrecompSwitch")}}


def verify(group, expected):
    assert snapshot(group) == expected, {"expected": expected, "actual": snapshot(group)}
    assert group["sb_ui_version"].value() == ui.REVISION
    assert not group["sb_status"].visible() and not group["sb_plan"].visible()
    for name in BUTTON_KEYS:
        assert group[name].getFlag(nuke.STARTLINE) == (name not in ("sb_cancel", "sb_validate", "sb_log")), name
    assert group["sb_seed"].isAnimated()
    assert group["sb_seed"].valueAt(1001) == 101 and group["sb_seed"].valueAt(1025) == 202
    assert group["sb_start"].hasExpression() and group["sb_start"].value() == 1001
    tab_labels = [knob.label() for knob in group.allKnobs() if isinstance(knob, nuke.Tab_Knob)]
    for label in ("ShotBox", "Precomp", "Advanced"):
        assert tab_labels.count(label) == 1, tab_labels
    assert "User" not in tab_labels, tab_labels
    assert not group["sb_width"].enabled() and not group["sb_force"].enabled() and not group["sb_precomp_path"].enabled()
    assert group["sb_cache_root"].visible() == (group["sb_storage"].value() == "Custom cache")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=Path("docs/nuke-ui-host.json"))
    parser.add_argument("--directory", type=Path, default=Path("test-runs/nuke-ui"))
    args = parser.parse_args()
    args.directory = args.directory.resolve()
    args.directory.mkdir(parents=True, exist_ok=True)
    report = {"host": capture.host_identity(), "gui": nuke.GUI, "passed": []}
    original_apply = ui.apply
    try:
        assert nuke.NUKE_VERSION_STRING == "17.1v2"
        shotbox_nuke.register()
        anchor = nuke.Int_Knob("ui_frame_start", "UI migration fixture frame")
        anchor.setValue(1001)
        nuke.root().addKnob(anchor)
        nuke.root()["first_frame"].setValue(1001)
        nuke.root()["last_frame"].setValue(1025)
        source = nuke.nodes.Constant(name="UILayoutFixtureSource")
        ui.apply = lambda group: None
        group = node.create()
        ui.apply = original_apply
        group.setName("ShotBox_UI_Migration")
        group.setInput(0, source)
        group["sb_start"].setExpression("root.ui_frame_start")
        group["sb_end"].setValue(1025)
        group["sb_height"].setValue(384)
        group["sb_color"].setValue("acescg")
        group["sb_precomp_name"].setValue("preserve_ui_values")
        group["sb_cache_root"].setValue(str(args.directory / "custom cache"))
        group["sb_storage"].setValue("Custom cache")
        group["sb_seed"].setAnimated()
        group["sb_seed"].setValueAt(101, 1001)
        group["sb_seed"].setValueAt(202, 1025)
        group["sb_precision"].setValue("float")
        group["sb_low_vram"].setValue(True)
        group["sb_use_precomp"].setValue(False)
        group["sb_active_capture"].setValue(json.dumps({"fixture": "preserve hidden values", "start": 1001}))
        # Save an actual previous-layout script before applying the redesign.
        legacy = args.directory / "legacy-layout.nk"
        migrated = args.directory / "migrated-layout.nk"
        nuke.scriptSaveAs(str(legacy), overwrite=True)
        expected = snapshot(group)
        original_apply(group)
        verify(group, expected)
        original_apply(group)
        verify(group, expected)
        report["passed"].append("old native knob layout migrates and reapplies idempotently with all control values, animations, expressions, hidden references and action commands preserved")
        nuke.scriptSaveAs(str(migrated), overwrite=True)
        nuke.scriptOpen(str(migrated))
        group = nuke.toNode("ShotBox_UI_Migration")
        verify(group, expected)
        assert not group["sb_use_precomp"].value()
        report["passed"].append("migrated native script saves and reopens with identical knob data and live bypass choice")
        nuke.scriptOpen(str(legacy))
        group = nuke.toNode("ShotBox_UI_Migration")
        verify(group, expected)
        report["passed"].append("opening an actual never-generated previous-layout script automatically applies the new properties layout without losing values")
        report.update(status="passed", legacy_script=str(legacy), migrated_script=str(migrated), snapshot=expected)
    except Exception:
        report.update(status="failed", error=traceback.format_exc())
        raise
    finally:
        ui.apply = original_apply
        args.report.write_text(json.dumps(report, indent=2))
    print("SHOTBOX_UI_HOST_PASS " + str(args.report))


if __name__ == "__main__":
    main()
