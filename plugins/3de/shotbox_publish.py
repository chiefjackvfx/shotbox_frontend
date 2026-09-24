# 3DE4.script.name:    Publish
# 3DE4.script.version: v1.1
# 3DE4.script.gui:     Main Window::ShotBox
# 3DE4.script.comment: Batch publish to Nuke, Lens, Houdini, Maya, Blender, and Alembic silently.

import os
import re
import sys
import glob
import tempfile
import tde4

# ─── Config ──────────────────────────────────────────────────────────────────

# Resolve at publish time through 3DE, never through the inherited working
# directory or the filename supplied by 3DE's script execution namespace.
SCRIPTS_DIR = None
_SYS_DATA = None
ALEMBIC_LIB = None


def resolve_dependency_paths():
    global SCRIPTS_DIR, _SYS_DATA, ALEMBIC_LIB
    SCRIPTS_DIR = _SYS_DATA = ALEMBIC_LIB = None
    reported = None
    expected = "<3DE installation>/sys_data/py_scripts"
    try:
        reported = tde4.get3DEInstallPath()
        if not isinstance(reported, str) or not reported.strip():
            raise ValueError("3DE did not report an installation path")
        install = os.path.normpath(reported)
        sys_data = os.path.join(install, "sys_data")
        expected = os.path.join(sys_data, "py_scripts")
        if not os.path.isabs(install) or (
            os.name == "nt" and not os.path.splitdrive(install)[0]
        ):
            raise ValueError("3DE installation path must be absolute")
        if not os.path.isdir(expected):
            raise ValueError("3DE scripts directory does not exist")
    except Exception as exc:
        raise RuntimeError(
            "Could not locate 3DE supporting scripts.\n"
            "Reported installation path: {!r}\n"
            "Expected scripts directory: {}\n{}".format(reported, expected, exc)
        ) from exc
    SCRIPTS_DIR = expected
    _SYS_DATA = sys_data
    ALEMBIC_LIB = _find_alembic_lib()


def _find_alembic_lib():
    """Locate 3DE's bundled site-packages across Linux/Windows and Python versions.

    Linux:   <sys_data>/pyXYZ_inst/lib/pythonX.Y/site-packages
    Windows: <sys_data>/pyXYZ_inst/Lib/site-packages
    """
    for py_inst in sorted(glob.glob(os.path.join(_SYS_DATA, "py*_inst"))):
        # Windows CPython layout
        win = os.path.join(py_inst, "Lib", "site-packages")
        if os.path.isdir(win):
            return win
        # Linux / macOS CPython layout
        for sp in glob.glob(os.path.join(py_inst, "lib", "python*", "site-packages")):
            if os.path.isdir(sp):
                return sp
    return None


VERSION_RE  = re.compile(r'(_v\d+)', re.IGNORECASE)


def alembic_deps_available():
    """Check whether the Python modules required by export_alembic.py are present.
    3DE bundles imath.so / alembic.so under its own py311_inst, but that path
    isn't always on sys.path at runtime — so we add it on demand."""
    try:
        import imath    # noqa: F401
        import alembic  # noqa: F401
        return True, None
    except ImportError:
        if ALEMBIC_LIB and os.path.isdir(ALEMBIC_LIB) and ALEMBIC_LIB not in sys.path:
            sys.path.insert(0, ALEMBIC_LIB)
            try:
                import imath    # noqa: F401
                import alembic  # noqa: F401
                return True, None
            except ImportError as e:
                return False, str(e)
        return False, "imath / alembic not importable from 3DE's Python"

# ─── Path derivation ─────────────────────────────────────────────────────────

