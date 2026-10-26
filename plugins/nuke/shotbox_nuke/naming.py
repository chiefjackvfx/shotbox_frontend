"""Ask for an artist's precomp name before any generation state is allocated."""
from pathlib import Path
import re


def sanitize_live(text):
    """Match PrecompSwitcher's typing rules, including unfinished underscores."""
    return re.sub(r"[^A-Za-z0-9_]", "_", text)


def destination(node, name, script_path):
    """Preview the first free version without reserving or writing anything."""
    from .precomp_paths import normalize_name, preview
    name = normalize_name(sanitize_live(name))
    if node["sb_storage"].value() == "Project precomp":
        template = node["sb_precomp_template"].value().strip() or None
        for version in range(1, 1000000):
            pattern = preview(script_path, name, version=version, template=template)
            if not Path(pattern).parent.exists():
                return {"pattern": pattern, "filename": Path(pattern).name, "version": version}
        raise ValueError("No available precomp version below v1000000.")
    root = Path(node["sb_cache_root"].value()).expanduser()
    if not node["sb_cache_root"].value().strip() or not root.is_absolute():
        raise ValueError("Choose an absolute custom cache root under Advanced.")
    filename = name + ".####.exr"
    return {"pattern": str(root / "<node>" / "<attempt>" / filename), "filename": filename, "version": None}


def make_dialog(node, previous):
    # Keep bundled Qt entirely outside startup and cached-render imports.
    from .naming_dialog import NamingDialog
    return NamingDialog(node, initial_name=previous)


def prompt(node):
    """Return False on cancel/invalid input; save only an accepted valid name.

    Scripted headless callers retain their explicitly configured name. Importing
    this module needs neither Nuke nor Qt, including on render workers.
    """
    import nuke
    from .precomp_paths import normalize_name, preview

    if not nuke.GUI:
        return True
    current = node["sb_precomp_name"].value()
    try:
        previous = normalize_name(current) if current else ""
    except ValueError:
        previous = ""
    dialog = make_dialog(node, previous)
    if not dialog.exec():
        return False
    try:
        name = normalize_name(dialog.precomp_name)
        if node["sb_storage"].value() == "Project precomp":
            preview(nuke.root().name(), name,
                    template=node["sb_precomp_template"].value().strip() or None)
    except (ValueError, OSError) as exc:
        nuke.message("ShotBox precomp: " + str(exc))
        return False
    node["sb_precomp_name"].setValue(name)
    return True
