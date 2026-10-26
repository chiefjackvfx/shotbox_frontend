"""Foreground, fixed-format capture of the connected evaluated input."""
from contextlib import contextmanager
from pathlib import Path
import platform
import sys


def host_identity():
    import nuke
    from PySide6 import __version__ as pyside_version
    return {"executable": nuke.env.get("ExecutablePath", sys.executable),
            "nuke": nuke.NUKE_VERSION_STRING, "python": sys.version,
            "pyside": pyside_version, "os": platform.platform(),
            "edition": "NukeX" if nuke.env.get("nukex") else "Nuke"}


@contextmanager
def preserve_host():
    import nuke
    selected = nuke.selectedNodes()
    frame = nuke.frame()
    proxy = nuke.root()["proxy"].value()
    undo_disabled = nuke.Undo.disabled()
    if not undo_disabled:
        nuke.Undo.disable()
    try:
        nuke.root()["proxy"].setValue(False)
        yield
    finally:
        nuke.root()["proxy"].setValue(proxy)
        nuke.frame(frame)
        for node in nuke.selectedNodes():
            node["selected"].setValue(False)
        for node in selected:
            try:
                node["selected"].setValue(True)
            except (ValueError, RuntimeError):
                pass
        if not undo_disabled:
            nuke.Undo.enable()


def input_geometry(node, start, end):
    import nuke
    source = node.input(0)
    if source is None:
        raise ValueError("Connect one flat RGB input before Generate.")
    if len(nuke.views()) != 1:
        raise ValueError("ShotBox requires a single-view project. Convert to a single view first.")
    frame = nuke.frame()
    geometry = None
    try:
        for current in range(start, end + 1):
            nuke.frame(current)
            fmt = source.format()
            sample = {"width": int(fmt.width()), "height": int(fmt.height()),
                      "pixel_aspect": float(fmt.pixelAspect()),
                      "box": [0, 0, int(fmt.width()), int(fmt.height())]}
            if sample["pixel_aspect"] != 1.0:
                raise ValueError("Non-square pixels are unsupported; convert upstream to pixel aspect 1.0.")
            if geometry is not None and sample != geometry:
                raise ValueError("Input format changes across the range; reformat to one fixed raster upstream.")
            if source.Class().startswith("Deep") or any(c.startswith("deep.") for c in source.channels()):
                raise ValueError("Deep input is unsupported; convert to flat RGB upstream.")
            if not {"rgba.red", "rgba.green", "rgba.blue"}.issubset(source.channels()):
                raise ValueError("Input must contain flat rgba.red, rgba.green and rgba.blue channels.")
            geometry = sample
    finally:
        nuke.frame(frame)
    return geometry


def capture(node, attempt, snapshot):
    import nuke
    from . import render
    directory = Path(attempt) / "capture_v001"
    directory.mkdir(exist_ok=True)
    if any(directory.iterdir()):
        raise RuntimeError("Capture directory is not empty; allocate a new attempt.")
    source_directory = Path(attempt) / "source_v001"
    retain_source = snapshot.get("mode") != "alpha"
    if retain_source:
        source_directory.mkdir(exist_ok=True)
        if any(source_directory.iterdir()):
            raise RuntimeError("Source snapshot directory is not empty; allocate a new attempt.")
    parent_name = node.fullName().rpartition(".")[0]
    parent = nuke.toNode(parent_name) if parent_name else nuke.root()
    owned = []
    with preserve_host(), parent:
        try:
            source_read = node.input(0)
            if retain_source:
                source_write = nuke.nodes.Write(name="ShotBoxSourceWrite", inputs=[node.input(0)])
                owned.append(source_write)
                source_write["file"].setValue(str(source_directory / "source.%04d.exr").replace("\\", "/"))
                source_write["file_type"].setValue("exr")
                configure_write(source_write, channels="all", offset=1 - snapshot["start"])
                token = render.register_capture_write(source_write, node)
                try:
                    nuke.execute(source_write, snapshot["start"], snapshot["end"], 1,
                                 views=[snapshot["view"]], continueOnError=False)
                finally:
                    render.unregister_capture_write(token)
                source_read = nuke.nodes.Read(name="ShotBoxSourceSnapshot")
                owned.append(source_read)
                configure_read(source_read, source_directory / "source.%04d.exr", snapshot)
            crop = nuke.nodes.Crop(name="ShotBoxCaptureCrop", inputs=[source_read])
            owned.append(crop)
            for key, value in (("box", snapshot["box"]), ("intersect", False),
                               ("reformat", False), ("crop", True), ("softness", 0)):
                crop[key].setValue(value)
            write = nuke.nodes.Write(name="ShotBoxCaptureWrite", inputs=[crop])
            owned.append(write)
            write["file"].setValue(str(directory / "capture.%04d.exr").replace("\\", "/"))
            write["file_type"].setValue("exr")
            configure_write(write, channels="rgb", offset=1 - snapshot["start"])
            # Only this exact owned Write is exempt; upstream matte caches still validate.
            token = render.register_capture_write(write, node)
            try:
                nuke.execute(write, snapshot["start"], snapshot["end"], 1,
                             views=[snapshot["view"]], continueOnError=False)
            finally:
                render.unregister_capture_write(token)
        finally:
            for temporary in reversed(owned):
                nuke.delete(temporary)
    expected = {"capture.%04d.exr" % index for index in range(1, snapshot["count"] + 1)}
    if {p.name for p in directory.iterdir()} != expected:
        raise RuntimeError("Capture is incomplete; inference was not started. Generate again.")
    expected_source = {"source.%04d.exr" % index for index in range(1, snapshot["count"] + 1)}
    if retain_source and {p.name for p in source_directory.iterdir()} != expected_source:
        raise RuntimeError("Source snapshot is incomplete; inference was not started. Generate again.")
    return directory


def configure_write(write, *, channels, offset=None):
    """Lossless scene-linear EXR settings shared by capture and publication."""
    for key, value in (("channels", channels), ("datatype", "32 bit float"),
                       ("compression", "Zip (1 scanline)"), ("raw", True),
                       ("autocrop", False), ("proxy", "")):
        write[key].setValue(value)
    if offset is not None:
        write["frame_mode"].setValue("offset")
        write["frame"].setValue(str(offset))
    if "aces_compliant_exr" in write.knobs():
        write["aces_compliant_exr"].setValue(False)
    if "interleave" in write.knobs():
        write["interleave"].setValue("channels, layers and views")


def configure_read(read, pattern, snapshot):
    """Map sequential immutable source frames back onto the captured timeline."""
    read["file"].setValue(str(pattern).replace("\\", "/"))
    read["first"].setValue(1)
    read["last"].setValue(snapshot["count"])
    read["raw"].setValue(True)
    read["on_error"].setValue("error")
    read["localizationPolicy"].setValue("off")
    read["frame_mode"].setValue("expression")
    read["frame"].setValue("frame - (%d) + 1" % snapshot["start"])