def derive_paths(current_path):
    """
    Given a project path like:
        /job/shot/3de/eatwell_550_matchmove_v002.3de
    Returns (export_dir, paths_dict, shot_base) where export_dir is
        /job/shot/export/
    and paths_dict maps export type to absolute output path.
    """
    filename    = os.path.basename(current_path)
    name, _     = os.path.splitext(filename)
    project_dir = os.path.dirname(current_path)
    export_dir  = os.path.join(os.path.dirname(project_dir), "export")

    m = VERSION_RE.search(name)
    if not m:
        return None, None, None

    # "eatwell_550_matchmove" -> rsplit on last underscore -> "eatwell_550"
    shot_base    = name[:m.start()].rsplit("_", 1)[0]
    version_part = m.group(1)

    paths = {
        "nuke_camera": os.path.join(export_dir, "{}_camera{}.nk".format(shot_base, version_part)),
        "nuke_lens":   os.path.join(export_dir, "{}_lens{}.nk".format(shot_base, version_part)),
        "houdini":     os.path.join(export_dir, "{}_camera{}_hou.py".format(shot_base, version_part)),
        "maya":        os.path.join(export_dir, "{}_camera{}_maya.py".format(shot_base, version_part)),
        "blender":     os.path.join(export_dir, "{}_camera{}_blender.py".format(shot_base, version_part)),
        "alembic":     os.path.join(export_dir, "{}_camera{}.abc".format(shot_base, version_part)),
    }
    return export_dir, paths, shot_base


# ─── Silent script runner ────────────────────────────────────────────────────

def silent_exec(script_name, widget_overrides, entry_point=None):
    """
    Execute a built-in 3DE export script headlessly by monkey-patching
    tde4 dialog functions so no UI appears.

    widget_overrides: {widget_name: value} injected into tde4.getWidgetValue.
    entry_point:      optional function name to call after exec (for scripts
                      gated on __name__ == '__main__').

    Returns (success: bool, error: str|None).
    """
    script_path = os.path.join(SCRIPTS_DIR, script_name)
    if not os.path.exists(script_path):
        return False, "Script not found: {}".format(script_path)

    # Stash every tde4 UI function we need to suppress
    orig = {
        "postCustomRequester":              tde4.postCustomRequester,
        "getWidgetValue":                   tde4.getWidgetValue,
        "postQuestionRequester":            tde4.postQuestionRequester,
        "updateProgressRequester":          tde4.updateProgressRequester,
        "unpostProgressRequester":          tde4.unpostProgressRequester,
        "postProgressRequesterAndContinue": tde4.postProgressRequesterAndContinue,
    }

    # Auto-click OK on any dialog
    tde4.postCustomRequester             = lambda req, title, w, h, *btns: 1
    # Return our values for known widgets; fall back to the real call for others
    tde4.getWidgetValue                  = lambda req, wid: (
        widget_overrides[wid] if wid in widget_overrides
        else orig["getWidgetValue"](req, wid)
    )
    tde4.postQuestionRequester           = lambda *a: None
    tde4.updateProgressRequester         = lambda *a: None
    tde4.unpostProgressRequester         = lambda *a: None
    tde4.postProgressRequesterAndContinue = lambda *a: None

    err = None
    try:
        with open(script_path, "r") as f:
            source = f.read()
        ns = {"tde4": tde4}
        exec(compile(source, script_path, "exec"), ns)
        # Scripts guarded by if __name__ == '__main__' need an explicit call
        if entry_point and entry_point in ns:
            ns[entry_point]()
    except Exception as e:
        err = str(e)
    finally:
        # Always restore, even if something explodes
        for k, v in orig.items():
            setattr(tde4, k, v)

    return err is None, err


# ─── Individual exporters ────────────────────────────────────────────────────

def export_nuke_camera(path, start_frame):
    return silent_exec("export_nuke.py", {
        "file_browser":     path,
        "startframe_field": str(start_frame),
    })


def export_nuke_lens(path, start_frame):
    # The LD script is gated on __name__, so we exec then call the entry point.
    # The FOV mode 2 = "relative to Display Window" (recommended).
    return silent_exec("export_nuke_LD_3DE4_Lens_Distortion_Node.py", {
        "file_nuke_path":                    path,
        "text_initial_frame_nuke":           str(start_frame),
        "option_menu_fov_mode":              2,
        "option_menu_default_initial_frame": 2,
    }, entry_point="main_export_nuke_ld_3de4")


def _export_file_state(path):
    try:
        stat = os.stat(path)
        return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
    except FileNotFoundError:
        return None


