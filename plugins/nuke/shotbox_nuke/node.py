"""Saved native Group and its short package callbacks."""
import json
from pathlib import Path
import sys
import uuid

VERSION = "0.1.0"


def is_shotbox(node):
    try:
        return node.Class() == "Group" and "sb_schema" in node.knobs()
    except (ValueError, RuntimeError):
        # Nuke can return detached wrappers while script-close deletes nodes.
        return False


def set_status(node, text):
    node["sb_status"].setValue(str(text))
    from .ui import refresh
    refresh(node)
    update_state(node)


def update_state(node, state=None):
    """Precomp Switcher-style visual state; never changes graph routing."""
    if node.knob("sb_state") is None:
        return
    if state is None:
        module = sys.modules.get("shotbox_nuke.controller")
        run = getattr(module, "RUNS", {}).get(node["sb_uuid"].value())
        if run is not None and run.data.get("state") in ("probing", "capturing", "preflight", "generating", "validating"):
            state = "Rendering"
        else:
            ready = node.node("SBHasResult")
            state = "Precomped" if ready is not None and ready["which"].value() and node["sb_use_precomp"].value() else "Bypass"
    node["sb_state"].setValue(state)
    node["tile_color"].setValue({"Bypass": 0xE6E6E6FF, "Rendering": 0x3A74BFFF, "Precomped": 0xB65B5BFF}[state])


def precomp_active(node):
    """True when the saved result graph reads a complete published EXR sequence."""
    try:
        mode = node.node("SBResultMode")
        return bool(node["sb_use_precomp"].value() and node.node("SBHasResult")["which"].value()
                    and mode is not None and mode["which"].value() == 1)
    except (KeyError, ValueError, RuntimeError, NameError):
        return False


def refresh_precomp_path(node):
    import nuke
    from . import precomp_paths
    if node.knob("sb_storage") is None:
        return
    try:
        if node["sb_storage"].value() == "Custom cache":
            text = str(Path(node["sb_cache_root"].value()).expanduser() / "<node>" / "<attempt>" / "precomp.%04d.exr")
        else:
            template = node["sb_precomp_template"].value().strip() or None
            name = node["sb_precomp_name"].value()
            if not name.strip():
                node["sb_precomp_path"].setValue("Choose a name when you click Generate.")
                return
            version = 1
            text = precomp_paths.preview(nuke.root().name(), name, version=version, template=template)
            while Path(text).parent.exists():
                version += 1
                text = precomp_paths.preview(nuke.root().name(), name, version=version, template=template)
        node["sb_precomp_path"].setValue(text)
    except (ValueError, OSError) as exc:
        node["sb_precomp_path"].setValue(str(exc))


