"""Install a normal Read beside the generator in its parent node graph."""
from pathlib import Path
import re
import uuid


def _rect(node):
    """Use the actual DAG dimensions, including expanded Read thumbnails."""
    width, height = node.screenWidth() or 80, node.screenHeight() or 18
    if node.Class() == "BackdropNode":
        width, height = node["bdwidth"].value(), node["bdheight"].value()
    return (node.xpos(), node.ypos(), node.xpos() + width, node.ypos() + height)


def _overlaps(a, b, margin=16):
    return (a[0] < b[2] + margin and a[2] + margin > b[0]
            and a[1] < b[3] + margin and a[3] + margin > b[1])


def _clear_position(rect, obstacles, margin=16):
    for bounds, backdrop in obstacles:
        if not backdrop:
            if _overlaps(rect, bounds, margin):
                return False
        elif _overlaps(rect, bounds, margin):
            # A Read can live inside a backdrop, clear of its border and title.
            if not (rect[0] >= bounds[0] + margin and rect[2] <= bounds[2] - margin
                    and rect[1] >= bounds[1] + backdrop and rect[3] <= bounds[3] - margin):
                return False
    return True


def _position(node, read, siblings):
    """Choose the nearest available space to the right of the generator."""
    source = _rect(node)
    width, height = max(80, read.screenWidth()), max(60, read.screenHeight())
    preferred = (source[2] + 70, source[1])
    obstacles = [(_rect(candidate), max(48, int(candidate["note_font_size"].value() * 1.5) + 12)
                  if candidate.Class() == "BackdropNode" else 0)
                 for candidate in siblings if candidate is not read]
    xs, ys = {preferred[0]}, {preferred[1]}
    for bounds, backdrop in obstacles:
        xs.update((bounds[0] - width - 24, bounds[2] + 24))
        ys.update((bounds[1] - height - 24, bounds[3] + 24))
        if backdrop:
            xs.update((bounds[0] + 24, bounds[2] - width - 24))
            ys.update((bounds[1] + backdrop + 8, bounds[3] - height - 24))
    # Limit local search before taking a Cartesian product. Large production
    # DAGs can otherwise allocate millions of candidates and scan every node
    # for each one. The unconditional outer-bound fallback remains available.
    xs = sorted(xs, key=lambda x: (abs(x - preferred[0]), x))[:32]
    ys = sorted(ys, key=lambda y: (abs(y - preferred[1]), y))[:32]
    candidates = sorted(((int(x), int(y)) for x in xs for y in ys),
                        key=lambda point: ((point[0] - preferred[0]) ** 2 + (point[1] - preferred[1]) ** 2,
                                           point[0] < source[2], abs(point[1] - preferred[1]), point))
    for x, y in candidates:
        if _clear_position((x, y, x + width, y + height), obstacles):
            return x, y
    # A point beyond all graph bounds is always clear, even with nested backdrops.
    return int(max(bounds[2] for bounds, _ in obstacles) + 24), int(preferred[1])


def _parent(node):
    import nuke
    name = node.fullName().rpartition(".")[0]
    return nuke.toNode(name) if name else nuke.root()


def _nodes(parent):
    import nuke
    return nuke.allNodes(group=parent, recurseGroups=False)


def _tag(node, name, value):
    import nuke
    knob = node.knob(name)
    if knob is None:
        knob = nuke.String_Knob(name, "")
        knob.setVisible(False)
        node.addKnob(knob)
    knob.setValue(str(value))


def find(node):
    """Find the original owned Read, including after an artist renames it.

    Repurposed Reads and ambiguous copied IDs are left alone. Saved Groups
    copied by an artist receive a new owner UUID through the normal lifecycle.
    """
    identity = node.knob("sb_output_read_id")
    if identity is None or not identity.value():
        return None
    owner = node["sb_uuid"].value()
    matches = []
    for candidate in _nodes(_parent(node)):
        if candidate.Class() != "Read":
            continue
        tags = [candidate.knob(key) for key in ("sb_read_owner", "sb_read_id", "sb_read_pattern")]
        if (all(tag is not None for tag in tags) and tags[0].value() == owner
                and tags[1].value() == identity.value() and tags[2].value() == candidate["file"].value()):
            matches.append(candidate)
    saved = node.knob("sb_output_read")
    if saved is not None:
        exact = [candidate for candidate in matches if candidate.fullName() == saved.value()]
        if len(exact) == 1:
            return exact[0]
    return matches[0] if len(matches) == 1 else None


def find_result(node):
    """Resolve a generator or its tagged, unmodified output Read."""
    if node.Class() != "Read":
        return find(node)
    tags = [node.knob(key) for key in ("sb_read_owner", "sb_read_id", "sb_read_pattern")]
    if all(tag is not None and tag.value() for tag in tags) and tags[2].value() == node["file"].value():
        return node
    return None


def reveal(node):
    """Select and frame the output Read in its own parent node graph."""
    import nuke
    read = find_result(node)
    if read is None:
        raise RuntimeError("Generate a matte first; there is no output Read to find.")
    parent = _parent(read)
    nuke.showDag(parent)
    with parent:
        for candidate in nuke.selectedNodes():
            candidate["selected"].setValue(False)
        read["selected"].setValue(True)
        nuke.zoomToFitSelected()
    return read