def record_houdini_export(path):
    log = os.path.abspath(os.path.expandvars(os.path.expanduser(
        os.environ.get("SHOTBOX_HOUDINI_HISTORY") or "~/.export_houdini.log"
    )))
    paths = [os.path.abspath(path)]
    try:
        with open(log, encoding="utf-8") as stream:
            for line in stream:
                entry = line.strip().strip('"').strip("'")
                if entry and os.path.normcase(entry) not in {os.path.normcase(p) for p in paths}:
                    paths.append(entry)
                if len(paths) >= 10:
                    break
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(log), exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=os.path.dirname(log), delete=False) as stream:
            temporary = stream.name
            stream.write("\n".join(paths) + "\n")
        os.replace(temporary, log)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def export_houdini(path, start_frame):
    before = _export_file_state(path)
    ok, error = silent_exec("export_houdini.py", {
        "file_browser":     path,
        "startframe_field": str(start_frame),
    })
    if not ok:
        return ok, error
    after = _export_file_state(path)
    if not after or not after[0] or before == after:
        return False, "No new nonempty Houdini export was written; history not updated"
    try:
        record_houdini_export(path)
    except (OSError, UnicodeError) as exc:
        return True, "Export succeeded, but history could not be updated: {}".format(exc)
    return True, None


def export_maya(path, start_frame, shot_base):
    # Maya's main() is gated on __name__, so we exec then call it.
    # model_selection=1 means "No 3D Models" — clean camera-only publish.
    # camera_selection=1 means "Current Camera Only".
    return silent_exec("export_maya.py", {
        "file_browser":                   path,
        "startframe_field":               str(start_frame),
        "export_mode":                    1,        # Export Whole Project
        "scene_name":                     shot_base,
        "camera_selection":               1,        # Current Camera Only
        "hide_ref_frames":                1,
        "model_selection":                1,        # No 3D Models
        "export_texture":                 1,
        "export_overscan_width_percent":  "100.0",
        "export_overscan_height_percent": "100.0",
        "units":                          1,        # cm
        "point_sets":                     1,
        "export_2p5d":                    0,
        "tempfile":                       1,        # OS temp dir
    }, entry_point="main")


def export_blender(path, start_frame):
    """Export a Blender camera script using whichever Blender exporter ships with 3DE.

    Newer 3DE releases use exportBlender.py; older installs may still ship
    export_blender.py.  Supplying aliases for the common output/start-frame
    widget names is harmless because silent_exec only intercepts widgets the
    exporter actually asks for.
    """
    candidates = (
        ("exportBlender.py", "main"),
        ("export_blender.py", None),
    )

    for script_name, entry_point in candidates:
        if os.path.exists(os.path.join(SCRIPTS_DIR, script_name)):
            return silent_exec(script_name, {
                "file_browser":     path,
                "file":             path,
                "file_blender_path": path,
                "startframe_field": str(start_frame),
                "start_frame":      str(start_frame),
                "camera_selection": 1,
            }, entry_point=entry_point)

    return False, "Blender exporter not found (expected {} or {})".format(
        *(os.path.join(SCRIPTS_DIR, name) for name, _ in candidates)
    )


def export_alembic(path, start_frame):
    return silent_exec("export_alembic.py", {
        "file":              path,
        "current_cam_only":  0,    # export all cameras
        "no_ref_cameras":    0,
        "overscan":          "100.0 100.0",
        "start_frame":       str(start_frame),
        "export_3dmodels":   1,
        "export_distortion": 1,
        "export_tracking":   1,
    })


# ─── Publish runner ──────────────────────────────────────────────────────────

