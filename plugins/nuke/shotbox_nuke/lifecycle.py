"""Idempotent headless-safe Nuke hooks and live node identities."""
import sys
import uuid
from pathlib import Path

_registered = False
_reconciling = False
_session = str(uuid.uuid4())
_live = {}
_retired = set()
_callbacks = []


def session_token():
    return _session


def _nodes():
    import nuke
    from .node import is_shotbox
    return [node for node in nuke.allNodes(recurseGroups=True) if is_shotbox(node)]


def _shutdown(node_id=None):
    # Script load/render must never import the GUI process adapter.
    module = sys.modules.get('shotbox_nuke.controller')
    if module is not None and hasattr(module, 'shutdown'):
        module.shutdown(node_id=node_id)


def reconcile():
    """Copy/paste/undo duplicates keep read-only caches but receive fresh IDs."""
    global _reconciling, _live
    if _reconciling:
        return
    _reconciling = True
    try:
        nodes = _nodes()
        by_identity = {}
        # Preserve an already-known owner when a pasted copy sorts before it.
        for node in nodes:
            identity = node['sb_uuid'].value()
            if _live.get(identity) is node:
                by_identity[identity] = node
        for node in nodes:
            identity = node['sb_uuid'].value()
            try:
                uuid.UUID(identity)
                valid = True
            except (ValueError, TypeError, AttributeError):
                valid = False
            if not valid or identity in _retired or identity in by_identity and by_identity[identity] is not node:
                identity = str(uuid.uuid4())
                node['sb_uuid'].setValue(identity)
                # A copy must not recover or cancel the original's attempt.
                if node.knob('sb_pending_manifest') is not None:
                    node['sb_pending_manifest'].setValue('')
            by_identity[identity] = node
        _live = by_identity
    finally:
        _reconciling = False


def _loaded():
    global _session
    from . import cache, node as nodes, render
    _shutdown()
    _session = str(uuid.uuid4())
    _live.clear()
    _retired.clear()
    reconcile()
    for node in _nodes():
        nodes.upgrade(node)
        pending = node['sb_pending_manifest'].value()
        if pending:
            try:
                path = cache.resolve_manifest_path(Path(pending))
                manifest = cache.read_manifest(path)
                from . import storage
                owned_elsewhere = manifest.get('precomp_request', {}).get('output_directory') and storage.owner_is_running(manifest)
                if manifest['state'] not in ('complete', 'failed', 'cancelled', 'interrupted') and not owned_elsewhere:
                    manifest['state'] = 'failed'
                    manifest['error'] = 'Interrupted by script close or host exit; Generate again.'
                    manifest['updated_at'] = cache.utc_now()
                    cache.atomic_write_json(path, manifest)
                    if manifest.get("precomp_request", {}).get("output_directory"):
                        storage.cleanup(path.parent, manifest)
            except Exception:
                # Recovery is informational and cannot manufacture a result.
                pass
        active = node['sb_active_manifest'].value()
        if not active:
            nodes.set_status(node, 'Not generated' + ('; interrupted attempt retained' if pending else ''))
            continue
        try:
            use_precomp = node.knob('sb_use_precomp')
            saved_live = use_precomp is not None and not use_precomp.value()
            render.validate_node(node)
            nodes.activate(node, active)
            if saved_live:
                node['sb_use_precomp'].setValue(False)
                nodes.set_status(node, 'Live input; completed precomp retained')
        except Exception as exc:
            nodes.set_status(node, str(exc))


def _closed():
    global _session
    _session = str(uuid.uuid4())
    _shutdown()
    _live.clear()


def _destroyed():
    import nuke
    from .node import is_shotbox
    node = nuke.thisNode()
    if is_shotbox(node):
        identity = node['sb_uuid'].value()
        module = sys.modules.get('shotbox_nuke.controller')
        run = getattr(module, 'RUNS', {}).get(identity) if module is not None else None
        owns_run = run is not None and getattr(run, 'node', None) is node
        if _live.get(identity) is node or owns_run:
            _shutdown(identity)
            _retired.add(identity)
            _live.pop(identity, None)


