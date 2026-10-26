"""Conservative graph provenance. A match means only 'no detected change'.

All Nuke inspection runs in the caller's main thread. Media identities use
size/mtime, not pixel proofs; unchanged metadata can hide external replacement.
"""
import hashlib
import json
import os
import re
from pathlib import Path

# Presentation and transient host UI state do not affect evaluated pixels.
COSMETIC = frozenset(('name', 'selected', 'xpos', 'ypos', 'label', 'tile_color',
    'gl_color', 'note_font', 'note_font_size', 'note_font_color', 'help', 'panel',
    'icon', 'indicators', 'hide_input', 'cached', 'dope_sheet', 'bookmark',
    'postage_stamp', 'postage_stamp_frame', 'group_view_position', 'show_group_view',
    'disable_group_view', 'mapsize', 'window', 'lock_connections', 'export_as_gizmo',
    'rootNodeUpdated', 'color_panelDropped'))
SETTINGS = ('sb_start', 'sb_end', 'sb_color', 'sb_height', 'sb_width', 'sb_seed',
    'sb_offload', 'sb_low_vram', 'sb_precision', 'sb_temporal')
UPSTREAM_RESULT = ('sb_output', 'sb_use_precomp', 'sb_active_manifest', 'sb_active_attempt',
    'sb_active_start', 'sb_active_end')
ROOT_KNOBS = ('fps', 'format', 'colorManagement', 'OCIO_config', 'customOCIOConfigPath',
    'workingSpaceLUT', 'monitorLut', 'int8Lut', 'int16Lut', 'logLut', 'floatLut',
    'ocio_context', 'ocio_context_variables', 'views', 'project_directory')


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                    separators=(',', ':')).encode()).hexdigest()


def is_shotbox(node):
    return node.knob('sb_schema') is not None


def dependencies(node, *, live=False):
    """All pipes, expressions and link dependencies, including nested Groups."""
    import nuke
    if is_shotbox(node) and not live:
        from .node import precomp_active
        if precomp_active(node):
            return []
    flags = 0
    for key in ('INPUTS', 'HIDDEN_INPUTS', 'EXPRESSIONS', 'LINKINPUTS', 'LINKNODE_INPUTS'):
        flags |= getattr(nuke, key, 0)
    if node.Class() == 'Group' and not is_shotbox(node):
        # Follow only reachable Output -> internal Input -> external sockets.
        # Native Group.dependencies(INPUTS) includes unused external sockets.
        found = list(node.dependencies(flags & ~getattr(nuke, 'INPUTS', 0)))
        found.extend(child for child in node.nodes() if child.Class() == 'Output')
    else:
        found = list(node.dependencies(flags))
    if node.Class() == 'Input':
        parent_name = node.fullName().rpartition('.')[0]
        parent = nuke.toNode(parent_name) if parent_name else None
        if parent is not None:
            external = parent.input(int(node['number'].value()))
            if external is not None:
                found.append(external)

    return found