def run_publish(req, paths, start_frame, shot_base):
    """
    Read toggle states from the dialog, run each selected exporter,
    and return a human-readable results summary.
    """
    selected = {
        "nuke_camera": tde4.getWidgetValue(req, "tog_nuke_camera") == 1,
        "nuke_lens":   tde4.getWidgetValue(req, "tog_nuke_lens")   == 1,
        "houdini":     tde4.getWidgetValue(req, "tog_houdini")     == 1,
        "maya":        tde4.getWidgetValue(req, "tog_maya")        == 1,
        "blender":     tde4.getWidgetValue(req, "tog_blender")     == 1,
        "alembic":     tde4.getWidgetValue(req, "tog_alembic")     == 1,
    }

    if not any(selected.values()):
        return "Nothing selected — tick at least one export."

    # Ensure export dir exists before any exporter tries to write
    export_dir = os.path.dirname(paths["nuke_camera"])
    if not os.path.exists(export_dir):
        os.makedirs(export_dir)

    exporters = [
        ("nuke_camera", "Nuke Camera ",  lambda: export_nuke_camera(paths["nuke_camera"], start_frame)),
        ("nuke_lens",   "Nuke Lens    ", lambda: export_nuke_lens(paths["nuke_lens"],   start_frame)),
        ("houdini",     "Houdini      ", lambda: export_houdini(paths["houdini"],       start_frame)),
        ("maya",        "Maya         ", lambda: export_maya(paths["maya"],             start_frame, shot_base)),
        ("blender",     "Blender      ", lambda: export_blender(paths["blender"],         start_frame)),
        ("alembic",     "Alembic      ", lambda: export_alembic(paths["alembic"],       start_frame)),
    ]

    results = []
    for key, label, fn in exporters:
        if not selected[key]:
            continue
        ok, err = fn()
        if ok and os.path.exists(paths[key]):
            results.append("{}  OK  ->  {}".format(label, os.path.basename(paths[key])))
            if err:
                results.append("{}  WARN  {}".format(label, err))
        elif ok:
            # Script ran but file wasn't created — likely an internal validation fail
            results.append("{}  WARN  file not found after export".format(label))
        else:
            # Friendlier message for the common alembic-deps case
            if key == "alembic" and err and ("imath" in err or "alembic" in err):
                err = "pyalembic / imath not installed in 3DE's Python environment"
            results.append("{}  FAILED  {}".format(label, err or "unknown error"))

    return "\n".join(results)


# ─── Dialog ──────────────────────────────────────────────────────────────────

