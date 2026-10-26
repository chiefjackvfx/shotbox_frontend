"""Bake a complete EXR precomp from immutable source and generated alpha.

Runs on Nuke's main thread after inference. No current upstream graph is read,
and the previous published version remains untouched if publication fails.
"""
from pathlib import Path
import math

from . import cache
from .capture import configure_read, configure_write, preserve_host


def _bbox(node):
    box = node.bbox()
    return [int(box.x()), int(box.y()), int(box.x() + box.w()), int(box.y() + box.h())]


def publish(attempt_path, manifest, output_pattern):
    """Return a complete precomp descriptor only after every EXR decodes."""
    import nuke
    root = Path(attempt_path).resolve()
    requested = Path(output_pattern)
    relative = requested.relative_to(root).as_posix() if requested.is_absolute() else requested.as_posix()
    pattern = cache.resolve_owned(root, relative)
    if relative.count("%04d") != 1 or "%" in relative.replace("%04d", "") or not relative.endswith(".exr"):
        raise ValueError("Precomp output must be one owned %04d EXR sequence.")
    snapshot = manifest["capture"]
    start, end, count = snapshot["start"], snapshot["end"], snapshot["count"]
    source_directory = cache.resolve_owned(root, "source_v001")
    expected_sources = {"source.%04d.exr" % i for i in range(1, count + 1)}
    if not source_directory.is_dir() or {p.name for p in source_directory.iterdir()} != expected_sources:
        raise RuntimeError("Immutable source snapshot is missing or incomplete; Generate again.")
    for record in snapshot.get("source_files", []):
        path = cache.resolve_owned(root, record["path"])
        if path.stat().st_size != record["size"] or cache.sha256_file(path) != record["sha256"]:
            raise RuntimeError("Immutable source snapshot changed; Generate again.")
    outputs = manifest["outputs"]
    if len(outputs) != count:
        raise RuntimeError("Generated matte frame count does not match the source snapshot.")
    matte_paths = [cache.resolve_owned(root, record["path"]) for record in outputs]
    first_matte = matte_paths[0]
    matte_prefix = first_matte.name.rsplit(".", 2)[0]
    matte_pattern = first_matte.with_name(matte_prefix + ".%04d.exr")
    if matte_paths != [Path(str(matte_pattern) % frame) for frame in range(1, count + 1)]:
        raise RuntimeError("Generated matte is not the expected contiguous sequence.")
    destinations = [Path(str(pattern) % frame) for frame in range(start, end + 1)]
    if any(path.exists() for path in destinations):
        raise RuntimeError("Precomp version already contains frames; allocate a new version.")
    pattern.parent.mkdir(parents=True, exist_ok=True)
    owned = []
    geometries = []
    channels = set()
    with preserve_host(), nuke.root():
        try:
            source = nuke.nodes.Read(name="ShotBoxPrecompSource")
            owned.append(source)
            configure_read(source, source_directory / "source.%04d.exr", snapshot)
            matte = nuke.nodes.Read(name="ShotBoxPrecompAlpha")
            owned.append(matte)
            configure_read(matte, matte_pattern, snapshot)
            copy = nuke.nodes.Copy(name="ShotBoxPrecompCopy", inputs=[source, matte])
            owned.append(copy)
            copy["from0"].setValue("rgba.red")
            copy["to0"].setValue("rgba.alpha")
            for key in ("bbox", "metainput", "rangeinput"):
                copy[key].setValue("B")
            for frame in range(start, end + 1):
                nuke.frame(frame)
                fmt = source.format()
                frame_channels = sorted(set(source.channels()) | {"rgba.alpha"})
                channels.update(frame_channels)
                geometries.append({"geometry": {"width": int(fmt.width()), "height": int(fmt.height())},
                                   "box": _bbox(source), "pixel_aspect": float(fmt.pixelAspect()),
                                   "channels": frame_channels})
            write = nuke.nodes.Write(name="ShotBoxPrecompWrite", inputs=[copy])
            owned.append(write)
            write["file"].setValue(str(pattern).replace("\\", "/"))
            write["file_type"].setValue("exr")
            configure_write(write, channels="all")
            nuke.execute(write, start, end, 1, views=[snapshot["view"]], continueOnError=False)
            if any(not path.is_file() or path.stat().st_size == 0 for path in destinations):
                raise RuntimeError("Precomp render is missing completed EXR frames.")
            check = nuke.nodes.Read(name="ShotBoxPrecompValidate")
            owned.append(check)
            check["file"].setValue(str(pattern).replace("\\", "/"))
            check["first"].setValue(start)
            check["last"].setValue(end)
            check["raw"].setValue(True)
            check["on_error"].setValue("error")
            check["localizationPolicy"].setValue("off")
            for frame, details in zip(range(start, end + 1), geometries):
                nuke.frame(frame)
                actual = {"width": check.width(), "height": check.height()}
                if actual != details["geometry"] or _bbox(check) != details["box"]:
                    raise RuntimeError("Rendered precomp changed the captured format or data window.")
                if check.format().pixelAspect() != details["pixel_aspect"] or set(check.channels()) != set(details["channels"]):
                    raise RuntimeError("Rendered precomp changed captured channels or pixel aspect.")
                left, bottom, right, top = details["box"]
                width, height = right - left, top - bottom
                # Depth and other auxiliary channels may legitimately contain
                # infinity. Decode them without imposing RGBA's finite contract.
                for channel in details["channels"]:
                    average = check.sample(channel, (left + right) / 2, (bottom + top) / 2,
                                           max(1, width), max(1, height))
                    if channel.startswith("rgba.") and not math.isfinite(average):
                        raise RuntimeError("Nuke decoded non-finite data in the rendered precomp.")
        finally:
            for temporary in reversed(owned):
                nuke.delete(temporary)
    return {"pattern": relative, "first": start, "last": end,
            "files": [cache.file_record(path, root, **details) for path, details in zip(destinations, geometries)],
            "channels": sorted(channels), "precision": "float", "state": "complete"}
