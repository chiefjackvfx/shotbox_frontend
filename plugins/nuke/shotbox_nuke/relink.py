"""Relink a published sequence without copying pixels or changing shared receipts."""
from copy import deepcopy
from pathlib import Path
import re
import uuid

from . import cache


def _selection_pattern(selection, descriptor):
    value = str(selection).strip()
    match = re.fullmatch(r"(.+\.exr)(?:\s+-?\d+(?:--?\d+)?(?:x\d+)?)?", value)
    if match is None:
        raise ValueError("Choose a matte EXR frame or a #### / %04d EXR sequence.")
    selected = Path(match[1]).expanduser()
    if not selected.is_absolute():
        raise ValueError("Choose an absolute EXR sequence path.")
    directory = selected.parent.resolve()
    name = selected.name
    if "####" in name:
        name = name.replace("####", "%04d")
    if "%" in name:
        if name.count("%04d") != 1 or "%" in name.replace("%04d", ""):
            raise ValueError("The selected EXR sequence must use one #### or %04d frame token.")
    else:
        frame = re.fullmatch(r"(.*?)(-?\d+)\.exr", name)
        if frame is None:
            raise ValueError("The selected EXR filename needs a padded frame number.")
        prefix, number = frame.groups()
        chosen = int(number)
        if not descriptor["first"] <= chosen <= descriptor["last"] and number.startswith("-"):
            prefix, number, chosen = prefix + "-", number[1:], -chosen
        if not descriptor["first"] <= chosen <= descriptor["last"]:
            raise ValueError("The selected frame is outside the recorded matte timeline.")
        name = prefix + "%04d.exr"
        if name % chosen != selected.name:
            raise ValueError("The selected EXR filename must use four-digit frame padding.")
    return directory, name


def _new_receipt(node):
    from . import preferences
    prefs = preferences.load()
    application = Path(prefs.get("application_root") or Path(__file__).resolve().parents[3]).expanduser()
    root = Path(prefs["cache_root"]).expanduser() if prefs.get("cache_root") else application / ".shotbox-cache"
    owner = str(uuid.UUID(node["sb_uuid"].value()))
    directory = root.resolve() / "relinks" / owner / str(uuid.uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    return directory / "integration.json"


def _knobs(node):
    return [(knob, knob.toScript()) for knob in node.allKnobs()]


def _snapshot(node):
    """Include presentation/status values and internal Read expressions."""
    from . import readback
    children = list(node.nodes())
    parent = readback._parent(node)
    peers = list(readback._nodes(parent))
    owned = readback.find(node)
    knobs = _knobs(node)
    for child in children:
        knobs.extend(_knobs(child))
    if owned is not None:
        knobs.extend(_knobs(owned))
    connections = [(peer, index, peer.input(index)) for peer in peers for index in range(peer.inputs())]
    return knobs, connections, parent, {peer.fullName() for peer in peers}


def _restore(node, snapshot):
    import nuke
    from . import readback
    knobs, connections, parent, previous = snapshot
    errors = []
    for peer, index, source in connections:
        try:
            if peer.input(index) is not source:
                peer.setInput(index, source)
        except Exception as exc:
            errors.append(exc)
    # readback.install ordinarily removes its own failed new Read. Cover an
    # exception after creation as well, while leaving other graph nodes alone.
    for peer in readback._nodes(parent):
        tag = peer.knob("sb_read_owner")
        if peer.fullName() not in previous and tag is not None and tag.value() == node["sb_uuid"].value():
            try:
                nuke.delete(peer)
            except Exception as exc:
                errors.append(exc)
    for knob, script in reversed(knobs):
        try:
            if knob.toScript() != script:
                knob.fromScript(script)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise RuntimeError("Could not fully restore the previous ShotBox reference: " + str(errors[0]))


def relink(node, selection, manifest_path=None):
    """Verify moved/renamed EXRs, then install a new private receipt atomically.

    Complete original receipts remain untouched, so copied Groups and Reads
    retain their chosen versions. Selection never changes the recorded range.
    """
    if str(selection).strip().lower().endswith(".json"):
        from . import reload_result
        return reload_result(node, str(selection).strip())
    source = manifest_path or node["sb_active_manifest"].value() or node["sb_pending_manifest"].value()
    if not source:
        raise ValueError("This node has no published matte receipt to relink.")
    original = cache.resolve_manifest_path(source)
    manifest = cache.read_manifest(original, require_complete=True)
    if manifest.get("storage", {}).get("mode") != "published" or not manifest.get("precomp"):
        raise ValueError("EXR relinking needs a complete published matte receipt; choose its integration.json for a legacy cache.")
    candidate = deepcopy(manifest)
    descriptor = candidate["precomp"]
    directory, pattern = _selection_pattern(selection, descriptor)
    candidate["storage"]["output_directory"] = str(directory)
    descriptor["pattern"] = pattern
    for frame, record in zip(range(descriptor["first"], descriptor["last"] + 1), descriptor["files"]):
        record["path"] = pattern % frame
    candidate["relink"] = {"source_manifest": str(original), "previous_output_directory": manifest["storage"]["output_directory"],
                           "previous_pattern": manifest["precomp"]["pattern"], "at": cache.utc_now()}
    candidate["updated_at"] = cache.utc_now()
    # Verify every selected image before even reserving a private receipt.
    # The selected EXR folder is never written to.
    report = cache.validate_published_manifest(candidate, original, checksums=True)
    from .render import decode_outputs
    from .node import activate
    from .readback import install
    decode_outputs(report)
    snapshot = _snapshot(node)
    receipt = _new_receipt(node)
    undo_started = False
    try:
        cache.atomic_write_json(receipt, candidate)
        report = cache.validate_cache(receipt, checksums=True)
        import nuke
        if not nuke.Undo.disabled():
            nuke.Undo.begin("Relink ShotBox matte")
            undo_started = True
        activate(node, receipt)
        install(node, receipt)
        return report
    except Exception:
        try:
            if snapshot is not None:
                _restore(node, snapshot)
        finally:
            receipt.unlink(missing_ok=True)
            try:
                receipt.parent.rmdir()
            except OSError:
                pass
        raise
    finally:
        if undo_started:
            nuke.Undo.end()