def build_dialog(export_dir, paths, abc_ok):
    """Build and return a 3DE custom requester with per-format toggles.

    Layout per row:
        [ Description label, anchored left ]              [Checkbox, anchored right]
    Two widgets per row gives us full control — a single Toggle widget
    clips its label because the size hint constrains label width.
    """
    req = tde4.createCustomRequester()

    # Output directory label — full width
    tde4.addLabelWidget(req, "lbl_dir", "Output:  {}".format(export_dir), "ALIGN_LABEL_LEFT")
    tde4.setWidgetOffsets(req, "lbl_dir", 15, 15, 12, 0)
    tde4.setWidgetAttachModes(req, "lbl_dir", "ATTACH_WINDOW", "ATTACH_WINDOW", "ATTACH_WINDOW", "ATTACH_NONE")
    tde4.setWidgetSize(req, "lbl_dir", 10, 20)

    tde4.addSeparatorWidget(req, "sep_top")
    tde4.setWidgetOffsets(req, "sep_top", 0, 0, 8, 0)
    tde4.setWidgetAttachModes(req, "sep_top", "ATTACH_WINDOW", "ATTACH_WINDOW", "ATTACH_WIDGET", "ATTACH_NONE")
    tde4.setWidgetSize(req, "sep_top", 10, 5)

    abc_label = "Alembic       \u2192   {}".format(os.path.basename(paths["alembic"]))
    if not abc_ok:
        abc_label += "    (unavailable: pyalembic / imath not installed)"

    # Each row: (toggle_id, label_id, descriptive_text, default_on)
    rows = [
        ("tog_nuke_camera", "lbl_nuke_camera", "Nuke Camera   \u2192   {}".format(os.path.basename(paths["nuke_camera"])), 1),
        ("tog_nuke_lens",   "lbl_nuke_lens",   "Nuke Lens     \u2192   {}".format(os.path.basename(paths["nuke_lens"])),   1),
        ("tog_houdini",     "lbl_houdini",     "Houdini       \u2192   {}".format(os.path.basename(paths["houdini"])),     1),
        ("tog_maya",        "lbl_maya",        "Maya          \u2192   {}".format(os.path.basename(paths["maya"])),        1),
        ("tog_blender",     "lbl_blender",     "Blender       \u2192   {}".format(os.path.basename(paths["blender"])),     1),
        ("tog_alembic",     "lbl_alembic",     abc_label, 1 if abc_ok else 0),
    ]

    prev = "sep_top"
    for tog_id, lbl_id, text, default in rows:
        # Checkbox — small, anchored to window's RIGHT edge
        tde4.addToggleWidget(req, tog_id, "", default)
        tde4.setWidgetOffsets(req, tog_id, 0, 25, 10, 0)
        tde4.setWidgetAttachModes(req, tog_id, "ATTACH_NONE", "ATTACH_WINDOW", "ATTACH_WIDGET", "ATTACH_NONE")
        tde4.setWidgetSize(req, tog_id, 20, 20)
        tde4.setWidgetLinks(req, tog_id, "", "", prev, "")

        # Description label — fills from window LEFT to just before the checkbox
        tde4.addLabelWidget(req, lbl_id, text, "ALIGN_LABEL_LEFT")
        tde4.setWidgetOffsets(req, lbl_id, 20, 10, 10, 0)
        tde4.setWidgetAttachModes(req, lbl_id, "ATTACH_WINDOW", "ATTACH_WIDGET", "ATTACH_WIDGET", "ATTACH_NONE")
        tde4.setWidgetSize(req, lbl_id, 10, 20)
        tde4.setWidgetLinks(req, lbl_id, "", tog_id, prev, "")

        prev = tog_id  # next row hangs off this row's checkbox

    # Gray out the Alembic toggle if its dependencies are missing
    if not abc_ok:
        tde4.setWidgetSensitiveFlag(req, "tog_alembic", 0)
        tde4.setWidgetSensitiveFlag(req, "lbl_alembic", 0)

    tde4.addSeparatorWidget(req, "sep_bot")
    tde4.setWidgetOffsets(req, "sep_bot", 0, 0, 8, 0)
    tde4.setWidgetAttachModes(req, "sep_bot", "ATTACH_WINDOW", "ATTACH_WINDOW", "ATTACH_WIDGET", "ATTACH_NONE")
    tde4.setWidgetSize(req, "sep_bot", 10, 5)
    tde4.setWidgetLinks(req, "sep_bot", "", "", prev, "")

    # Root links
    tde4.setWidgetLinks(req, "lbl_dir", "", "", "", "")
    tde4.setWidgetLinks(req, "sep_top", "", "", "lbl_dir", "")

    return req


# ─── Entry point ─────────────────────────────────────────────────────────────

def shotbox_publish():
    try:
        resolve_dependency_paths()
    except RuntimeError as exc:
        tde4.postQuestionRequester("ShotBox Publish", str(exc), "OK")
        return

    current_path = tde4.getProjectPath()
    if not current_path:
        tde4.postQuestionRequester("ShotBox Publish", "No project is currently open.", "OK")
        return

    export_dir, paths, shot_base = derive_paths(current_path)
    if paths is None:
        tde4.postQuestionRequester(
            "ShotBox Publish",
            "Could not find a version number in:\n{}\n\nExpected a pattern like _v001.".format(
                os.path.basename(current_path)
            ),
            "OK"
        )
        return

    cam         = tde4.getCurrentCamera()
    start_frame = tde4.getCameraFrameOffset(cam) if cam else 1001

    abc_ok, _ = alembic_deps_available()

    req = build_dialog(export_dir, paths, abc_ok)
    ret = tde4.postCustomRequester(req, "ShotBox Publish", 720, 0, "Publish", "Close")

    if ret == 1:
        summary = run_publish(req, paths, start_frame, shot_base)
        tde4.postQuestionRequester("ShotBox Publish", summary, "OK")

    tde4.deleteCustomRequester(req)


if __name__ == "__main__":
    shotbox_publish()