def upgrade(node, *, new=False):
    """Keep saved alpha-only Groups working; add the full-precomp branch lazily."""
    import nuke
    if node.knob("sb_storage") is None:
        node.addKnob(nuke.Tab_Knob("sb_precomp_tab", "Precomp"))
        node.addKnob(nuke.Text_Knob("sb_state", "State", "Bypass"))
        storage = nuke.Enumeration_Knob("sb_storage", "Save location", ["Project precomp", "Custom cache"])
        storage.setValue("Project precomp" if new else "Custom cache")
        node.addKnob(storage)
        name = nuke.String_Knob("sb_precomp_name", "Precomp name")
        name.setValue("")
        node.addKnob(name)
        path = nuke.File_Knob("sb_precomp_path", "Precomp file")
        path.setEnabled(False)
        node.addKnob(path)
        toggle = nuke.Boolean_Knob("sb_use_precomp", "Use precomp (off = live input)")
        toggle.setValue(True)
        toggle.setFlag(nuke.NO_ANIMATION)
        node.addKnob(toggle)
        template = nuke.String_Knob("sb_precomp_template", "Path template override")
        template.setTooltip("Blank follows PrecompSwitcher's WRITE_FILE_TEMPLATE. Saved comp required for project-relative paths.")
        node.addKnob(template)
        node.addKnob(nuke.Text_Knob("sb_precomp_note", "", "Capture RGB → generate alpha → automatically Read the alpha EXRs. Each render creates a new version."))
    if node.node("SBPrecomp") is None:
        node.begin()
        try:
            read = nuke.nodes.Read(name="SBPrecomp")
            read["raw"].setValue(True)
            read["on_error"].setValue("error")
            read["before"].setValue("black")
            read["after"].setValue("black")
            read["localizationPolicy"].setValue("off")
            result = nuke.nodes.Switch(name="SBResultMode", inputs=[node.node("SBCopy"), read])
            result["which"].setValue(0)
            node.node("SBOutputChoice").setInput(0, result)
            switch = nuke.nodes.Switch(name="SBPrecompSwitch", inputs=[node.node("SBInput"), node.node("SBHasResult")])
            switch["which"].setExpression("parent.sb_use_precomp")
            node.node("SBOutput").setInput(0, switch)
        finally:
            node.end()
    if node.node("SBPrecompMatte") is None:
        node.begin()
        try:
            matte = nuke.nodes.Expression(name="SBPrecompMatte", inputs=[node.node("SBPrecomp")])
            for key, value in (("expr0", "a"), ("expr1", "a"), ("expr2", "a"), ("expr3", "1")):
                matte[key].setValue(value)
            mode = nuke.nodes.Switch(name="SBMatteMode", inputs=[node.node("SBMatteView"), matte])
            mode["which"].setExpression("SBResultMode.which")
            node.node("SBOutputChoice").setInput(1, mode)
        finally:
            node.end()
    if node.node("SBPrecompChannels") is None:
        node.begin()
        try:
            channels = nuke.nodes.AddChannels(name="SBPrecompChannels", inputs=[node.node("SBPrecomp")])
            channels["channels"].setValue("rgba")
            node.node("SBPrecompMatte").setInput(0, channels)
        finally:
            node.end()
    for key in ("sb_output_read", "sb_output_read_id"):
        if node.knob(key) is None:
            _hidden(node, key, "")
    for key in ("sb_find", "sb_view"):
        if node.knob(key) is None:
            button = nuke.PyScript_Knob(key, "Find Read" if key == "sb_find" else "View Matte")
            button.setCommand("import shotbox_nuke; shotbox_nuke.action(%r)" % key[3:])
            node.addKnob(button)
    if node.knob("sb_result_kind") is None:
        _hidden(node, "sb_result_kind", "")
    refresh_precomp_path(node)
    update_state(node)
    from .ui import apply
    apply(node)


def refresh_dimensions(node):
    from .protocol import generation_size
    source = node.input(0)
    if source is not None:
        fmt = source.format()
        width, height = generation_size(fmt.width(), fmt.height(), int(node["sb_height"].value()))
        if int(node["sb_width"].value()) != width:
            node["sb_width"].setValue(width)
        node["sb_capture_area"].setValue("%d × %d; PAR %g" %
                                          (fmt.width(), fmt.height(), fmt.pixelAspect()))


def _hidden(node, name, value):
    import nuke
    knob = nuke.String_Knob(name, name)
    knob.setValue(str(value))
    knob.setVisible(False)
    node.addKnob(knob)


def create():
    """Use native GUI insertion, then replace its temporary node with the Group."""
    import nuke
    if not nuke.GUI:
        return _build()
    # An empty Group has no input yet, so createNode cannot insert it into a
    # selected pipe. Let a native one-input node perform placement/insertion.
    nuke.Undo.begin("Create ShotBoxMatte")
    anchor = None
    group = None
    connections = []
    source = None
    try:
        anchor = nuke.createNode("NoOp", inpanel=False)
        source = anchor.input(0)
        connections = [(candidate, index)
                       for candidate in anchor.dependent(nuke.INPUTS | nuke.HIDDEN_INPUTS)
                       for index in range(candidate.inputs()) if candidate.input(index) is anchor]
        group = _build()
        group.setXYpos(anchor.xpos(), anchor.ypos())
        group.setInput(0, source)
        for candidate, index in connections:
            candidate.setInput(index, group)
        for candidate in nuke.selectedNodes():
            candidate["selected"].setValue(False)
        group["selected"].setValue(True)
        refresh_dimensions(group)
        group.showControlPanel()
        return group
    except Exception:
        for candidate, index in connections:
            candidate.setInput(index, source)
        if group is not None:
            nuke.delete(group)
        raise
    finally:
        if anchor is not None:
            nuke.delete(anchor)
        nuke.Undo.end()


