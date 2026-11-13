import os
import re
import json
import shutil
import tempfile
import subprocess
import traceback
import math
import hou

from PySide6 import QtWidgets, QtCore, QtGui


# ============================================================
# SETTINGS
# ============================================================

HIP_EXTENSIONS = (".hip", ".hiplc", ".hipnc")
SCAN_TIMEOUT_SECONDS = 300
SECTION_STYLE = """
QGroupBox#SectionGroup {
    background-color: #363A40;
    border: 1px solid #555B64;
    border-radius: 8px;
    margin-top: 15px;
    padding: 12px 10px 10px 10px;
}
QGroupBox#SectionGroup::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 2px 8px;
    color: #F2F2F2;
    background-color: #484E57;
    border-radius: 4px;
}
"""


def style_section(group):
    group.setObjectName("SectionGroup")
    group.setStyleSheet(SECTION_STYLE)


def clean_node_name(value):
    base = os.path.splitext(os.path.basename(value or ""))[0]
    base = re.sub(r"[^A-Za-z0-9_]", "_", base).strip("_") or "hip"
    if base[0].isdigit():
        base = "hip_" + base
    return base


def canonical_hip_path(path):
    """Return a stable path key, resolving relative paths and symlinks where possible."""
    if not path:
        return ""

    expanded = os.path.expandvars(os.path.expanduser(str(path)))
    absolute = os.path.abspath(os.path.normpath(expanded))

    try:
        absolute = os.path.realpath(absolute)
    except Exception:
        pass

    return os.path.normcase(os.path.normpath(absolute))


def same_hip_file(path_a, path_b):
    """Compare HIP paths robustly, including case, relative paths and aliases."""
    if not path_a or not path_b:
        return False

    try:
        if os.path.exists(path_a) and os.path.exists(path_b):
            if os.path.samefile(path_a, path_b):
                return True
    except (OSError, ValueError, TypeError):
        pass

    return canonical_hip_path(path_a) == canonical_hip_path(path_b)


def is_current_hip_file(path):
    """True when path points to the HIP currently open in this Houdini session."""
    try:
        current_path = hou.hipFile.path()
    except Exception:
        current_path = ""

    return same_hip_file(path, current_path)


def unique_child_name(parent, base_name):
    base = re.sub(r"[^A-Za-z0-9_]", "_", base_name).strip("_") or "merged_hip"
    if base[0].isdigit():
        base = "merged_" + base
    candidate = base
    index = 1
    while parent.node(candidate) is not None:
        index += 1
        candidate = "{}_{}".format(base, index)
    return candidate


def all_scene_nodes():
    root = hou.node("/")
    if root is None:
        raise RuntimeError("Could not access the Houdini root network.")
    return list(root.allSubChildren(top_down=True, recurse_in_locked_nodes=True))


def find_hython():
    """Find the hython executable belonging to this Houdini installation."""
    candidates = []
    hfs = hou.getenv("HFS") or os.environ.get("HFS")
    if hfs:
        bin_dir = os.path.join(hfs, "bin")
        candidates.extend([
            os.path.join(bin_dir, "hython.exe"),
            os.path.join(bin_dir, "hython"),
        ])
    which = shutil.which("hython") or shutil.which("hython.exe")
    if which:
        candidates.append(which)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise RuntimeError(
        "Could not find Houdini's hython executable. Make sure HFS points to "
        "the Houdini 22 installation and that HFS/bin/hython exists."
    )


SCANNER_SCRIPT = r'''
import os
import sys
import json
import traceback
import hou

source_path = os.path.abspath(sys.argv[1])
result_path = os.path.abspath(sys.argv[2])

try:
    hou.hipFile.load(
        source_path,
        suppress_save_prompt=True,
        ignore_load_warnings=True
    )

    root = hou.node("/")
    contexts = []

    for network in root.children():
        try:
            children = list(network.children())
        except Exception:
            continue

        if not children:
            continue

        entries = []
        for child in children:
            try:
                entries.append({
                    "name": child.name(),
                    "path": child.path(),
                    "type": child.type().name(),
                })
            except Exception:
                pass

        if entries:
            contexts.append({
                "name": network.name(),
                "path": network.path(),
                "type": network.type().name(),
                "nodes": entries,
            })

    payload = {"source": source_path, "contexts": contexts}

except Exception:
    payload = {"source": source_path, "error": traceback.format_exc()}

with open(result_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2)
'''


