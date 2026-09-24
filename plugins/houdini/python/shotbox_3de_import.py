"""Import 3DE's Houdini Python exports from the ShotBox shelf."""
import os
from pathlib import Path
import tokenize
import traceback


def import_export(filename):
    import hou
    # Houdini's live $JOB/$HIP can differ from Python's inherited os.environ.
    # Let Houdini expand variables before any filesystem normalization.
    path = Path(os.path.expanduser(hou.expandString(filename))).resolve()
    if path.suffix.lower() != ".py":
        raise ValueError("Choose a 3DE Houdini Python export (.py).")
    with tokenize.open(str(path)) as stream:
        code = compile(stream.read(), str(path), "exec")
    # Do not retry execution: an exception may follow successful node creation.
    with hou.undos.group("Import 3DE export"):
        try:
            exec(code, {"hou": hou, "__name__": "__main__", "__file__": str(path)})
        except Exception as exc:
            raise RuntimeError(
                f"Import failed: {path}\n{exc}\nIf nodes were created, use Undo to remove this import."
            ) from exc
    return str(path)


def show_import_menu():
    """Open the file browser directly; keep the entry point for installed shelves."""
    import hou
    try:
        filename = hou.ui.selectFile(
            title="Choose 3DE Houdini Export", pattern="*.py",
            chooser_mode=hou.fileChooserMode.Read,
        )
        if not filename:
            return
        imported = import_export(filename)
        hou.ui.setStatusMessage(f"Imported 3DE export: {imported}")
    except Exception as exc:
        traceback.print_exc()
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)