def _build():
    import nuke
    from .preferences import load
    prefs = load()
    group = nuke.nodes.Group(name="ShotBoxMatte")
    group.addKnob(nuke.Tab_Knob("shotbox", "ShotBox"))
    for key, label, value in (
        ("start", "Frame In", int(nuke.root()["first_frame"].value())),
        ("end", "Frame Out", int(nuke.root()["last_frame"].value())),
        ("height", "Generation height", 544), ("width", "Generation width", 960)):
        knob = nuke.Int_Knob("sb_" + key, label)
        knob.setValue(value)
        if key == "width":
            knob.setEnabled(False)
        group.addKnob(knob)
    for key, label, values in (
        ("color", "Input colour encoding", ["Unset", "linear-srgb", "acescg", "srgb"]),
        ("output", "Output", ["Original RGB + generated alpha", "Matte only"])):
        knob = nuke.Enumeration_Knob("sb_" + key, label, values)
        knob.setValue("acescg" if key == "color" else "Matte only")
        group.addKnob(knob)
    cache = nuke.File_Knob("sb_cache_root", "Cache root")
    script = Path(nuke.root().name())
    cache.setValue(prefs.get("cache_root", "") or (str(script.parent / ".shotbox-cache")
                   if script.name != "Root" else ""))
    group.addKnob(cache)
    group.addKnob(nuke.Text_Knob("sb_capture_area", "Capture area", "Input format; square pixels required."))
    group.addKnob(nuke.Text_Knob("sb_plan", "Model dimensions", "Available after capture preflight"))
    group.addKnob(nuke.Text_Knob("sb_notice", "", "Off-format overscan excluded. Source alpha ignored; prepare straight RGB upstream if needed."))
    group.addKnob(nuke.Text_Knob("sb_status", "Status", "Not generated"))
    for key, label in (("generate", "Generate"), ("cancel", "Cancel"), ("reload", "Reload Result"),
                       ("validate", "Validate Cache"), ("relink", "Relink Cache"),
                       ("folder", "Open Output Folder"), ("log", "Open Log"),
                       ("preferences", "Preferences"), ("force", "Force stop worker")):
        knob = nuke.PyScript_Knob("sb_" + key, label)
        knob.setCommand("import shotbox_nuke; shotbox_nuke.action(%r)" % key)
        group.addKnob(knob)
        if key == "force":
            knob.setEnabled(False)
    group.addKnob(nuke.Tab_Knob("sb_advanced", "Advanced"))
    seed = nuke.Int_Knob("sb_seed", "Seed")
    seed.setValue(42)
    group.addKnob(seed)
    for key, label, values in (("offload", "Offload", ["cpu", "none", "disk"]),
                               ("precision", "EXR precision", ["half", "float"]),
                               ("temporal", "Frame processing", ["segmented", "full-range"])):
        group.addKnob(nuke.Enumeration_Knob("sb_" + key, label, values))
    group.addKnob(nuke.Boolean_Knob("sb_low_vram", "Tile reference VAE", False))
    group.addKnob(nuke.Text_Knob("sb_experimental", "", "Full range is experimental: may exceed VRAM; quality needs review."))
    for key, value in (("schema", "1"), ("version", VERSION), ("uuid", uuid.uuid4()),
                       ("active_manifest", ""), ("active_attempt", ""),
                       ("active_start", "0"), ("active_end", "0"), ("pending_manifest", ""),
                       ("active_capture", "")):
        _hidden(group, "sb_" + key, value)
    group.begin()
    try:
        original = nuke.nodes.Input(name="SBInput")
        read = nuke.nodes.Read(name="SBCache")
        read["raw"].setValue(True)
        read["on_error"].setValue("error")
        read["before"].setValue("black")
        read["after"].setValue("black")
        read["localizationPolicy"].setValue("off")
        crop = nuke.nodes.Crop(name="SBSpatialCrop", inputs=[read])
        crop["intersect"].setValue(False)
        crop["reformat"].setValue(False)
        crop["crop"].setValue(True)
        crop["softness"].setValue(0)
        zero = nuke.nodes.Constant(name="SBZero")
        zero["color"].setValue([0, 0, 0, 0])
        ranged = nuke.nodes.Switch(name="SBRange", inputs=[zero, crop])
        ranged["which"].setExpression("frame >= parent.sb_active_start && frame <= parent.sb_active_end")
        # Verified in 17.1v2: B (retained original) is input 0; A is input 1.
        copy = nuke.nodes.Copy(name="SBCopy", inputs=[original, ranged])
        copy["from0"].setValue("rgba.red")
        copy["to0"].setValue("rgba.alpha")
        for key in ("bbox", "metainput", "rangeinput"):
            copy[key].setValue("B")
        matte = nuke.nodes.Expression(name="SBMatteView", inputs=[ranged])
        for channel, expression in (("expr0", "r"), ("expr1", "r"), ("expr2", "r"), ("expr3", "1")):
            matte[channel].setValue(expression)
        choice = nuke.nodes.Switch(name="SBOutputChoice", inputs=[copy, matte])
        choice["which"].setExpression("parent.sb_output")
        ready = nuke.nodes.Switch(name="SBHasResult", inputs=[original, choice])
        ready["which"].setValue(0)
        nuke.nodes.Output(name="SBOutput", inputs=[ready])
    finally:
        group.end()
    # Group onCreate runs before our identifying knobs exist.
    from .lifecycle import reconcile
    upgrade(group, new=True)
    group["label"].setValue("[value sb_state]")
    reconcile()
    return group