def scan_hip_file(source_path, hython_path):
    """Inspect a HIP in a separate hython process, leaving the live scene alone."""
    source_path = os.path.abspath(os.path.expandvars(os.path.expanduser(source_path)))
    if is_current_hip_file(source_path):
        raise ValueError(
            "The currently open HIP file is excluded from scanning. "
            "Choose a different source HIP."
        )
    if not os.path.isfile(source_path):
        raise FileNotFoundError("Source HIP file not found:\n" + source_path)
    if os.path.splitext(source_path)[1].lower() not in HIP_EXTENSIONS:
        raise ValueError("Unsupported Houdini file type:\n" + source_path)

    with tempfile.TemporaryDirectory(prefix="houdini_hip_scan_") as temp_dir:
        scanner_path = os.path.join(temp_dir, "scan_hip_inventory.py")
        result_path = os.path.join(temp_dir, "inventory.json")

        with open(scanner_path, "w", encoding="utf-8") as handle:
            handle.write(SCANNER_SCRIPT)

        command = [hython_path, scanner_path, source_path, result_path]
        run_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "timeout": SCAN_TIMEOUT_SECONDS,
            "env": os.environ.copy(),
        }
        # Avoid opening a console window when scanning from Houdini on Windows.
        if os.name == "nt":
            run_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        completed = subprocess.run(command, **run_kwargs)

        if not os.path.isfile(result_path):
            details = (completed.stderr or completed.stdout or "No scanner output.").strip()
            raise RuntimeError(
                "Houdini could not inspect this HIP file.\n\n" + details[-5000:]
            )

        with open(result_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        if payload.get("error"):
            raise RuntimeError(payload["error"])

        return payload


def merge_selected_context(source_path, context_path):
    """Merge one selected context and wrap its newly imported direct children."""
    source_path = os.path.abspath(os.path.expandvars(os.path.expanduser(source_path)))
    if is_current_hip_file(source_path):
        raise ValueError("The currently open HIP file cannot be merged into itself.")
    if not os.path.isfile(source_path):
        raise FileNotFoundError("Source HIP file no longer exists:\n" + source_path)

    # Houdini's HIP merge accepts a node pattern. A context wildcard limits this
    # merge pass to direct nodes in the chosen manager network. If the current
    # scene does not yet have that manager network, merge the manager itself so
    # Houdini can create the context before its contents are grouped.
    before_network = hou.node(context_path)
    node_pattern = (
        context_path.rstrip("/") + "/*"
        if before_network is not None else context_path
    )
    before_paths = set()
    if before_network is not None:
        before_paths = {child.path() for child in before_network.children()}

    hou.hipFile.merge(
        source_path,
        node_pattern=node_pattern,
        overwrite_on_conflict=False,
        ignore_load_warnings=True
    )

    target_network = hou.node(context_path)
    if target_network is None:
        raise RuntimeError(
            "Merge pattern '{}' did not create or find target context '{}'.".format(
                node_pattern, context_path
            )
        )

    imported_roots = [
        child for child in target_network.children()
        if child.path() not in before_paths
    ]

    if not imported_roots:
        return {
            "context": context_path,
            "pattern": node_pattern,
            "imported": 0,
            "subnet": None,
            "warning": (
                "No new direct nodes were detected for {}. Check whether this "
                "HIP's node pattern matched the requested context."
            ).format(context_path),
        }

    source_stem = clean_node_name(source_path)
    subnet_name = unique_child_name(target_network, "merged_" + source_stem)

    try:
        subnet = target_network.collapseIntoSubnet(
            tuple(imported_roots),
            subnet_name=subnet_name
        )
        full_source = os.path.normpath(source_path)
        subnet.setComment(
            "Merged HIP content\n"
            "Source HIP: {}\n"
            "Original network: {}\n"
            "Imported top-level nodes: {}".format(
                full_source, context_path, len(imported_roots)
            )
        )
        subnet.setUserData("source_hip_file", full_source)
        subnet.setUserData("source_hip_filename", os.path.basename(source_path))
        subnet.setUserData("source_network", context_path)
        subnet.setUserData("merge_prefix", "merged_")
        subnet.setUserData("hip_object_merge_organiser", "3")

        try:
            subnet.setColor(hou.Color((0.20, 0.39, 0.48)))
        except Exception:
            pass
        try:
            subnet.layoutChildren()
        except Exception:
            pass

        return {
            "context": context_path,
            "pattern": node_pattern,
            "imported": len(imported_roots),
            "subnet": subnet.path(),
            "warning": None,
        }

    except Exception as exc:
        # Never destroy imported content if the network type cannot be wrapped.
        return {
            "context": context_path,
            "pattern": node_pattern,
            "imported": len(imported_roots),
            "subnet": None,
            "warning": "Imported nodes remain in {} because subnet grouping failed: {}".format(
                context_path, exc
            ),
        }


def merge_one_hip(source_path, selected_contexts):
    if not selected_contexts:
        return {"source": source_path, "results": [], "warning": "No contexts selected."}

    results = []
    for context_path in selected_contexts:
        results.append(merge_selected_context(source_path, context_path))
    return {"source": source_path, "results": results}


class LonghouseShapeJoinAnimation(QtWidgets.QWidget):
    """Animated Longhouse character joining two pieces into one new shape."""

    TIMER_INTERVAL_MS = 40
    FRAME_DT = TIMER_INTERVAL_MS / 1000.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(250, 116)
        self.setMouseTracking(True)
        self.setToolTip(
            "Watch the Longhouse character push two shapes together "
            "to create one new shape."
        )

        self.phase = 0.0
        self.mode_elapsed = 0.0
        self.mode = "approach"
        self.character_bob = 0.0
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(self.TIMER_INTERVAL_MS)
        self.timer.timeout.connect(self.advance_animation)
        self.timer.start()

    @staticmethod
    def smoothstep(value):
        value = max(0.0, min(1.0, value))
        return value * value * (3.0 - 2.0 * value)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.timer.isActive():
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def advance_animation(self):
        dt = self.FRAME_DT
        self.phase += dt
        self.mode_elapsed += dt
        self.character_bob = math.sin(self.phase * 3.0) * 1.2

        durations = {
            "approach": 0.85,
            "push": 1.15,
            "merge": 0.65,
            "celebrate": 1.15,
            "reset": 0.75,
        }
        if self.mode_elapsed >= durations[self.mode]:
            sequence = ["approach", "push", "merge", "celebrate", "reset"]
            self.mode = sequence[(sequence.index(self.mode) + 1) % len(sequence)]
            self.mode_elapsed = 0.0
        self.update()

    def phase_progress(self):
        durations = {
            "approach": 0.85,
            "push": 1.15,
            "merge": 0.65,
            "celebrate": 1.15,
            "reset": 0.75,
        }
        return self.smoothstep(self.mode_elapsed / durations[self.mode])

    def shape_positions(self):
        """Return the two pieces as they move together above the character."""
        p = self.phase_progress()
        left_start, right_start = 111.0, 190.0
        joined = 150.0

        if self.mode == "approach":
            # The character reaches up and takes hold of the two pieces.
            return left_start, right_start, 25.0, 25.0, 0.0
        if self.mode == "push":
            return (
                left_start + (joined - 14.0 - left_start) * p,
                right_start + (joined + 14.0 - right_start) * p,
                25.0 - 3.0 * p,
                25.0 - 3.0 * p,
                0.0,
            )
        if self.mode == "merge":
            q = self.smoothstep(self.mode_elapsed / 0.65)
            return joined - 14.0 + 14.0 * q, joined + 14.0 - 14.0 * q, 22.0, 22.0, q
        if self.mode == "celebrate":
            pulse = 1.0 + math.sin(self.mode_elapsed * 7.0) * 0.035
            return joined, joined, 25.0 * pulse, 25.0 * pulse, 1.0

        # Split the new shape back into its two source pieces for the next loop.
        return (
            joined + (left_start - joined) * p,
            joined + (right_start - joined) * p,
            25.0,
            25.0,
            1.0 - p,
        )

    def draw_source_shape(self, painter, cx, cy, size, side):
        painter.save()
        painter.translate(cx, cy)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if side == "left":
            fill = QtGui.QColor("#63B3ED")
            edge = QtGui.QColor("#2C6893")
            path = QtGui.QPainterPath()
            path.moveTo(-size, -size * 0.72)
            path.quadTo(-size, -size, -size * 0.45, -size)
            path.lineTo(size * 0.42, -size)
            path.lineTo(size * 0.42, -size * 0.28)
            path.lineTo(size, 0)
            path.lineTo(size * 0.42, size * 0.28)
            path.lineTo(size * 0.42, size)
            path.lineTo(-size * 0.45, size)
            path.quadTo(-size, size, -size, size * 0.72)
            path.closeSubpath()
        else:
            fill = QtGui.QColor("#E6A23C")
            edge = QtGui.QColor("#9A6423")
            path = QtGui.QPainterPath()
            path.moveTo(-size * 0.42, -size)
            path.lineTo(size * 0.45, -size)
            path.quadTo(size, -size, size, -size * 0.72)
            path.lineTo(size, size * 0.72)
            path.quadTo(size, size, size * 0.45, size)
            path.lineTo(-size * 0.42, size)
            path.lineTo(-size * 0.42, size * 0.28)
            path.lineTo(-size, 0)
            path.lineTo(-size * 0.42, -size * 0.28)
            path.closeSubpath()
        painter.setPen(QtGui.QPen(edge, 1.5))
        painter.setBrush(fill)
        painter.drawPath(path)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(255, 255, 255, 100))
        painter.drawEllipse(QtCore.QRectF(-size * 0.48, -size * 0.56, size * 0.42, size * 0.25))
        painter.restore()

    def draw_combined_shape(self, painter, cx, cy, size, glow=True):
        painter.save()
        painter.translate(cx, cy)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        if glow:
            for expand, alpha in ((9, 22), (5, 35)):
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(QtGui.QColor(255, 190, 80, alpha))
                painter.drawEllipse(QtCore.QRectF(
                    -size - expand, -size - expand,
                    (size + expand) * 2, (size + expand) * 2
                ))

        path = QtGui.QPainterPath()
        path.moveTo(-size * 0.92, -size * 0.35)
        path.cubicTo(-size * 1.05, -size * 1.0, -size * 0.25, -size * 1.12, 0, -size * 0.78)
        path.cubicTo(size * 0.55, -size * 1.18, size * 1.08, -size * 0.55, size * 0.86, 0)
        path.cubicTo(size * 1.12, size * 0.58, size * 0.45, size * 1.08, 0, size * 0.78)
        path.cubicTo(-size * 0.55, size * 1.12, -size * 1.08, size * 0.52, -size * 0.92, -size * 0.35)
        path.closeSubpath()

        gradient = QtGui.QLinearGradient(-size, -size, size, size)
        gradient.setColorAt(0.0, QtGui.QColor("#63B3ED"))
        gradient.setColorAt(0.48, QtGui.QColor("#A6B7C4"))
        gradient.setColorAt(1.0, QtGui.QColor("#E6A23C"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#805D45"), 1.5))
        painter.setBrush(gradient)
        painter.drawPath(path)

        # A small central join mark makes the two becoming one readable.
        if not glow:
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 150), 1.2))
            painter.drawLine(QtCore.QPointF(0, -size * 0.35), QtCore.QPointF(0, size * 0.35))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(255, 255, 255, 145))
        painter.drawEllipse(QtCore.QRectF(-size * 0.48, -size * 0.55, size * 0.25, size * 0.17))
        painter.restore()

    def draw_character(self, painter, celebrating=False):
        painter.save()
        painter.translate(0, self.character_bob)
        dark = QtGui.QColor("#39333D")
        orange = QtGui.QColor("#FF6600")
        white = QtGui.QColor("#FFFFFF")

        limb_pen = QtGui.QPen(dark, 3.5)
        limb_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        limb_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(limb_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        # Legs and feet.
        painter.drawLine(QtCore.QPointF(119, 72), QtCore.QPointF(113, 87))
        painter.drawLine(QtCore.QPointF(113, 87), QtCore.QPointF(125, 91))
        painter.drawLine(QtCore.QPointF(151, 72), QtCore.QPointF(159, 86))
        painter.drawLine(QtCore.QPointF(159, 86), QtCore.QPointF(171, 89))
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(orange)
        painter.drawRoundedRect(QtCore.QRectF(119, 88, 14, 7), 3, 3)
        painter.drawRoundedRect(QtCore.QRectF(166, 86, 14, 7), 3, 3)

        # Both arms lift above the mascot, then press inward as the shapes meet.
        p = self.phase_progress()
        if self.mode in ("approach", "push", "merge"):
            arm_l = (105 + 12 * p, 17 + 7 * p)
            arm_r = (181 - 12 * p, 17 + 7 * p)
        elif celebrating:
            arm_l = (104, 8 + math.sin(self.phase * 8) * 2)
            arm_r = (182, 8 - math.sin(self.phase * 8) * 2)
        else:
            arm_l = (108, 25)
            arm_r = (178, 25)

        left_arm = QtGui.QPainterPath()
        left_arm.moveTo(119, 48)
        left_arm.cubicTo(108, 42, arm_l[0] - 5, arm_l[1] + 12, arm_l[0], arm_l[1])
        painter.drawPath(left_arm)
        right_arm = QtGui.QPainterPath()
        right_arm.moveTo(151, 48)
        right_arm.cubicTo(163, 42, arm_r[0] + 5, arm_r[1] + 12, arm_r[0], arm_r[1])
        painter.drawPath(right_arm)

        # Orange Longhouse body and white spiral mark.
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(orange)
        painter.drawRoundedRect(QtCore.QRectF(112, 35, 47, 40), 3, 3)
        spiral = QtGui.QPainterPath()
        spiral.moveTo(119, 45)
        spiral.cubicTo(127, 38, 141, 39, 150, 46)
        spiral.cubicTo(157, 53, 153, 64, 145, 68)
        spiral.cubicTo(136, 73, 126, 67, 123, 62)
        spiral.cubicTo(119, 57, 124, 51, 130, 51)
        spiral.cubicTo(137, 50, 142, 55, 139, 59)
        spiral.cubicTo(137, 62, 132, 60, 133, 57)
        spiral_pen = QtGui.QPen(white, 3.5)
        spiral_pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        spiral_pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(spiral_pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawPath(spiral)

        # Hands.
        painter.setPen(QtGui.QPen(dark, 1.0))
        painter.setBrush(white)
        painter.drawEllipse(QtCore.QRectF(arm_l[0] - 4, arm_l[1] - 4, 8, 8))
        painter.drawEllipse(QtCore.QRectF(arm_r[0] - 4, arm_r[1] - 4, 8, 8))
        painter.restore()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        # Ground and soft shadow.
        painter.setPen(QtGui.QPen(QtGui.QColor("#777C84"), 1.0))
        painter.drawLine(QtCore.QPointF(7, 101), QtCore.QPointF(243, 101))
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0, 28))
        painter.drawEllipse(QtCore.QRectF(105, 98, 82, 5))

        left_x, right_x, left_size, right_size, merged = self.shape_positions()
        shape_y = 22.0

        if merged >= 0.98:
            self.draw_combined_shape(
                painter, 150, shape_y, 22.0,
                glow=(self.mode == "celebrate")
            )
        else:
            if merged > 0.0:
                painter.save()
                painter.setOpacity(max(0.0, 1.0 - merged))
            self.draw_source_shape(painter, left_x, shape_y, left_size, "left")
            self.draw_source_shape(painter, right_x, shape_y, right_size, "right")
            if merged > 0.0:
                painter.restore()
            if merged > 0.55:
                painter.save()
                painter.setOpacity((merged - 0.55) / 0.45)
                self.draw_combined_shape(painter, 150, shape_y, 22.0, glow=True)
                painter.restore()

        self.draw_character(painter, celebrating=(self.mode == "celebrate"))
        painter.end()