def view(node):
    """Show the matte as alpha while preserving other Viewer connections."""
    import nuke
    read = find_result(node)
    if read is None:
        raise RuntimeError("Generate a matte first; there is no output Read to view.")
    parent = _parent(read)
    viewer = nuke.activeViewer()
    with parent:
        if viewer is None or _parent(viewer.node()) is not parent:
            # connectViewer chooses a Viewer in the current group. Reserve a
            # slot clear in every candidate so its choice cannot replace an
            # artist's connection, including when crossing into a nested DAG.
            candidates = [candidate for candidate in _nodes(parent) if candidate.Class() == "Viewer"]
            slots = range(min((candidate.maxInputs() for candidate in candidates), default=10))
            free = [index for index in slots
                    if all(candidate.input(index) is None or candidate.input(index) is read
                           for candidate in candidates)]
            if not free:
                raise RuntimeError("The Viewer has no free input. Disconnect one input and try View matte again.")
            nuke.connectViewer(free[0], read)
            viewer = nuke.activeViewer()
        if viewer is None:
            raise RuntimeError("Open a Viewer panel to view the generated matte.")
        target = viewer.node()
        count = target.maxInputs()
        existing = [index for index in range(count) if target.input(index) is read]
        available = [index for index in range(count) if target.input(index) is None]
        if not existing and not available:
            raise RuntimeError("The active Viewer has no free input. Disconnect one input and try View matte again.")
        index = (existing or available)[0]
        target.setInput(index, read)
        viewer.activateInput(index)
        target["channels"].setValue("alpha")
    return viewer


def install(node, manifest_path):
    """Validate, create/update the main-graph Read and switch connected outputs.

    Loading a script never calls this function. Explicit completion/reload does.
    Original connections and an existing Read are restored if installation fails.
    The generated EXRs remain available for a retry.
    """
    import nuke
    from .cache import resolve_owned, validate_cache
    report = validate_cache(manifest_path, checksums=True)
    path = Path(report["manifest_path"])
    data = report["manifest"]
    descriptor = data.get("precomp")
    if not descriptor:
        return None  # Older alpha-only results continue using their Group.
    pattern = str(resolve_owned(Path(report["precomp_root"]), descriptor["pattern"])).replace("\\", "/")
    parent = _parent(node)
    connections = [(candidate, index) for candidate in _nodes(parent) if candidate is not node
                   for index in range(candidate.inputs()) if candidate.input(index) is node]
    read = find(node)
    created = read is None
    prior = []
    group_prior = [(key, node[key].toScript()) for key in ("sb_output_read", "sb_output_read_id")
                   if node.knob(key) is not None]
    selected = nuke.selectedNodes()
    undo_started = not nuke.Undo.disabled()
    if undo_started:
        nuke.Undo.begin("Read ShotBox precomp")
    try:
        with parent:
            if created:
                name = data.get("precomp_request", {}).get("name") or path.parent.name
                read = nuke.nodes.Read(name="Read_" + re.sub(r"[^A-Za-z0-9_]", "_", name))
                metadata = nuke.Tab_Knob("sb_read_metadata", "ShotBox")
                read.addKnob(metadata)
                read.addKnob(nuke.Text_Knob("sb_read_note", "",
                    "This Read uses the rendered EXR sequence.<br>"
                    "Use ShotBoxMatte to generate another version."))
                identity = str(uuid.uuid4())
            else:
                identity = read["sb_read_id"].value()
                keys = ("file", "first", "last", "origfirst", "origlast", "frame_mode", "frame", "raw",
                        "before", "after", "on_error", "localizationPolicy", "sb_read_manifest", "sb_read_pattern")
                prior = [(key, read[key].toScript()) for key in keys if read.knob(key) is not None]
            read["file"].setValue(pattern)
            for key in ("first", "origfirst"):
                read[key].setValue(descriptor["first"])
            for key in ("last", "origlast"):
                read[key].setValue(descriptor["last"])
            read["raw"].setValue(True)
            read["frame_mode"].setValue("expression")
            read["frame"].setValue("frame")
            read["before"].setValue("black")
            read["after"].setValue("black")
            read["on_error"].setValue("error")
            read["localizationPolicy"].setValue("off")
            _tag(read, "sb_read_owner", node["sb_uuid"].value())
            _tag(read, "sb_read_id", identity)
            _tag(read, "sb_read_manifest", path)
            _tag(read, "sb_read_pattern", pattern)
            if created:
                read.setXYpos(*_position(node, read, _nodes(parent)))
            for consumer, index in connections:
                consumer.setInput(index, read)
            _tag(node, "sb_output_read", read.fullName())
            _tag(node, "sb_output_read_id", identity)
        return read
    except Exception:
        for consumer, index in connections:
            consumer.setInput(index, node)
        if created and read is not None:
            nuke.delete(read)
        elif read is not None:
            for key, script in reversed(prior):
                read[key].fromScript(script)
        for key, script in group_prior:
            node[key].fromScript(script)
        raise
    finally:
        if undo_started:
            nuke.Undo.end()
        for candidate in nuke.selectedNodes():
            candidate["selected"].setValue(False)
        for candidate in selected:
            try:
                candidate["selected"].setValue(True)
            except (ValueError, RuntimeError):
                pass
