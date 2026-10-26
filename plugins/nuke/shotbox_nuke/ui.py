"""Native knob layout, including presentation upgrades for saved Groups."""
import html
import textwrap
import sys

REVISION = "4"
_active = False


def layout_active():
    return _active


def _wrapped(value, width=48):
    lines = []
    for paragraph in str(value).splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width=width, break_long_words=True, break_on_hyphens=False) or [""])
    return "<br>".join(html.escape(line) for line in lines)


def refresh(node):
    """Update progress and enable only actions available for this node."""
    if not hasattr(node, "knob") or node.knob("sb_ui_status") is None:
        return
    node["sb_ui_status"].setValue(_wrapped(node["sb_status"].value()))
    node["sb_ui_plan"].setValue(_wrapped(node["sb_plan"].value()))
    node["sb_cache_root"].setVisible(node["sb_storage"].value() == "Custom cache")
    module = sys.modules.get("shotbox_nuke.controller")
    session = getattr(module, "SESSION", None)
    busy = getattr(session, "current", None) is not None
    run = getattr(module, "RUNS", {}).get(node["sb_uuid"].value())
    local_busy = run is not None and run.data.get("state") in ("probing", "capturing", "preflight", "generating", "validating")
    active = bool(node["sb_active_manifest"].value())
    pending = bool(node["sb_pending_manifest"].value())
    complete = active
    if not complete and pending and not local_busy:
        from . import result_manifest
        try:
            result_manifest(node)
            complete = True
        except (OSError, ValueError, RuntimeError):
            pass
    from .readback import find_result
    result = find_result(node)
    valid_range = 1 <= int(node["sb_end"].value()) - int(node["sb_start"].value()) + 1 <= 250
    valid_source = node.input(0) is not None and node["sb_color"].value() != "Unset"
    states = {"sb_generate": not busy and valid_source and valid_range,
              "sb_cancel": local_busy and run.cancel_time is None,
              "sb_reload": complete and not local_busy, "sb_validate": active and not local_busy,
              "sb_relink": complete and not local_busy, "sb_find": result is not None,
              "sb_view": result is not None, "sb_folder": active,
              "sb_log": active or pending, "sb_use_precomp": active}
    for key, enabled in states.items():
        if node.knob(key) is not None:
            node[key].setEnabled(enabled)
    if not local_busy:
        node["sb_force"].setEnabled(False)
    node["sb_output"].setVisible(node["sb_result_kind"].value() in ("rgba", "legacy"))


def refresh_all():
    """Synchronise the script-wide single-worker lock across open node panels."""
    import sys
    nuke = sys.modules.get("nuke")
    if not callable(getattr(nuke, "allNodes", None)):
        return
    for node in nuke.allNodes(recurseGroups=True):
        try:
            if node.knob("sb_schema") is not None:
                refresh(node)
        except (RuntimeError, ValueError):
            pass  # Script close may detach a node while ownership is released.