class HipObjectMergeDialog(QtWidgets.QDialog):

    ROLE_KIND = QtCore.Qt.ItemDataRole.UserRole
    ROLE_PATH = QtCore.Qt.ItemDataRole.UserRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("HIP Asset Merge Organiser")
        self.setMinimumSize(820, 620)
        self.resize(960, 760)
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.build_ui()

    def build_ui(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)

        heading = QtWidgets.QLabel("Merge Selected Contents from HIP Files")
        heading.setStyleSheet("font-size: 19px; font-weight: bold;")
        header.addWidget(heading, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)

        # Animated character pushes two pieces together to form one new shape.
        self.builder_character = LonghouseShapeJoinAnimation(self)
        header.addWidget(
            self.builder_character,
            0,
            QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        main.addLayout(header)

        intro = QtWidgets.QLabel(
            "Add HIP files and scan their contents. Each source is listed by network "
            "context, with the nodes found inside it. Tick only the contexts you want "
            "to bring into the current scene. Selected contexts are grouped into "
            "merged_<hipname> subnets."
        )
        intro.setWordWrap(True)
        main.addWidget(intro)

        source_group = QtWidgets.QGroupBox("1. Source HIP Files")
        style_section(source_group)
        source_layout = QtWidgets.QVBoxLayout(source_group)

        buttons = QtWidgets.QHBoxLayout()
        self.add_button = QtWidgets.QPushButton("Add HIP Files...")
        self.remove_button = QtWidgets.QPushButton("Remove Selected HIP")
        self.scan_button = QtWidgets.QPushButton("Scan / Refresh Contents")
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.scan_button)
        buttons.addStretch(1)
        source_layout.addLayout(buttons)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Merge", "Source / Context / Node", "Count / Type"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.header().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        source_layout.addWidget(self.tree, 1)

        tree_controls = QtWidgets.QHBoxLayout()
        self.select_all_button = QtWidgets.QPushButton("Select All Contexts")
        self.select_none_button = QtWidgets.QPushButton("Select No Contexts")
        self.status_label = QtWidgets.QLabel("No source files added.")
        self.status_label.setStyleSheet("color: #B8BEC7; font-size: 11px;")
        tree_controls.addWidget(self.select_all_button)
        tree_controls.addWidget(self.select_none_button)
        tree_controls.addStretch(1)
        tree_controls.addWidget(self.status_label)
        source_layout.addLayout(tree_controls)
        main.addWidget(source_group, 1)

        note = QtWidgets.QLabel(
            "Only checked contexts are merged. Dependencies stored in unchecked "
            "contexts are not added automatically. Existing nodes are never "
            "overwritten; Houdini renames name conflicts. The currently open HIP "
            "cannot be scanned or merged into itself, and is never saved automatically."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #C4C9D1;")
        main.addWidget(note)

        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)
        self.close_button = QtWidgets.QPushButton("Close")
        self.merge_button = QtWidgets.QPushButton("Merge Checked Contexts")
        self.merge_button.setMinimumHeight(36)
        self.merge_button.setMinimumWidth(190)
        self.merge_button.setEnabled(False)
        self.merge_button.setStyleSheet("font-weight: bold;")
        bottom.addWidget(self.close_button)
        bottom.addWidget(self.merge_button)
        main.addLayout(bottom)

        self.add_button.clicked.connect(self.add_files)
        self.remove_button.clicked.connect(self.remove_selected_source)
        self.scan_button.clicked.connect(self.scan_sources)
        self.select_all_button.clicked.connect(lambda: self.set_all_context_checks(True))
        self.select_none_button.clicked.connect(lambda: self.set_all_context_checks(False))
        self.close_button.clicked.connect(self.close)
        self.merge_button.clicked.connect(self.merge_checked)
        self.tree.itemChanged.connect(self.update_status)

    def current_start_folder(self):
        current_hip = hou.hipFile.path()
        if current_hip:
            folder = os.path.dirname(os.path.abspath(current_hip))
            if os.path.isdir(folder):
                return folder
        return os.path.expanduser("~")

    def source_items(self):
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def source_path(self, item):
        return item.data(0, self.ROLE_PATH)

    def add_files(self):
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self,
            "Select HIP Files to Inspect and Merge",
            self.current_start_folder(),
            "Houdini Projects (*.hip *.hiplc *.hipnc);;All Files (*.*)"
        )
        if not paths:
            return

        known = {
            canonical_hip_path(self.source_path(item))
            for item in self.source_items()
            if self.source_path(item)
        }

        added_items = []
        skipped_current = []

        for path in paths:
            path = os.path.abspath(
                os.path.expandvars(os.path.expanduser(path))
            )
            if os.path.splitext(path)[1].lower() not in HIP_EXTENSIONS:
                continue

            # Never let the live scene's own HIP reach the scanner process.
            if is_current_hip_file(path):
                skipped_current.append(path)
                continue

            key = canonical_hip_path(path)
            if key in known:
                continue

            source_item = QtWidgets.QTreeWidgetItem([
                os.path.basename(path), path, "Scanning..."
            ])
            source_item.setData(0, self.ROLE_KIND, "source")
            source_item.setData(0, self.ROLE_PATH, path)
            source_item.setToolTip(0, path)
            source_item.setToolTip(1, path)
            source_item.setExpanded(True)
            self.tree.addTopLevelItem(source_item)
            QtWidgets.QTreeWidgetItem(
                source_item, ["", "Scanning HIP contents...", ""]
            )
            added_items.append(source_item)
            known.add(key)

        self.update_status()

        if skipped_current:
            QtWidgets.QMessageBox.information(
                self,
                "Current HIP Excluded",
                "The HIP file currently open in Houdini was not added or scanned. "
                "A scene cannot be used as its own merge source."
            )

        # Scan only newly-added HIP files immediately. The manual
        # Scan / Refresh button remains available for rescanning everything.
        if added_items:
            self.scan_sources(added_items)

    def mark_current_scene_item(self, source_item):
        """Remove stale scan results for an item that is now the live HIP."""
        source_item.takeChildren()
        source_item.setText(2, "CURRENT SCENE — excluded")
        warning_item = QtWidgets.QTreeWidgetItem([
            "", "This is the HIP currently open in Houdini; it is not scanned or merged.", ""
        ])
        source_item.addChild(warning_item)
        source_item.setExpanded(True)

    def selected_source_item(self):
        selected = self.tree.selectedItems()
        if not selected:
            return None
        item = selected[0]
        while item.parent() is not None:
            item = item.parent()
        return item if item.data(0, self.ROLE_KIND) == "source" else None

    def remove_selected_source(self):
        item = self.selected_source_item()
        if item is None:
            return
        index = self.tree.indexOfTopLevelItem(item)
        self.tree.takeTopLevelItem(index)
        self.update_status()

    def set_all_context_checks(self, checked):
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        self.tree.blockSignals(True)
        try:
            for source in self.source_items():
                for i in range(source.childCount()):
                    context_item = source.child(i)
                    if context_item.data(0, self.ROLE_KIND) == "context":
                        context_item.setCheckState(0, state)
        finally:
            self.tree.blockSignals(False)
        self.update_status()

    def scan_sources(self, items=None):
        # When called from Add HIP Files, scan only newly-added sources.
        # When called from the Scan / Refresh button, scan every listed source.
        items = list(items) if items is not None else self.source_items()
        if not items:
            return

        safe_items = []
        newly_blocked = []
        for source_item in items:
            path = self.source_path(source_item)
            if is_current_hip_file(path):
                self.mark_current_scene_item(source_item)
                newly_blocked.append(path)
            else:
                safe_items.append(source_item)
        items = safe_items

        if newly_blocked:
            QtWidgets.QMessageBox.information(
                self,
                "Current HIP Excluded",
                "The currently open HIP file was skipped. It cannot be scanned "
                "or merged into itself."
            )

        if not items:
            self.update_status()
            return

        try:
            hython = find_hython()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Hython Not Found", str(exc))
            return

        self.scan_button.setEnabled(False)
        self.merge_button.setEnabled(False)
        progress = QtWidgets.QProgressDialog(
            "Inspecting HIP files in a separate Houdini process...",
            "Cancel",
            0,
            len(items),
            self
        )
        progress.setWindowTitle("Scanning HIP Contents")
        progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        errors = []
        for index, source_item in enumerate(items):
            if progress.wasCanceled():
                break
            path = self.source_path(source_item)
            progress.setLabelText("Scanning {}".format(os.path.basename(path)))
            QtWidgets.QApplication.processEvents()

            try:
                inventory = scan_hip_file(path, hython)
                source_item.takeChildren()

                contexts = inventory.get("contexts", [])
                for context in contexts:
                    nodes = context.get("nodes", [])
                    context_item = QtWidgets.QTreeWidgetItem([
                        "", context.get("path", context.get("name", "Context")),
                        "{} nodes".format(len(nodes))
                    ])
                    context_item.setData(0, self.ROLE_KIND, "context")
                    context_item.setData(0, self.ROLE_PATH, context.get("path", ""))
                    context_item.setFlags(
                        context_item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                    )
                    context_item.setCheckState(0, QtCore.Qt.CheckState.Checked)
                    context_item.setToolTip(1, "{} ({})".format(
                        context.get("path", ""), context.get("type", "")
                    ))
                    source_item.addChild(context_item)

                    for node in nodes:
                        node_item = QtWidgets.QTreeWidgetItem([
                            "", node.get("path", node.get("name", "Node")),
                            node.get("type", "")
                        ])
                        node_item.setData(0, self.ROLE_KIND, "node")
                        node_item.setToolTip(1, node.get("path", ""))
                        context_item.addChild(node_item)
                    context_item.setExpanded(True)

                source_item.setText(2, "{} contexts".format(len(contexts)))
                source_item.setToolTip(1, path)
                source_item.setExpanded(True)

                if not contexts:
                    QtWidgets.QTreeWidgetItem(source_item, ["", "No populated top-level contexts found", ""])

            except subprocess.TimeoutExpired:
                errors.append("{}: scan timed out after {} seconds".format(
                    path, SCAN_TIMEOUT_SECONDS
                ))
                source_item.takeChildren()
                QtWidgets.QTreeWidgetItem(source_item, ["", "SCAN TIMED OUT", ""])
            except Exception as exc:
                errors.append("{}: {}".format(path, exc))
                source_item.takeChildren()
                QtWidgets.QTreeWidgetItem(source_item, ["", "SCAN FAILED — see results", ""])

            progress.setValue(index + 1)
            QtWidgets.QApplication.processEvents()

        progress.close()
        self.scan_button.setEnabled(True)
        self.update_status()

        if errors:
            QtWidgets.QMessageBox.warning(
                self,
                "Some HIP Files Could Not Be Scanned",
                "\n\n".join(errors)
            )

    def selected_contexts_by_source(self):
        selection = []
        for source_item in self.source_items():
            path = self.source_path(source_item)
            contexts = []
            for i in range(source_item.childCount()):
                child = source_item.child(i)
                if child.data(0, self.ROLE_KIND) != "context":
                    continue
                if child.checkState(0) == QtCore.Qt.CheckState.Checked:
                    context_path = child.data(0, self.ROLE_PATH)
                    if context_path:
                        contexts.append(context_path)
            if contexts:
                selection.append((path, contexts))
        return selection

    def update_status(self, *args):
        scanned = 0
        enabled_contexts = 0
        for source_item in self.source_items():
            found = False
            for i in range(source_item.childCount()):
                child = source_item.child(i)
                if child.data(0, self.ROLE_KIND) == "context":
                    found = True
                    if child.checkState(0) == QtCore.Qt.CheckState.Checked:
                        enabled_contexts += 1
            if found:
                scanned += 1

        self.merge_button.setEnabled(enabled_contexts > 0)
        self.status_label.setText(
            "{} HIP(s) scanned | {} context(s) checked".format(scanned, enabled_contexts)
        )

    def merge_checked(self):
        selection = self.selected_contexts_by_source()
        if not selection:
            return

        filtered = []
        skipped = []
        for path, contexts in selection:
            if not os.path.isfile(path):
                skipped.append("Missing file: {}".format(path))
                continue
            if is_current_hip_file(path):
                skipped.append("Skipped currently open HIP: {}".format(path))
                continue
            filtered.append((path, contexts))

        if not filtered:
            QtWidgets.QMessageBox.warning(
                self, "Nothing to Merge", "No valid source HIP files remain.\n\n" + "\n".join(skipped)
            )
            return

        summary = []
        for path, contexts in filtered:
            summary.append("{}\n  {}".format(os.path.basename(path), ", ".join(contexts)))

        # Use explicit button text instead of QMessageBox.StandardButton.Yes/No.
        # Some Houdini/Qt platform styles expose the mnemonic ampersands as
        # literal text ("&Yes" / "&No") on standard message-box buttons.
        confirm_box = QtWidgets.QMessageBox(self)
        confirm_box.setWindowTitle("Confirm Selective HIP Merge")
        confirm_box.setIcon(QtWidgets.QMessageBox.Icon.Question)
        confirm_box.setText(
            "Merge only these checked contexts?\n\n{}\n\n"
            "Unchecked contexts will not be merged. Dependencies in unchecked "
            "contexts are not brought in automatically. Existing nodes will not "
            "be overwritten. The current HIP will not be saved automatically.".format(
                "\n\n".join(summary)
            )
        )
        yes_button = confirm_box.addButton(
            "Yes", QtWidgets.QMessageBox.ButtonRole.AcceptRole
        )
        no_button = confirm_box.addButton(
            "No", QtWidgets.QMessageBox.ButtonRole.RejectRole
        )
        confirm_box.setDefaultButton(no_button)
        confirm_box.setEscapeButton(no_button)
        confirm_box.exec()

        if confirm_box.clickedButton() is not yes_button:
            return

        self.merge_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.add_button.setEnabled(False)
        QtWidgets.QApplication.setOverrideCursor(
            QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor)
        )

        results = []
        failures = list(skipped)
        try:
            for path, contexts in filtered:
                try:
                    results.append(merge_one_hip(path, contexts))
                except Exception as exc:
                    failures.append(
                        "{}: {}\n{}".format(
                            os.path.basename(path), exc, traceback.format_exc()
                        )
                    )
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self.scan_button.setEnabled(True)
            self.add_button.setEnabled(True)
            self.update_status()

        lines = []
        for result in results:
            lines.append(os.path.basename(result["source"]))
            for item in result["results"]:
                if item["subnet"]:
                    lines.append(
                        "  {} → {} ({} nodes)".format(
                            item["context"], item["subnet"], item["imported"]
                        )
                    )
                else:
                    lines.append(
                        "  {}: {} node(s) imported".format(
                            item["context"], item["imported"]
                        )
                    )
                if item.get("warning"):
                    lines.append("    WARNING: " + item["warning"])

        if failures:
            lines.append("\nSkipped / failed:")
            lines.extend(failures)

        message = "\n".join(lines) if lines else "No changes were made."
        if failures or any(
            item.get("warning")
            for result in results for item in result.get("results", [])
        ):
            QtWidgets.QMessageBox.warning(self, "HIP Merge Results", message)
        else:
            QtWidgets.QMessageBox.information(self, "HIP Merge Complete", message)


def show_hip_object_merge_tool():
    old_dialog = getattr(hou.session, "_hip_object_merge_organiser_v3", None)
    if old_dialog is not None:
        try:
            old_dialog.close()
            old_dialog.deleteLater()
        except Exception:
            pass

    dialog = HipObjectMergeDialog(hou.qt.mainWindow())
    hou.session._hip_object_merge_organiser_v3 = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()


if __name__ == "__main__":
    show_hip_object_merge_tool()
