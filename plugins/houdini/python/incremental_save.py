# Houdini 22.0 shelf tool
# Incremental Version Up with persistent cache-path protection and an
# animated Longhouse mascot holding a version-up item overhead.

import os
import re
import math
import hou

from PySide6 import QtCore, QtGui, QtWidgets


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

PRESERVE_ALL_HIPNAME_STRINGS = False

# Optional recovery override. If this scene was already versioned by an older
# script and its caches belong to an earlier HIP name, enter that name here
# once, e.g. "shot_v001". Leave empty for automatic detection.
CACHE_HIPNAME_ANCHOR_OVERRIDE = ""

# Stored on the / node in the HIP file so the original cache-name anchor
# survives subsequent Version Up operations.
ANCHOR_USERDATA_KEY = "incremental_save_cache_hipname_anchor_v1"

HIPNAME_PATTERN = re.compile(r"\$\{HIPNAME\}|\$HIPNAME")

PATH_PARAMETER_NAMES = {
    "file", "files", "filename", "filepath", "file_path",
    "path", "cache", "cachefile", "cache_file", "cachepath",
    "cache_path", "directory", "folder", "sopoutput",
    "output", "outputfile", "output_file", "outputpath",
    "output_path", "fileprefix", "file_prefix",
}

RENDER_IMAGE_PARAMETER_NAMES = {
    "vm_picture", "picture", "image", "outputimage",
    "output_image", "renderpicture", "render_picture",
}


# -----------------------------------------------------------------------------
# Cache-reference handling
# -----------------------------------------------------------------------------

def _is_string_parm(parm):
    try:
        return parm.parmTemplate().type() == hou.parmTemplateType.String
    except Exception:
        return False


def _is_render_image_output(parm):
    """Do not pin ordinary rendered-image output paths."""
    try:
        node = parm.node()
        if node.type().category().name().lower() != "driver":
            return False
        pname = parm.name().lower()
        label = parm.parmTemplate().label().lower()
        tokens = set(re.findall(r"[a-z0-9_]+", pname))
        if tokens.intersection(RENDER_IMAGE_PARAMETER_NAMES):
            return True
        if pname in RENDER_IMAGE_PARAMETER_NAMES:
            return True
        if "picture" in label or "image output" in label:
            return True
    except Exception:
        pass
    return False


def is_path_parameter(parm):
    """Identify cache/file paths without excluding geometry-cache ROPs."""
    try:
        node = parm.node()
        node_type = node.type().name().lower()
        if "cache" in node_type:
            return True

        pname = parm.name().lower()
        label = parm.parmTemplate().label().lower()
        tokens = set(re.findall(r"[a-z0-9_]+", pname))
        if tokens.intersection(PATH_PARAMETER_NAMES):
            return True
        if pname in PATH_PARAMETER_NAMES:
            return True

        # Catch custom-named path parms, including SOP/geometry output paths.
        try:
            raw = parm.unexpandedString()
            if ("/" in raw or "\\" in raw) and HIPNAME_PATTERN.search(raw):
                return True
        except Exception:
            pass

        if any(word in label for word in ("file path", "cache path", "output file")):
            return True
    except Exception:
        pass
    return False


def can_edit(parm):
    """Skip parameters Houdini says are locked, referenced or animated."""
    try:
        if parm.isLocked():
            return False
    except Exception:
        pass

    try:
        if not parm.node().isEditableInsideLockedHDA():
            return False
    except Exception:
        pass

    try:
        if parm.getReferencedParm() != parm:
            return False
        if parm.keyframes():
            return False
    except Exception:
        return False

    return True


def _iter_hipname_path_parms(root):
    if root is None:
        return
    try:
        nodes = root.allSubChildren()
    except Exception:
        nodes = []

    for node in nodes:
        try:
            parms = node.parms()
        except Exception:
            continue

        for parm in parms:
            try:
                if not _is_string_parm(parm):
                    continue
                raw = parm.unexpandedString()
                if not HIPNAME_PATTERN.search(raw):
                    continue
                if not PRESERVE_ALL_HIPNAME_STRINGS:
                    if _is_render_image_output(parm):
                        continue
                    if not is_path_parameter(parm):
                        continue
                yield parm, raw
            except Exception:
                continue


