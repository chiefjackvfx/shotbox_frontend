"""ShotBox Nuke integration: startup stays independent of Qt and the backend."""
__version__ = "0.1.0"


def register():
    from .lifecycle import register
    register()


def create():
    from .node import create
    return create()


def edit_preferences():
    import nuke
    from . import preferences
    values = preferences.load()
    values["application_root"] = values["application_root"] or str(preferences.application_root())
    panel = nuke.Panel("ShotBox preferences")
    labels = (("application_root", "ShotBox application directory"), ("interpreter", "External Python override"),
              ("model_root", "Model root"), ("model_config", "Optional model configuration"),
              ("cache_root", "Preferred persistent cache root"))
    for key, label in labels:
        panel.addSingleLineInput(label, values[key])
    if panel.show():
        preferences.save({key: panel.value(label) for key, label in labels})


def result_manifest(node):
    """Choose the latest completed attempt, preserving an explicit relink."""
    from . import cache
    candidates = []
    for key in ("sb_pending_manifest", "sb_active_manifest"):
        value = node[key].value()
        if not value:
            candidates.append(None)
            continue
        try:
            path = cache.resolve_manifest_path(value)
            data = cache.read_manifest(path, require_complete=True)
            candidates.append((path, data))
        except (OSError, ValueError):
            candidates.append(None)
    pending, active = candidates
    if active and (not pending or pending[1]["attempt_id"] == active[1]["attempt_id"]):
        return active[0]
    if pending:
        return pending[0]
    if active:
        return active[0]
    raise RuntimeError("No completed matte is available. Render a matte first.")


def job_manifest(node):
    """Diagnostics always describe the most recent attempt, including failure."""
    from .cache import resolve_manifest_path
    value = node["sb_pending_manifest"].value() or node["sb_active_manifest"].value()
    if not value:
        raise RuntimeError("No job is available yet. Render a matte first.")
    return resolve_manifest_path(value)


def reload_result(node, path=None):
    from pathlib import Path
    from . import cache, freshness
    from .node import activate
    path = Path(path) if path else result_manifest(node)
    report = cache.validate_cache(path, checksums=True)
    current = freshness.fingerprint(node)
    status = freshness.compare(report["manifest"]["fingerprint"], current)
    if status != "no detected change" and not report["manifest"].get("precomp"):
        raise RuntimeError("Cache input is %s. Restore the captured input/settings or Generate a new result." % status)
    from .render import decode_outputs
    decode_outputs(report)
    activate(node, path)
    if report["manifest"].get("precomp"):
        from .readback import install
        install(node, path)


def action(name):
    import nuke
    from pathlib import Path
    try:
        node = nuke.thisNode()
        if name == "preferences":
            edit_preferences()
        elif name == "generate":
            from .controller import generate
            generate(node)
        elif name in ("cancel", "force"):
            from .controller import cancel
            cancel(node, force=name == "force")
        elif name == "reload":
            reload_result(node)
        elif name == "relink":
            path = nuke.getFilename("Relink matte EXR sequence", "*.exr *.json")
            if path:
                from .relink import relink
                relink(node, path)
        elif name in ("find", "view"):
            from . import readback
            readback.reveal(node) if name == "find" else readback.view(node)
        elif name == "validate":
            from .render import validate_node
            from .node import set_status
            report = validate_node(node)
            set_status(node, "Precomp valid; reading saved EXRs; upstream " + report["freshness"]
                       if report["manifest"].get("precomp") else "Valid cache; full checksums verified; no detected input change")
        elif name in ("folder", "log"):
            from .qt_compat import qt_core
            from PySide6.QtGui import QDesktopServices
            from .cache import resolve_manifest_path
            path = (resolve_manifest_path(Path(node["sb_active_manifest"].value()))
                    if name == "folder" and node["sb_active_manifest"].value() else job_manifest(node))
            if not path.is_file():
                raise RuntimeError("No attempt is available yet. Generate first.")
            if name == "folder":
                from .cache import validate_cache
                target = Path(validate_cache(path)["output_dir"])
            else:
                target = path.parent / "stderr.log"
                if not target.is_file():
                    from .cache import read_manifest
                    data = read_manifest(path)
                    nuke.message("ShotBox: " + (data.get("message") or data.get("error") or data["state"])
                                 + "\nTemporary files removed. Job receipt: " + str(path))
                    return
            QDesktopServices.openUrl(qt_core().QUrl.fromLocalFile(str(target)))
        else:
            raise ValueError("Unknown ShotBox action: " + name)
    except Exception as exc:
        nuke.message("ShotBox: " + str(exc))