def activate(node, manifest_path):
    """Install a fully validated reference, restoring all knobs on an error."""
    from .cache import resolve_owned, validate_cache
    report = validate_cache(manifest_path, checksums=True)
    path = Path(report["manifest_path"])
    data = report["manifest"]
    upgrade(node)
    outputs = report["outputs"]
    sequence = ""
    if outputs:
        first = Path(outputs[0])
        sequence = str(first.with_name(first.name.rsplit(".", 2)[0] + ".%04d.exr"))
    capture = data["capture"]
    read = node.node("SBCache")
    crop = node.node("SBSpatialCrop")
    knobs = [read[k] for k in ("file", "first", "last", "frame_mode", "frame")]
    knobs += [crop["box"], node.node("SBHasResult")["which"]]
    precomp = node.node("SBPrecomp")
    knobs += [precomp[k] for k in ("file", "first", "last", "frame_mode", "frame")]
    knobs += [node.node("SBResultMode")["which"], node["sb_precomp_path"], node["sb_use_precomp"], node["sb_output"],
              node.node("SBOutputChoice")["which"]]
    knobs += [node[k] for k in ("sb_active_manifest", "sb_active_attempt", "sb_active_start", "sb_active_end", "sb_active_capture", "sb_result_kind")]
    prior = [(knob, knob.toScript()) for knob in knobs]
    try:
        read["file"].setValue(sequence.replace("\\", "/"))
        read["first"].setValue(1)
        read["last"].setValue(capture["count"])
        read["frame_mode"].setValue("expression")
        read["frame"].setValue("frame - (%d) + 1" % capture["start"])
        crop["box"].setValue([0, 0, capture["width"], capture["height"]])
        node["sb_active_manifest"].setValue(str(path))
        node["sb_active_attempt"].setValue(data["attempt_id"])
        node["sb_active_start"].setValue(str(capture["start"]))
        node["sb_active_end"].setValue(str(capture["end"]))
        node["sb_active_capture"].setValue(json.dumps(capture, sort_keys=True))
        descriptor = data.get("precomp")
        node["sb_result_kind"].setValue(descriptor.get("kind", "rgba") if descriptor else "legacy")
        choice = node.node("SBOutputChoice")["which"]
        choice.setExpression("1" if descriptor and descriptor.get("kind") == "alpha" else "parent.sb_output")
        if descriptor:
            if descriptor.get("kind") == "alpha":
                node["sb_output"].setValue("Matte only")
            precomp["file"].setValue(str(resolve_owned(Path(report["precomp_root"]), descriptor["pattern"])).replace("\\", "/"))
            precomp["first"].setValue(descriptor["first"])
            precomp["last"].setValue(descriptor["last"])
            precomp["frame_mode"].setValue("expression")
            precomp["frame"].setValue("frame")
            node["sb_precomp_path"].setValue(str(resolve_owned(Path(report["precomp_root"]), descriptor["pattern"])))
            node.node("SBResultMode")["which"].setValue(1)
        else:
            node.node("SBResultMode")["which"].setValue(0)
        node["sb_use_precomp"].setValue(True)
        node.node("SBHasResult")["which"].setValue(1)
        set_status(node, ("Precomped; reading generated alpha" if descriptor.get("kind") == "alpha"
                          else "Precomped; reading rendered RGB + alpha") if descriptor
                   else "Valid cache; no detected input change")
    except Exception:
        for knob, script in reversed(prior):
            knob.fromScript(script)
        raise
