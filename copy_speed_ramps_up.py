from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory
import xml.etree.ElementTree as ET


__all__ = ["run"]


def read_time(value):
    if not value or not value.endswith("s"):
        raise ValueError("Invalid FCPXML time value")
    return Fraction(value[:-1])


def write_time(value):
    ticks_per_second = 1_000_000
    value = Fraction(round(value * ticks_per_second), ticks_per_second)
    return f"{value.numerator}/{value.denominator}s"


def validate_time_map(time_map, expected_source_span):
    points = time_map.findall("timept")
    actual_span = read_time(points[-1].get("value")) - read_time(
        points[0].get("value")
    )
    tolerance = Fraction(1, 10_000)

    if abs(actual_span - expected_source_span) > tolerance:
        raise ValueError("The copied retime does not fit the target source range")

    for point, next_point in zip(points, points[1:]):
        timeline_delta = read_time(next_point.get("time")) - read_time(
            point.get("time")
        )
        source_delta = read_time(next_point.get("value")) - read_time(
            point.get("value")
        )
        if timeline_delta <= 0 or abs(source_delta / timeline_delta) > 20:
            raise ValueError("The copied retime contains an invalid speed segment")

        out_time = point.get("outTime")
        if out_time is not None and not 0 < read_time(out_time) <= timeline_delta:
            raise ValueError("A curve out-handle crosses its next retime point")

        in_time = next_point.get("inTime")
        if in_time is not None and not 0 < read_time(in_time) <= timeline_delta:
            raise ValueError("A curve in-handle crosses its previous retime point")


def clamp_curve_handles(points):
    for index, point in enumerate(points):
        if point.get("interp", "smooth2") == "linear":
            point.attrib.pop("inTime", None)
            point.attrib.pop("outTime", None)
            continue

        point_time = read_time(point.get("time"))

        if point.get("inTime") is not None:
            if index == 0:
                point.attrib.pop("inTime")
            else:
                previous_time = read_time(points[index - 1].get("time"))
                available = point_time - previous_time
                point.set(
                    "inTime",
                    write_time(min(read_time(point.get("inTime")), available)),
                )

        if point.get("outTime") is not None:
            if index == len(points) - 1:
                point.attrib.pop("outTime")
            else:
                next_time = read_time(points[index + 1].get("time"))
                available = next_time - point_time
                point.set(
                    "outTime",
                    write_time(min(read_time(point.get("outTime")), available)),
                )


def mapped_point_at(points, time):
    for point in points:
        if read_time(point.get("time")) == time:
            return read_time(point.get("value")), deepcopy(point)

    for point, next_point in zip(points, points[1:]):
        point_time = read_time(point.get("time"))
        next_time = read_time(next_point.get("time"))

        if point_time < time < next_time:
            point_value = read_time(point.get("value"))
            next_value = read_time(next_point.get("value"))
            position = (time - point_time) / (next_time - point_time)
            value = point_value + (next_value - point_value) * position

            # A trim inside a smooth segment is not itself a curve control
            # point. Preserve the interpolation but do not copy handles that
            # belong to either neighbouring point.
            return value, ET.Element(
                "timept",
                {
                    "time": write_time(time),
                    "value": write_time(value),
                    "interp": point.get("interp", "linear"),
                },
            )

    raise ValueError("The visible clip start is outside its retime map")


