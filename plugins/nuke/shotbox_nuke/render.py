"""Strict cached rendering; standard library plus Nuke, never a backend process."""
import uuid
import math
from pathlib import Path

from . import freshness

_capture_writes = {}
_validating = False
_checked_writes = {}


def register_capture_write(write, node):
    token = str(uuid.uuid4())
    _capture_writes[token] = (write, node)
    return token


def unregister_capture_write(token):
    _capture_writes.pop(token, None)


def _disabled(node, start, end):
    knob = node.knob('disable')
    if knob is None:
        return False
    try:
        return all(bool(knob.valueAt(frame)) for frame in range(start, end + 1))
    except Exception:
        return False


def _precomp_bypass(node, start, end):
    knob = node.knob('sb_use_precomp') if freshness.is_shotbox(node) else None
    if knob is None:
        return False
    try:
        return all(not bool(knob.valueAt(frame)) for frame in range(start, end + 1))
    except Exception:
        return False


def contributing_nodes(write, start, end):
    """Conservative pipe/expression/link traversal, respecting proven bypass."""
    seen = set()
    todo = [write]
    while todo:
        current = todo.pop()
        key = current.fullName()
        if key in seen:
            continue
        seen.add(key)
        if _disabled(current, start, end) or _precomp_bypass(current, start, end):
            # Native disabled operators use their primary input; hidden and
            # expression dependencies of the disabled effect do not contribute.
            upstream = current.input(0)
            if upstream is not None:
                todo.append(upstream)
            continue
        yield current
        if freshness.is_shotbox(current):
            from .node import precomp_active
            if precomp_active(current):
                # A finished RGBA precomp has no live upstream pixel dependency.
                continue
        todo.extend(freshness.dependencies(current))


def _range(write, start, end):
    import nuke
    if start is None or end is None:
        if write.knob('use_limit') is not None and write['use_limit'].value():
            start, end = write['first'].value(), write['last'].value()
        else:
            start, end = nuke.root()['first_frame'].value(), nuke.root()['last_frame'].value()
    if int(start) != start or int(end) != end or end < start:
        raise RuntimeError('Cannot establish the requested integer render range.')
    return int(start), int(end)


def decode_outputs(report):
    """Request the whole raster through Nuke's own image decoder.

    Full-area sampling works inside beforeRender; nested nuke.execute does
    not. The three RGB requests evaluate all rows, without backend or Qt.
    """
    if not report["outputs"]:
        _decode_precomp(report)
        return
    import nuke
    from .capture import preserve_host
    with preserve_host(), nuke.root():
        read = nuke.nodes.Read(name='ShotBoxValidateRead')
        try:
            first = Path(report['outputs'][0])
            read['file'].setValue(str(first.with_name(first.name.rsplit('.', 2)[0] + '.%04d.exr')))
            read['first'].setValue(1)
            read['last'].setValue(len(report['outputs']))
            read['raw'].setValue(True)
            read['on_error'].setValue('error')
            read['localizationPolicy'].setValue('off')
            read['frame_mode'].setValue('expression')
            read['frame'].setValue('frame')
            capture = report['manifest']['capture']
            width, height = capture['width'], capture['height']
            for frame in range(1, len(report['outputs']) + 1):
                nuke.frame(frame)
                if read.width() != width or read.height() != height:
                    raise RuntimeError('Nuke decoded cache dimensions differ from the capture.')
                box = read.bbox()
                if (box.x(), box.y(), box.w(), box.h()) != (0, 0, width, height) or read.format().pixelAspect() != 1.0:
                    raise RuntimeError('Nuke decoded cache data window or pixel aspect differs from the capture.')
                for channel in ('red', 'green', 'blue'):
                    average = read.sample(channel, width / 2, height / 2, width, height)
                    if not math.isfinite(average):
                        raise RuntimeError('Nuke decoded non-finite cache data.')
        finally:
            nuke.delete(read)
    if report.get('precomp_files'):
        _decode_precomp(report)


