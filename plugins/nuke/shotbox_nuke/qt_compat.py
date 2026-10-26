"""The only supported GUI binding is the one shipped with Nuke 17.1v2."""


def qt_core():
    import nuke
    if not nuke.GUI:
        raise RuntimeError("Generation requires interactive Nuke; cached rendering is headless safe.")
    from PySide6 import QtCore
    return QtCore