def copy_time_map(source_clip, target_clip, assets_by_id):
    source_map = source_clip.find("timeMap")
    source_points = source_map.findall("timept")
    if len(source_points) < 2:
        raise ValueError("The source retime map has fewer than two points")

    # Treat each clip's visible trimmed-in frame as local frame zero.
    # The clips' source timecodes are never compared with each other.
    source_visible_in = read_time(source_clip.get("start"))
    source_visible_out = source_visible_in + read_time(source_clip.get("duration"))
    target_visible_in = read_time(target_clip.get("start"))
    source_value_at_in, start_point = mapped_point_at(
        source_points, source_visible_in
    )
    source_value_at_out, end_point = mapped_point_at(
        source_points, source_visible_out
    )
    source_span = source_value_at_out - source_value_at_in
    if source_span <= 0:
        raise ValueError("The visible retime must have a positive source span")

    target_span = read_time(target_clip.get("duration"))
    target_asset = assets_by_id[target_clip.get("ref")]
    target_media_start = read_time(target_asset.get("start"))

    visible_points = [start_point]
    visible_points.extend(
        deepcopy(point)
        for point in source_points
        if source_visible_in < read_time(point.get("time")) < source_visible_out
    )
    visible_points.append(end_point)

    target_map = ET.Element("timeMap", source_map.attrib)
    if target_media_start < target_visible_in:
        target_map.append(
            ET.Element(
                "timept",
                {
                    "time": write_time(target_media_start),
                    "value": write_time(target_media_start),
                    "interp": "linear",
                },
            )
        )

    for point in visible_points:
        local_timeline_offset = read_time(point.get("time")) - source_visible_in
        local_source_offset = read_time(point.get("value")) - source_value_at_in

        # Scale the complete value axis by one constant factor. Rewriting only
        # the final point makes smooth2 curves discontinuous and can crash
        # Resolve while it imports the resulting FCPXML.
        target_source_offset = local_source_offset * target_span / source_span
        point.set(
            "time",
            write_time(target_visible_in + local_timeline_offset),
        )
        point.set(
            "value",
            write_time(target_visible_in + target_source_offset),
        )
        target_map.append(point)

    clamp_curve_handles(target_map.findall("timept"))
    validate_time_map(
        target_map,
        target_span + target_visible_in - target_media_start,
    )

    old_map = target_clip.find("timeMap")
    if old_map is not None:
        target_clip.remove(old_map)

    target_clip.insert(0, target_map)
    target_clip.set("duration", source_clip.get("duration"))


def copy_ramps_to_clips_above(root):
    changed = []
    assets_by_id = {
        asset.get("id"): asset for asset in root.findall("./resources/asset")
    }
    retimed_clips = [
        clip
        for clip in root.iter()
        if clip.tag in ("clip", "asset-clip") and clip.find("timeMap") is not None
    ]

    for source_clip in retimed_clips:
        source_start = read_time(source_clip.get("start"))

        for target_clip in source_clip.findall("asset-clip"):
            lane = int(target_clip.get("lane", "0"))
            target_offset = read_time(target_clip.get("offset"))

            if lane == 1 and target_offset == source_start:
                copy_time_map(source_clip, target_clip, assets_by_id)
                changed.append((source_clip.get("name"), target_clip.get("name")))

    return changed


def get_unique_timeline_name(project, base_name):
    names = {
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)
    }
    if base_name not in names:
        return base_name

    number = 2
    while f"{base_name} {number}" in names:
        number += 1
    return f"{base_name} {number}"


def get_media_pool_folders(folder):
    folders = [folder]
    for child in folder.GetSubFolderList():
        folders.extend(get_media_pool_folders(child))
    return folders


def copy_clip_colors(source_timeline, target_timeline):
    failures = 0
    track_count = min(
        source_timeline.GetTrackCount("video"),
        target_timeline.GetTrackCount("video"),
    )

    for track in range(1, track_count + 1):
        source_clips = source_timeline.GetItemListInTrack("video", track)
        target_clips = target_timeline.GetItemListInTrack("video", track)

        for source_clip, target_clip in zip(source_clips, target_clips):
            color = source_clip.GetClipColor()
            if color and not target_clip.SetClipColor(color):
                failures += 1

    return failures


def action_result(
    success,
    message,
    timeline_name=None,
    copied_pairs=None,
    warnings=None,
):
    return {
        "success": success,
        "message": message,
        "timeline_name": timeline_name,
        "copied_pairs": [
            {"source": source, "target": target}
            for source, target in (copied_pairs or [])
        ],
        "warnings": list(warnings or []),
    }