def _previous_version_name(name):
    match = re.match(r"^(.*)_([vV])(\d+)$", name)
    if not match:
        return None
    base, marker, digits = match.groups()
    value = int(digits)
    if value <= 1:
        return None
    return "{}_{}{}".format(base, marker, str(value - 1).zfill(len(digits)))


def _path_directory_exists(raw_value, candidate_name):
    """Check whether a candidate HIPNAME points at an existing cache folder."""
    try:
        candidate = HIPNAME_PATTERN.sub(candidate_name, raw_value)
        expanded = hou.expandString(candidate)
        expanded = os.path.expanduser(expanded)
        # Paths commonly end in a frame token or filename; test their parent.
        directory = os.path.dirname(expanded.rstrip("/\\"))
        if not directory:
            return False
        return os.path.isdir(directory)
    except Exception:
        return False


def _choose_cache_anchor(current_name, path_parms):
    """
    Choose a stable cache basename.

    If upgrading a scene that was already versioned by an older script, prefer
    the previous version only when its referenced cache directories exist and
    the current-version directories do not. Otherwise use the current HIP name.
    """
    if CACHE_HIPNAME_ANCHOR_OVERRIDE.strip():
        return CACHE_HIPNAME_ANCHOR_OVERRIDE.strip()

    previous = _previous_version_name(current_name)
    if not previous:
        return current_name

    current_hits = 0
    previous_hits = 0
    for _parm, raw in path_parms:
        if _path_directory_exists(raw, current_name):
            current_hits += 1
        if _path_directory_exists(raw, previous):
            previous_hits += 1

    if previous_hits > current_hits:
        return previous
    return current_name


def _open_version_browser_from_shelf():
    try:
        import browser_version
        browser_version.show_version_browser()
    except Exception as error:
        hou.ui.displayMessage(
            "Unable to open Version Browser:\n{}: {}".format(
                type(error).__name__, error
            ),
            title="Version Up"
        )


def _show_version_success(new_filename):
    dialog = VersionUpSuccessDialog(new_filename, hou.qt.mainWindow())
    dialog.exec()


# -----------------------------------------------------------------------------
# Animated Longhouse character: item held overhead, Zelda-style
# -----------------------------------------------------------------------------