def _changed():
    import nuke
    from . import ui
    if _reconciling or ui.layout_active():
        return
    current = nuke.thisNode()
    try:
        current.fullName()
    except (ValueError, RuntimeError):
        return
    knob = nuke.thisKnob()
    key = knob.name() if knob is not None else ''
    from . import freshness
    if key in ('sb_storage', 'sb_status', 'sb_plan', 'inputChange', 'sb_active_manifest', 'sb_pending_manifest', 'sb_output_read_id', 'sb_start', 'sb_end', 'sb_color') and freshness.is_shotbox(current):
        ui.refresh(current)
    if current.Class() == 'Root' and key == 'name':
        from .node import refresh_precomp_path
        for target in _nodes():
            refresh_precomp_path(target)
    if key in ('sb_storage', 'sb_precomp_name', 'sb_precomp_template', 'sb_cache_root') and freshness.is_shotbox(current):
        from .node import refresh_precomp_path
        refresh_precomp_path(current)
    if key == 'sb_use_precomp' and freshness.is_shotbox(current):
        from .node import update_state
        update_state(current)
    if key in ('inputChange', 'sb_height') and freshness.is_shotbox(current):
        from . import node as node_api
        if hasattr(node_api, 'refresh_dimensions'):
            node_api.refresh_dimensions(current)
    if key in ('sb_uuid', 'sb_schema'):
        reconcile()
    # Defer expensive provenance hashing until Generate/Validate/load/render.
    # The status prompt is a hint only; callbacks are never the authority.
    if key in ('sb_start', 'sb_end', 'sb_color', 'sb_height', 'sb_seed',
               'sb_offload', 'sb_low_vram', 'sb_precision', 'sb_temporal'):
        from .node import is_shotbox, set_status
        if is_shotbox(current) and current['sb_active_manifest'].value():
            set_status(current, 'Settings changed for next render; current precomp retained')
    if key in freshness.COSMETIC or key.startswith('sb_') and key not in freshness.SETTINGS + freshness.UPSTREAM_RESULT:
        return
    from .node import set_status
    for target in _nodes():
        if target is current or not target['sb_active_manifest'].value():
            continue
        try:
            todo = freshness.dependencies(target)
            visited = set()
            relevant = current.Class() == 'Root' and key in freshness.ROOT_KNOBS
            while todo and not relevant:
                dependency = todo.pop()
                name = dependency.fullName()
                if name in visited:
                    continue
                visited.add(name)
                if dependency is current:
                    relevant = True
                else:
                    todo.extend(freshness.dependencies(dependency))
            if relevant:
                from .node import precomp_active
                set_status(target, 'Upstream changed; reading frozen precomp (toggle to live input or Generate again)'
                           if precomp_active(target) else 'Upstream changed; Validate Cache or Generate again')
        except Exception:
            set_status(target, 'Input check needed; Validate Cache before rendering')


def register():
    global _registered
    if _registered:
        return
    import nuke
    from . import render
    pairs = [('addBeforeRender', 'removeBeforeRender', render.before_render, {}),
             ('addBeforeFrameRender', 'removeBeforeFrameRender', render.before_frame_render, {}),
             ('addAfterRender', 'removeAfterRender', render.after_render, {}),
             ('addOnScriptLoad', 'removeOnScriptLoad', _loaded, {}),
             ('addOnScriptClose', 'removeOnScriptClose', _closed, {}),
             ('addOnCreate', 'removeOnCreate', reconcile, {'nodeClass': 'Group'}),
             ('addOnDestroy', 'removeOnDestroy', _destroyed, {'nodeClass': 'Group'}),
             ('addKnobChanged', 'removeKnobChanged', _changed, {})]
    try:
        for add, remove, callback, kwargs in pairs:
            getattr(nuke, add)(callback, **kwargs)
            _callbacks.append((remove, callback, kwargs))
        reconcile()
        _registered = True
    except Exception:
        unregister()
        raise


def unregister():
    global _registered
    import nuke
    for remove, callback, kwargs in reversed(_callbacks):
        getattr(nuke, remove)(callback, **kwargs)
    _callbacks.clear()
    _registered = False