def _identity(path):
    resolved = Path(path).expanduser().resolve()
    stat = resolved.stat()
    return {'path': str(resolved), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def _media_identities(value, scanned):
    """Conservatively stat all numbered sequence members, including retimes.

    A TimeOffset/TimeWarp can request source frames outside the capture range.
    Recording every matching member avoids guessing transformed source time.
    This can mark a cache stale when an unused member changes or is added.
    """
    path = Path(os.path.expandvars(value)).expanduser()
    token = re.search(r'%0?\d*d|#+', path.name)
    if token is None:
        # Some Tcl expressions expand the frame token before this API returns.
        token = re.search(r'-?\d+(?=\.[^.]+$)', path.name)
    if token is None:
        return [_identity(path)]
    expression = re.escape(path.name[:token.start()]) + r'-?\d+' + re.escape(path.name[token.end():])
    parent = path.parent.resolve()
    key = (str(parent), expression)
    if key not in scanned:
        matcher = re.compile(expression)
        matches = sorted(candidate for candidate in parent.iterdir()
                         if candidate.is_file() and matcher.fullmatch(candidate.name))
        if not matches:
            raise ValueError('No readable members for source sequence: ' + value)
        scanned[key] = [_identity(candidate) for candidate in matches]
    return scanned[key]



def _root_refs(knob, serialized, unresolved, label, force=False):
    if 'root.' not in serialized:
        return set()
    try:
        expression = bool(knob.hasExpression())
    except (AttributeError, RuntimeError, TypeError):
        expression = False
    if not force and not expression and '[' not in serialized:
        return set()
    if re.search(r'\broot\.(?:[^A-Za-z_]|[A-Za-z_]\w*[$\[])', serialized):
        unresolved.append(label + ': dynamic root expression cannot establish its knob dependencies')
    return set(re.findall(r'\broot\.([A-Za-z_]\w*)', serialized))


def fingerprint(node):
    """Return digest, unresolved reasons, known dependencies and host identity.

    Expressions and animation are serialized, not evaluated at just one GUI
    frame. File knobs are evaluated with an explicit OutputContext at every
    requested timeline frame. Missing/uninspectable sources block strict use.
    """
    import nuke
    unresolved = []
    records = []
    media = []
    scanned = {}
    settings = {}
    root_references = set()
    for key in SETTINGS:
        knob = node.knob(key)
        if knob is not None:
            settings[key] = knob.toScript()
            root_references.update(_root_refs(knob, settings[key], unresolved, node.fullName() + '.' + key))
    try:
        start, end = int(node['sb_start'].value()), int(node['sb_end'].value())
        if not 1 <= end - start + 1 <= 250:
            raise ValueError('frame range must contain 1–250 frames')
    except Exception as exc:
        start = end = 1
        unresolved.append('Generation range: ' + str(exc))
    visited = set()
    try:
        todo = list(dependencies(node, live=True))
    except Exception as exc:
        todo = []
        unresolved.append('Input dependencies: ' + str(exc))
    if node.input(0) is None:
        unresolved.append('No connected input')
    while todo:
        current = todo.pop()
        if current is node or current.fullName() in visited:
            continue
        visited.add(current.fullName())
        try:
            dep_nodes = dependencies(current)
            # Names identify topology and expression targets. Renaming this
            # ShotBox itself is excluded; upstream names retain provenance.
            record = {'name': current.fullName(), 'class': current.Class(),
                      'dependencies': sorted(d.fullName() for d in dep_nodes),
                      'inputs': [current.input(index).fullName() if current.input(index) is not None else None
                                 for index in range(current.inputs())], 'knobs': {}}
            special = is_shotbox(current)
            from .node import precomp_active
            frozen = special and precomp_active(current)
            for key, knob in sorted(current.knobs().items()):
                if key in COSMETIC or key.startswith('sb_') and (not special or key not in SETTINGS + UPSTREAM_RESULT):
                    continue
                if frozen and key in SETTINGS:
                    continue
                if knob.Class() in ('Tab_Knob', 'Text_Knob', 'PyScript_Knob', 'Script_Knob'):
                    continue
                serialized = knob.toScript()
                # 17.1v2 GUI annotates the default Read enum with its resolved
                # space; headless startup serializes the same choice as default.
                # The raw knob, root colour settings and media identity remain
                # in the fingerprint, so real transform changes still invalidate.
                if current.Class() == 'Read' and key == 'colorspace' and re.fullmatch(r'default \([^)]+\)', serialized):
                    serialized = 'default'
                record['knobs'][key] = serialized
                root_references.update(_root_refs(knob, serialized, unresolved, current.fullName() + '.' + key,
                                                  force=current.Class() == 'Expression' and key.startswith('expr')))
                if re.search(r'\[\s*python\b|python\s*\{', serialized, re.IGNORECASE):
                    unresolved.append(current.fullName() + '.' + key + ': Python expression dependencies require explicit upstream nodes/files')
                if knob.Class() == 'File_Knob' and key not in ('proxy',):
                    identities = {}
                    for frame in range(start, end + 1):
                        context = nuke.OutputContext()
                        context.setFrame(frame)
                        value = knob.getEvaluatedValue(context)
                        if not value:
                            continue
                        if '[' in value or ']' in value:
                            raise ValueError('unresolved file expression: ' + value)
                        for identity in _media_identities(value, scanned):
                            identities[identity['path']] = identity
                    media.extend(identities.values())
            records.append(record)
            todo.extend(dep_nodes)
        except Exception as exc:
            unresolved.append(current.fullName() + ': ' + str(exc))
    root = {}
    for key in ROOT_KNOBS:
        knob = nuke.root().knob(key)
        if knob is not None:
            value = knob.toScript()
            # Nuke 17.1v2 -t starts these empty LUT enums at numeric 0;
            # saving/reopening resolves the same index to 'linear'.
            if key in ('workingSpaceLUT', 'int8Lut', 'int16Lut', 'logLut', 'floatLut') and value == '0':
                value = 'linear'
            root[key] = value
            if key == 'customOCIOConfigPath' and knob.value():
                try:
                    context = nuke.OutputContext()
                    context.setFrame(start)
                    path = knob.getEvaluatedValue(context) if hasattr(knob, 'getEvaluatedValue') else knob.value()
                    media.append(_identity(os.path.expandvars(path)))
                except Exception as exc:
                    unresolved.append('Custom OCIO config: ' + str(exc))
    # Nuke's normal dependency API omits Root references. Track only knobs
    # explicitly named by image expressions, recursively, without root UI state.
    referenced = set()
    while root_references:
        key = root_references.pop()
        if key in referenced or key == 'frame':
            continue
        referenced.add(key)
        knob = nuke.root().knob(key)
        if knob is None:
            unresolved.append('root.' + key + ': referenced root knob is missing')
            continue
        try:
            serialized = knob.toScript()
            if key not in root:
                root[key] = serialized
            root_references.update(_root_refs(knob, serialized, unresolved, 'root.' + key))
            if re.search(r'\[\s*python\b|python\s*\{', serialized, re.IGNORECASE):
                unresolved.append('root.' + key + ': Python expression dependencies are unresolved')
            if knob.Class() == 'File_Knob':
                for frame in range(start, end + 1):
                    context = nuke.OutputContext()
                    context.setFrame(frame)
                    value = knob.getEvaluatedValue(context)
                    if value:
                        media.extend(_media_identities(value, scanned))
        except Exception as exc:
            unresolved.append('root.' + key + ': ' + str(exc))
    ocio = {key: value for key, value in sorted(os.environ.items()) if key == 'OCIO' or key.startswith('OCIO_')}
    for key, value in ocio.items():
        if key == 'OCIO' and value:
            try:
                media.append(_identity(value))
            except OSError as exc:
                unresolved.append('OCIO config: ' + str(exc))
    host = {'version': nuke.NUKE_VERSION_STRING, 'executable': str(nuke.env.get('ExecutablePath', ''))}
    payload = {'settings': settings, 'graph': sorted(records, key=lambda item: item['name']),
               'root': root, 'ocio': ocio, 'dependencies': sorted(media, key=lambda item: item['path'])}
    return {'digest': _digest(payload), 'canonical': payload, 'unresolved': sorted(set(unresolved)),
            'dependencies': payload['dependencies'], 'deps': payload['dependencies'], 'host': host,
            'method': 'graph/animation/expression serialization and media size/mtime; not pixel identity'}


def compare(saved, current):
    if current.get('unresolved') or saved.get('unresolved'):
        return 'unverifiable'
    return 'no detected change' if saved.get('digest') == current.get('digest') else 'stale'