def _decode_precomp(report):
    import nuke
    from .capture import preserve_host
    descriptor = report['manifest']['precomp']
    root = Path(report.get('precomp_root', report['attempt_root']))
    with preserve_host(), nuke.root():
        read = nuke.nodes.Read(name='ShotBoxValidatePrecomp')
        try:
            read['file'].setValue(str(root / descriptor['pattern']))
            read['first'].setValue(descriptor['first'])
            read['last'].setValue(descriptor['last'])
            read['raw'].setValue(True)
            read['on_error'].setValue('error')
            read['localizationPolicy'].setValue('off')
            read['frame_mode'].setValue('expression')
            read['frame'].setValue('frame')
            for frame, record in zip(range(descriptor['first'], descriptor['last'] + 1), descriptor['files']):
                nuke.frame(frame)
                box = read.bbox()
                actual = [int(box.x()), int(box.y()), int(box.x() + box.w()), int(box.y() + box.h())]
                if actual != record['box'] or read.width() != record['geometry']['width'] or read.height() != record['geometry']['height']:
                    raise RuntimeError('Nuke decoded precomp geometry differs from the published version.')
                if set(read.channels()) != set(record['channels']) or read.format().pixelAspect() != record['pixel_aspect']:
                    raise RuntimeError('Nuke decoded precomp channels or pixel aspect differ.')
                left, bottom, right, top = actual
                channels = ('alpha',) if descriptor.get('kind') == 'alpha' else ('red', 'green', 'blue', 'alpha')
                for channel in channels:
                    value = read.sample(channel, (left + right) / 2, (bottom + top) / 2,
                                        max(1, right - left), max(1, top - bottom))
                    if not math.isfinite(value):
                        raise RuntimeError('Nuke decoded non-finite precomp RGBA data.')
        finally:
            nuke.delete(read)


def validate_node(node, checksums=True, decode=True):
    from .cache import validate_cache
    name = node.fullName()
    path = node['sb_active_manifest'].value()
    if not path:
        raise RuntimeError(name + ': Not generated. Generate and validate a matte, or explicitly disable the node.')
    try:
        report = validate_cache(path, checksums=checksums)
        saved = report['manifest'].get('fingerprint') or report['manifest'].get('freshness')
        if not isinstance(saved, dict):
            raise RuntimeError('Manifest has no input fingerprint; regenerate the cache.')
        current = freshness.fingerprint(node)
        state = freshness.compare(saved, current)
        report['freshness'] = state
        if state != 'no detected change' and not report['manifest'].get('precomp'):
            reasons = '; '.join(current.get('unresolved', []))
            raise RuntimeError('Input freshness is ' + state + (': ' + reasons if reasons else '') + '. Restore the recorded input/settings or Generate again.')
        if report['manifest'].get('precomp'):
            report['snapshot'] = True
        if decode:
            decode_outputs(report)
        return report
    except Exception as exc:
        raise RuntimeError(name + ': Cache validation failed: ' + str(exc)) from exc


def validate_write(write, start=None, end=None, *, checksums=True, decode=True):
    start, end = _range(write, start, end)
    exempt = {owner.fullName() for registered, owner in _capture_writes.values() if registered is write}
    reports = []
    for current in contributing_nodes(write, start, end):
        if freshness.is_shotbox(current) and current.fullName() not in exempt:
            reports.append(validate_node(current, checksums=checksums, decode=decode))
    return reports


def before_render():
    import nuke
    if not _validating:
        write = nuke.thisNode()
        _checked_writes[write.fullName()] = set()
        reports = validate_write(write)
        _checked_writes[write.fullName()] = {report['manifest_path'] for report in reports}


def after_render():
    import nuke
    _checked_writes.pop(nuke.thisNode().fullName(), None)


def before_frame_render():
    import nuke
    if not _validating:
        # An explicit -F range may differ from root.first/last. Recheck the
        # actual rendered frame, including animated/expression disable.
        frame = int(nuke.frame())
        write = nuke.thisNode()
        checked = _checked_writes.setdefault(write.fullName(), set())
        for node in contributing_nodes(write, frame, frame):
            if freshness.is_shotbox(node):
                path = node['sb_active_manifest'].value()
                full = str(Path(path).resolve()) not in checked if path else True
                report = validate_node(node, checksums=full, decode=full)
                checked.add(report['manifest_path'])


def farm_preflight(script=None, write=None, start=None, end=None):
    """Explicit startup contract; missing package import must fail the farm job.

    Call after importing shotbox_nuke from the farm's Nuke startup, before any
    execute call. A saved native Group alone cannot register Python guards.
    """
    import nuke
    from . import lifecycle
    lifecycle.register()
    if script:
        nuke.scriptOpen(str(script))
    if write is not None:
        selected = nuke.toNode(write) if isinstance(write, str) else write
        if selected is None:
            raise RuntimeError('Requested farm Write was not found: ' + str(write))
        return validate_write(selected, start, end)
    writes = [node for node in nuke.allNodes(recurseGroups=True) if node.Class() in ('Write', 'DeepWrite')]
    if not writes:
        raise RuntimeError('The script has no production Write to validate.')
    return [report for output in writes for report in validate_write(output, start, end)]