def _run(resolve):
    if not resolve:
        return action_result(False, "Could not connect to Resolve.")

    try:
        project_manager = resolve.GetProjectManager()
        project = project_manager.GetCurrentProject() if project_manager else None
        timeline = project.GetCurrentTimeline() if project else None
    except Exception as error:
        return action_result(False, f"Could not access the current timeline: {error}")

    if not timeline:
        return action_result(False, "No timeline is currently open in Resolve.")

    try:
        media_pool = project.GetMediaPool()
        if not media_pool:
            return action_result(False, "Could not access the project's Media Pool.")
        new_name = get_unique_timeline_name(
            project, f"{timeline.GetName()} - matched speed ramps"
        )
    except Exception as error:
        return action_result(False, f"Could not prepare the new timeline: {error}")

    with TemporaryDirectory(prefix="resolve_copy_ramps_") as temp_dir:
        xml_path = Path(temp_dir) / "timeline.fcpxml"
        try:
            exported = timeline.Export(
                str(xml_path), resolve.EXPORT_FCPXML_1_9
            )
        except Exception as error:
            return action_result(False, f"Timeline export failed: {error}")
        if not exported:
            return action_result(False, "Timeline export failed.")

        try:
            tree = ET.parse(xml_path)
            changed = copy_ramps_to_clips_above(tree.getroot())
        except (ET.ParseError, KeyError, TypeError, ValueError) as error:
            return action_result(
                False, f"Could not build a safe retime map: {error}"
            )
        except Exception as error:
            return action_result(
                False, f"Unexpected error while reading the timeline: {error}"
            )

        if not changed:
            return action_result(
                False,
                "No aligned clip directly above a retimed clip was found.",
            )

        try:
            xml_data = ET.tostring(
                tree.getroot(), encoding="UTF-8", xml_declaration=True
            )
            xml_data = xml_data.replace(
                b"?>", b"?>\n<!DOCTYPE fcpxml>", 1
            )
            xml_path.write_bytes(xml_data)
            new_timeline = media_pool.ImportTimelineFromFile(
                str(xml_path),
                {
                    "timelineName": new_name,
                    "importSourceClips": False,
                    "sourceClipsFolders": get_media_pool_folders(
                        media_pool.GetRootFolder()
                    ),
                },
            )
        except Exception as error:
            return action_result(False, f"Modified timeline import failed: {error}")

    if not new_timeline:
        return action_result(False, "Modified timeline import failed.")

    warnings = []
    try:
        result_name = new_timeline.GetName() or new_name
    except Exception as error:
        result_name = new_name
        warnings.append(f"Could not read the imported timeline name: {error}")

    try:
        color_failures = copy_clip_colors(timeline, new_timeline)
        if color_failures:
            warnings.append(
                f"Could not restore the colour of {color_failures} clip(s)."
            )
    except Exception as error:
        warnings.append(f"Could not restore clip colours: {error}")

    try:
        if not project.SetCurrentTimeline(new_timeline):
            warnings.append("Could not open the new timeline automatically.")
    except Exception as error:
        warnings.append(f"Could not open the new timeline automatically: {error}")

    try:
        if not project_manager.SaveProject():
            warnings.append("Resolve did not confirm that the project was saved.")
    except Exception as error:
        warnings.append(f"Could not save the project: {error}")

    pair_count = len(changed)
    pair_word = "pair" if pair_count == 1 else "pairs"
    return action_result(
        True,
        f"Created timeline '{result_name}' and copied {pair_count} speed-ramp {pair_word}.",
        timeline_name=result_name,
        copied_pairs=changed,
        warnings=warnings,
    )


def run(resolve):
    """Copy speed ramps to aligned clips above on the current timeline."""
    try:
        return _run(resolve)
    except Exception as error:
        return action_result(False, f"Copy Speed Ramps Up failed: {error}")


if __name__ == "__main__":
    try:
        import DaVinciResolveScript as dvr_script

        standalone_result = run(dvr_script.scriptapp("Resolve"))
        print(standalone_result["message"])
        for warning in standalone_result["warnings"]:
            print(f"Warning: {warning}")
    except ImportError as error:
        print(f"Could not connect to Resolve: {error}")