class VersionUpCharacter(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(VersionUpCharacter, self).__init__(parent)
        self.setFixedSize(180, 164)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.phase = 0.0

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _tick(self):
        self.phase += 0.075
        self.update()

    def showEvent(self, event):
        super(VersionUpCharacter, self).showEvent(event)
        if not self.timer.isActive():
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super(VersionUpCharacter, self).hideEvent(event)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        orange = QtGui.QColor("#FF6600")
        dark = QtGui.QColor("#39333D")
        white = QtGui.QColor("#FFFFFF")
        gold = QtGui.QColor("#FFD45A")
        glow = QtGui.QColor("#FFB52E")

        bob = math.sin(self.phase * 2.0) * 1.5
        body_top = 82.0 + bob
        body_bottom = body_top + 43.0

        limb_pen = QtGui.QPen(dark, 4.0)
        limb_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        limb_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)

        # Legs and shoes.
        painter.setPen(limb_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawLine(QtCore.QPointF(48, body_bottom - 3), QtCore.QPointF(39, 145))
        painter.drawLine(QtCore.QPointF(73, body_bottom - 3), QtCore.QPointF(83, 145))
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(orange)
        painter.drawRoundedRect(QtCore.QRectF(31, 141, 17, 8), 3, 3)
        painter.drawRoundedRect(QtCore.QRectF(79, 141, 17, 8), 3, 3)

        # Raised arms, reaching up to hold the item.
        painter.setPen(limb_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        left_arm = QtGui.QPainterPath()
        left_arm.moveTo(39, body_top + 13)
        left_arm.cubicTo(22, body_top + 2, 30, 48, 58, 42)
        painter.drawPath(left_arm)

        right_arm = QtGui.QPainterPath()
        right_arm.moveTo(78, body_top + 13)
        right_arm.cubicTo(98, body_top + 2, 91, 48, 69, 42)
        painter.drawPath(right_arm)

        # Orange body.
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(orange)
        painter.drawRoundedRect(QtCore.QRectF(35, body_top, 48, 43), 3, 3)

        # White spiral mark.
        spiral = QtGui.QPainterPath()
        spiral.moveTo(42, body_top + 12)
        spiral.cubicTo(50, body_top + 4, 65, body_top + 5, 72, body_top + 13)
        spiral.cubicTo(78, body_top + 21, 73, body_top + 31, 65, body_top + 32)
        spiral.cubicTo(56, body_top + 34, 48, body_top + 27, 51, body_top + 21)
        spiral.cubicTo(53, body_top + 16, 61, body_top + 16, 64, body_top + 21)
        spiral.cubicTo(66, body_top + 24, 62, body_top + 27, 59, body_top + 25)
        spiral_pen = QtGui.QPen(white, 3.2)
        spiral_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        spiral_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(spiral_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(spiral)

        # Hands under the item.
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(white)
        painter.drawEllipse(QtCore.QRectF(52, 36, 10, 10))
        painter.drawEllipse(QtCore.QRectF(66, 36, 10, 10))

        # Glowing item held overhead, gently bobbing and sparkling.
        item_bob = math.sin(self.phase * 2.4) * 2.0
        cx, cy = 64.0, 20.0 + item_bob

        glow.setAlpha(65 + int((math.sin(self.phase * 3.0) + 1.0) * 35))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(QtCore.QRectF(cx - 20, cy - 20, 40, 40))

        painter.setPen(QtGui.QPen(dark, 1.4))
        painter.setBrush(gold)
        diamond = QtGui.QPolygonF([
            QtCore.QPointF(cx, cy - 13),
            QtCore.QPointF(cx + 11, cy),
            QtCore.QPointF(cx, cy + 13),
            QtCore.QPointF(cx - 11, cy),
        ])
        painter.drawPolygon(diamond)

        painter.setPen(QtGui.QPen(white, 2.0))
        painter.drawLine(QtCore.QPointF(cx, cy - 8), QtCore.QPointF(cx, cy + 7))
        painter.drawLine(QtCore.QPointF(cx - 6, cy - 1), QtCore.QPointF(cx + 5, cy - 1))

        # Small sparkle marks.
        painter.setPen(QtGui.QPen(gold, 2.0))
        for sx, sy, scale in ((24, 22, 1.0), (105, 26, 0.8), (104, 65, 0.65)):
            pulse = 1.0 + 0.25 * math.sin(self.phase * 3.0 + sx)
            size = 4.0 * scale * pulse
            painter.drawLine(QtCore.QPointF(sx - size, sy), QtCore.QPointF(sx + size, sy))
            painter.drawLine(QtCore.QPointF(sx, sy - size), QtCore.QPointF(sx, sy + size))

        painter.end()


class VersionUpSuccessDialog(QtWidgets.QDialog):
    def __init__(self, filename, parent=None):
        super(VersionUpSuccessDialog, self).__init__(parent)
        self.setWindowTitle("Version Up Complete")
        self.setModal(True)
        self.setWindowFlags(
            QtCore.Qt.WindowType.Dialog
            | QtCore.Qt.WindowType.WindowTitleHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self.setStyleSheet("""
            QDialog { background: #30343A; color: #F2F2F2; }
            QLabel#Heading { color: #F2F2F2; font-size: 16px; font-weight: 600; }
            QLabel#Filename {
                color: #FFB36B; font-size: 13px; font-weight: 600;
                padding: 5px 8px; background: #414750; border-radius: 5px;
            }
            QLabel#Subheading { color: #B8BEC7; font-size: 11px; }
            QPushButton {
                color: #F2F2F2; background: #484E57;
                border: 1px solid #626A75; border-radius: 5px;
                padding: 7px 22px; min-width: 70px;
            }
            QPushButton:hover { background: #59636F; border-color: #FF8A36; }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(7)

        heading = QtWidgets.QLabel("This file has versioned up to")
        heading.setObjectName("Heading")
        heading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        self.character = VersionUpCharacter(self)
        layout.addWidget(self.character, 0, QtCore.Qt.AlignmentFlag.AlignCenter)

        name_label = QtWidgets.QLabel(filename)
        name_label.setObjectName("Filename")
        name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        name_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(name_label)

        subheading = QtWidgets.QLabel("Your new version is now the active HIP file.")
        subheading.setObjectName("Subheading")
        subheading.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subheading)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        version_control_button = QtWidgets.QPushButton("Open Version Control")
        version_control_button.clicked.connect(self.open_version_control)
        buttons.addWidget(version_control_button)

        ok_button = QtWidgets.QPushButton("OK")
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        layout.addLayout(buttons)

        self.setFixedWidth(390)
        self.adjustSize()

    def open_version_control(self):
        self.accept()
        QtCore.QTimer.singleShot(0, _open_version_browser_from_shelf)


# -----------------------------------------------------------------------------
# Incremental save
# -----------------------------------------------------------------------------

def incremental_save():
    changes = []
    root = hou.node("/")
    anchor_was_present = False
    old_anchor = None

    try:
        current_path = hou.hipFile.path()
        if not current_path:
            hou.ui.displayMessage("Save the HIP file once before using Version Up.")
            return None

        current_path = os.path.abspath(current_path)
        directory = os.path.dirname(current_path)
        filename = os.path.basename(current_path)
        name, extension = os.path.splitext(filename)

        if extension.lower() not in (".hip", ".hiplc", ".hipnc"):
            hou.ui.displayMessage("Unsupported Houdini file extension.")
            return None

        match = re.match(r"^(.+)_([vV])(\d+)$", name)
        if not match:
            hou.ui.displayMessage(
                "Filename must end in _v001, for example shot_v001.hip."
            )
            return None

        base_name, marker, digits = match.groups()
        version = int(digits) + 1
        padding = max(3, len(digits))

        while True:
            new_filename = "{}_{}{}{}".format(
                base_name, marker, str(version).zfill(padding), extension
            )
            save_path = os.path.join(directory, new_filename)
            if not os.path.exists(save_path):
                break
            version += 1

        path_parms = list(_iter_hipname_path_parms(root))

        # Keep one stable cache basename across every subsequent version-up.
        try:
            old_anchor = root.userData(ANCHOR_USERDATA_KEY) if root else None
            anchor_was_present = bool(old_anchor)
        except Exception:
            old_anchor = None
            anchor_was_present = False

        if old_anchor:
            cache_anchor = old_anchor
        else:
            cache_anchor = _choose_cache_anchor(name, path_parms)
            if root is not None:
                root.setUserData(ANCHOR_USERDATA_KEY, cache_anchor)

        # Pin eligible cache paths to the persistent anchor, not the current
        # version name. This also retries previously skipped protected parms.
        for parm, old_value in path_parms:
            try:
                if not HIPNAME_PATTERN.search(old_value):
                    continue
                if not can_edit(parm):
                    continue

                new_value = HIPNAME_PATTERN.sub(cache_anchor, old_value)
                if new_value == old_value:
                    continue

                parm.set(new_value, follow_parm_reference=False)
                changes.append((parm, old_value))
            except Exception:
                # Protected parameters are skipped; one locked node must not
                # prevent versioning or edits to other cache references.
                continue

        # Save As creates the next active HIP without overwriting an existing one.
        try:
            hou.hipFile.save(save_path)
        except Exception:
            for parm, old_value in reversed(changes):
                try:
                    parm.set(old_value, follow_parm_reference=False)
                except Exception:
                    pass

            if root is not None and not anchor_was_present:
                try:
                    root.destroyUserData(ANCHOR_USERDATA_KEY)
                except Exception:
                    pass
            raise

        # The success popup is intentionally the only normal success feedback.
        _show_version_success(new_filename)
        return save_path

    except Exception as error:
        hou.ui.displayMessage(
            "Version Up failed:\n{}: {}".format(type(error).__name__, error),
            title="Incremental Save"
        )
        return None


if __name__ == "__main__":
    incremental_save()