def apply(node):
    """Reorder existing knob objects without replacing values or animations."""
    global _active
    import nuke
    if _active:
        return
    revision = node.knob("sb_ui_version")
    if revision is not None and revision.value() == REVISION:
        refresh(node)
        return
    _active = True
    try:
        layout = [
            ("tab", "shotbox", "ShotBox"),
            ("heading", "sb_ui_source", "Source"),
            ("knob", "sb_start", "Frame in"), ("inline", "sb_end", "Out"),
            ("knob", "sb_color", "Input colour"),
            ("heading", "sb_ui_generation", "Render matte"),
            ("knob", "sb_height", "Height"), ("inline", "sb_width", "Width"),
            ("knob", "sb_precomp_name", "Matte name"),
            ("knob", "sb_generate", "Render"), ("inline", "sb_cancel", "Cancel"),
            ("knob", "sb_state", "Result"),
            ("text", "sb_ui_status", "Progress"),
            ("space", "sb_ui_actions_space", ""),
            ("knob", "sb_find", "Find Read"), ("inline", "sb_view", "View Matte"),
            ("knob", "sb_folder", "Open output folder"),
            ("tab", "sb_advanced", "Advanced"),
            ("heading", "sb_ui_location", "Output"),
            ("knob", "sb_precomp_path", "EXR sequence"),
            ("knob", "sb_storage", "Save location"),
            ("knob", "sb_cache_root", "Custom cache root"),
            ("knob", "sb_precomp_template", "Path override"),
            ("heading", "sb_ui_saved", "Saved matte"),
            ("knob", "sb_reload", "Reload result"), ("inline", "sb_validate", "Validate files"),
            ("knob", "sb_relink", "Relink EXRs…"),
            ("heading", "sb_ui_processing", "Processing"),
            ("knob", "sb_seed", "Seed"), ("knob", "sb_offload", "Offload"),
            ("knob", "sb_precision", "Matte precision"),
            ("knob", "sb_temporal", "Frame processing"),
            ("knob", "sb_low_vram", "Tile reference VAE"),
            ("note", "sb_experimental", "Full range is experimental and may exceed VRAM."),
            ("heading", "sb_ui_details", "Diagnostics"),
            ("knob", "sb_capture_area", "Source format"),
            ("text", "sb_ui_plan", "Model fit"),
            ("knob", "sb_log", "Latest job log"),
            ("knob", "sb_preferences", "Preferences…"),
            ("heading", "sb_ui_preview", "Group preview"),
            ("knob", "sb_output", "Legacy preview output"),
            ("knob", "sb_use_precomp", "Preview rendered matte"),
            ("note", "sb_ui_preview_note", "Group preview only. The Read uses the saved EXRs."),
            ("heading", "sb_ui_recovery", "Worker recovery"),
            ("knob", "sb_force", "Force stop worker"),
        ]
        names = {name for _, name, _ in layout} | {"sb_status", "sb_plan", "sb_precomp_note", "sb_precomp_tab",
                                                                 "sb_ui_result", "sb_ui_versions", "sb_ui_configuration", "sb_notice"}
        existing = {name: node.knob(name) for name in names if node.knob(name) is not None}
        for knob in reversed(node.allKnobs()):
            if knob.name() in names and knob.name() != "shotbox":
                node.removeKnob(knob)
        # Raw status and planning strings retain their serialized contract.
        for name in ("sb_status", "sb_plan", "sb_precomp_note", "sb_notice"):
            if name in existing:
                existing[name].setVisible(False)
                node.addKnob(existing[name])
        for kind, name, label in layout:
            # Nuke retains the first user tab while hidden saved knobs remain.
            # Keep that tab as the anchor, then append the reordered controls.
            if name == "shotbox" and node.knob(name) is not None:
                node[name].setLabel(label)
                continue
            # Removed native tabs retain their former node attachment in Nuke
            # when hidden knobs remain. Only tabs are presentation objects;
            # controls keep their original values, expressions and animations.
            knob = None if kind == "tab" else existing.get(name)
            if knob is None:
                if kind == "tab":
                    knob = nuke.Tab_Knob(name, label)
                else:
                    knob = nuke.Text_Knob(name, "", "")
            if kind == "heading":
                knob.setLabel("")
                gap = "" if name in ("sb_ui_source", "sb_ui_location", "sb_ui_processing") else "<br>"
                knob.setValue(gap + "<b>" + html.escape(label) + "</b>")
            elif kind == "space":
                knob.setLabel("")
                knob.setValue(" ")
            elif kind == "note":
                knob.setLabel("")
                knob.setValue(_wrapped(label))
            else:
                knob.setLabel(label)
            knob.setFlag(nuke.STARTLINE)
            if kind == "inline":
                knob.clearFlag(nuke.STARTLINE)
            try:
                node.addKnob(knob)
            except RuntimeError as exc:
                raise RuntimeError("Cannot arrange ShotBox control %s: %s" % (name, exc)) from exc
        if revision is None:
            revision = nuke.String_Knob("sb_ui_version", "")
            revision.setVisible(False)
            node.addKnob(revision)
        revision.setValue(REVISION)
        tooltips = {
            "sb_generate": "Choose a precomp name, generate alpha, then create or update a standalone Read in this node graph.",
            "sb_height": "AI generation height. Width follows the source aspect ratio; the precomp retains source resolution.",
            "sb_width": "Derived AI generation width. The fitted and padded model dimensions appear under Advanced.",
            "sb_color": "Describe the incoming RGB values. No Nuke Write colour transform is applied during capture.",
            "sb_use_precomp": "Group preview only. On: preview the finished precomp. Off: preview the live input. The standalone Read is independent.",
            "sb_precision": "Precision of the saved alpha-only EXRs. They are read as raw data, without a colour transform.",
            "sb_find": "Select and frame the owned output Read in its node graph.",
            "sb_view": "Connect the matte to a Viewer and display its alpha channel.",
            "sb_relink": "Choose a moved or renamed EXR sequence; every frame is checked before changing the Read.",
            "sb_log": "Open diagnostics for the latest attempt, including failed or cancelled regeneration.",
            "sb_force": "Available 30 seconds after requesting cancellation. Stops only this node's owned worker.",
        }
        for name, tooltip in tooltips.items():
            node[name].setTooltip(tooltip)
        refresh(node)
    finally:
        _active = False
