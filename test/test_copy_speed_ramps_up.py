from copy import deepcopy
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

from copy_speed_ramps_up import (
    copy_ramps_to_clips_above,
    read_time,
    run,
)


def timeline_xml(
    source_points,
    source_duration="10s",
    target_duration="10s",
    target_lane="1",
    target_offset="0s",
):
    points = "\n".join(
        f'<timept {attributes}/>' for attributes in source_points
    )
    return ET.fromstring(
        f"""
        <fcpxml>
          <resources>
            <asset id="source" start="0s"/>
            <asset id="target" start="0s"/>
          </resources>
          <library>
            <asset-clip name="source clip" ref="source" start="0s"
                        offset="0s" duration="{source_duration}">
              <timeMap>{points}</timeMap>
              <asset-clip name="target clip" ref="target" start="0s"
                          offset="{target_offset}" duration="{target_duration}"
                          lane="{target_lane}"/>
            </asset-clip>
          </library>
        </fcpxml>
        """
    )


class CopySpeedRampsTests(unittest.TestCase):
    def test_smooth_curve_controls_are_preserved(self):
        root = timeline_xml(
            [
                'time="0s" value="0s" interp="smooth2"',
                (
                    'time="4s" value="2s" interp="smooth2" '
                    'inTime="1s" outTime="1s"'
                ),
                'time="10s" value="8s" interp="smooth2"',
            ]
        )

        self.assertEqual(
            copy_ramps_to_clips_above(root),
            [("source clip", "target clip")],
        )
        target = next(
            clip
            for clip in root.iter("asset-clip")
            if clip.get("name") == "target clip"
        )
        points = target.find("timeMap").findall("timept")

        self.assertEqual(points[1].get("interp"), "smooth2")
        self.assertEqual(read_time(points[1].get("inTime")), 1)
        self.assertEqual(read_time(points[1].get("outTime")), 1)
        self.assertEqual(read_time(points[-1].get("value")), 10)

    def test_curve_values_are_scaled_consistently(self):
        root = timeline_xml(
            [
                'time="0s" value="0s" interp="smooth2"',
                (
                    'time="4s" value="2s" interp="smooth2" '
                    'inTime="1s" outTime="1s"'
                ),
                'time="10s" value="8s" interp="smooth2"',
            ]
        )

        copy_ramps_to_clips_above(root)
        target = next(
            clip
            for clip in root.iter("asset-clip")
            if clip.get("name") == "target clip"
        )
        values = [
            read_time(point.get("value"))
            for point in target.find("timeMap").findall("timept")
        ]

        # The old implementation produced [0, 2, 10], changing just the last
        # segment. A single 10/8 scale keeps the curve coherent.
        self.assertEqual(values, [0, 5 / 2, 10])

    def test_exact_endpoint_curve_attributes_are_not_discarded(self):
        root = timeline_xml(
            [
                (
                    'time="0s" value="0s" interp="smooth2" '
                    'outTime="1s"'
                ),
                (
                    'time="10s" value="10s" interp="smooth2" '
                    'inTime="1s"'
                ),
            ]
        )

        copy_ramps_to_clips_above(root)
        target = next(
            clip
            for clip in root.iter("asset-clip")
            if clip.get("name") == "target clip"
        )
        points = target.find("timeMap").findall("timept")

        self.assertEqual(read_time(points[0].get("outTime")), 1)
        self.assertEqual(read_time(points[-1].get("inTime")), 1)

    def test_invalid_one_point_map_is_rejected_before_import(self):
        root = timeline_xml(
            ['time="0s" value="0s" interp="smooth2"']
        )

        with self.assertRaisesRegex(ValueError, "fewer than two points"):
            copy_ramps_to_clips_above(root)

    def test_speed_above_2000_percent_is_rejected(self):
        root = timeline_xml(
            [
                'time="0s" value="0s" interp="linear"',
                'time="1s" value="21s" interp="linear"',
            ],
            source_duration="1s",
            target_duration="21s",
        )

        with self.assertRaisesRegex(ValueError, "invalid speed segment"):
            copy_ramps_to_clips_above(root)

    def test_curve_handle_is_clamped_to_a_trimmed_endpoint(self):
        root = timeline_xml(
            [
                'time="0s" value="0s" interp="smooth2"',
                (
                    'time="4s" value="4s" interp="smooth2" '
                    'outTime="2s"'
                ),
                'time="6s" value="6s" interp="smooth2"',
            ],
            source_duration="5s",
            target_duration="5s",
        )

        copy_ramps_to_clips_above(root)
        target = next(
            clip
            for clip in root.iter("asset-clip")
            if clip.get("name") == "target clip"
        )
        points = target.find("timeMap").findall("timept")

        self.assertEqual(read_time(points[-2].get("outTime")), 1)
        self.assertEqual(points[-1].get("interp"), "smooth2")

    def test_only_aligned_lane_one_clips_are_changed(self):
        points = [
            'time="0s" value="0s" interp="linear"',
            'time="10s" value="10s" interp="linear"',
        ]

        for lane, offset in (("2", "0s"), ("1", "1s")):
            root = timeline_xml(
                points, target_lane=lane, target_offset=offset
            )

            self.assertEqual(copy_ramps_to_clips_above(root), [])
            target = next(
                clip
                for clip in root.iter("asset-clip")
                if clip.get("name") == "target clip"
            )
            self.assertIsNone(target.find("timeMap"))

    def test_multiple_aligned_pairs_are_changed(self):
        root = timeline_xml(
            [
                'time="0s" value="0s" interp="linear"',
                'time="10s" value="10s" interp="linear"',
            ]
        )
        source = next(root.iter("asset-clip"))
        second_source = deepcopy(source)
        second_source.set("name", "source clip 2")
        second_source.find("asset-clip").set("name", "target clip 2")
        root.find("./library").append(second_source)

        self.assertEqual(
            copy_ramps_to_clips_above(root),
            [
                ("source clip", "target clip"),
                ("source clip 2", "target clip 2"),
            ],
        )

    def test_real_fixture_keeps_source_and_retimes_target(self):
        fixture = Path(__file__).with_name("speed_ramps_original.fcpxml")
        root = ET.parse(str(fixture)).getroot()
        source = next(
            clip
            for clip in root.iter("asset-clip")
            if clip.find("timeMap") is not None
        )
        source_map_before = ET.tostring(source.find("timeMap"))
        target = source.find("asset-clip")

        changed = copy_ramps_to_clips_above(root)

        self.assertEqual(
            changed,
            [
                (
                    "A_0002C006_260715_174128_a1D5X.mxf",
                    "cirio110_v017_[1001-1402].exr",
                )
            ],
        )
        self.assertEqual(ET.tostring(source.find("timeMap")), source_map_before)
        self.assertEqual(target.get("duration"), source.get("duration"))
        self.assertEqual(
            read_time(target.find("timeMap").findall("timept")[-1].get("value")),
            1802,
        )


class FakeTimelineClip:
    def __init__(self, color="", accepts_color=True):
        self.color = color
        self.accepts_color = accepts_color
        self.applied_color = None

    def GetClipColor(self):
        return self.color

    def SetClipColor(self, color):
        self.applied_color = color
        return self.accepts_color


class FakeTimeline:
    def __init__(self, name, xml_text=None, export_result=True, clips=None):
        self.name = name
        self.xml_text = xml_text
        self.export_result = export_result
        self.clips = clips or []
        self.export_type = None

    def GetName(self):
        return self.name

    def Export(self, path, export_type):
        self.export_type = export_type
        if not self.export_result:
            return False
        Path(path).write_text(self.xml_text, encoding="utf-8")
        return True

    def GetTrackCount(self, track_type):
        return 1 if track_type == "video" else 0

    def GetItemListInTrack(self, track_type, track):
        return self.clips


class FakeFolder:
    def __init__(self, children=None):
        self.children = children or []

    def GetSubFolderList(self):
        return self.children


class FakeMediaPool:
    def __init__(self, imported_timeline=True, target_accepts_color=True):
        self.imported_timeline = imported_timeline
        self.target_accepts_color = target_accepts_color
        self.root_folder = FakeFolder([FakeFolder()])
        self.import_path = None
        self.import_options = None
        self.imported_xml = None

    def GetRootFolder(self):
        return self.root_folder

    def ImportTimelineFromFile(self, path, options):
        self.import_path = path
        self.import_options = options
        self.imported_xml = Path(path).read_text(encoding="utf-8")
        if not self.imported_timeline:
            return None
        if self.imported_timeline is True:
            self.imported_timeline = FakeTimeline(
                options["timelineName"],
                clips=[
                    FakeTimelineClip(accepts_color=self.target_accepts_color)
                ],
            )
        return self.imported_timeline


class FakeProject:
    def __init__(
        self,
        timeline,
        media_pool,
        timeline_names=None,
        activate_result=True,
    ):
        self.timeline = timeline
        self.media_pool = media_pool
        self.timelines = [
            FakeTimeline(name) for name in (timeline_names or [timeline.name])
        ]
        self.activate_result = activate_result
        self.activated_timeline = None

    def GetCurrentTimeline(self):
        return self.timeline

    def GetMediaPool(self):
        return self.media_pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def SetCurrentTimeline(self, timeline):
        self.activated_timeline = timeline
        return self.activate_result


class FakeProjectManager:
    def __init__(self, project, save_result=True):
        self.project = project
        self.save_result = save_result
        self.saved = False

    def GetCurrentProject(self):
        return self.project

    def SaveProject(self):
        self.saved = True
        return self.save_result


class FakeResolve:
    EXPORT_FCPXML_1_9 = "FCPXML_1_9"

    def __init__(self, project_manager):
        self.project_manager = project_manager

    def GetProjectManager(self):
        return self.project_manager


def make_resolve(
    xml_root,
    export_result=True,
    import_result=True,
    activate_result=True,
    save_result=True,
    timeline_names=None,
    target_accepts_color=True,
):
    xml_text = ET.tostring(xml_root, encoding="unicode")
    source_clip = FakeTimelineClip("Blue")
    timeline = FakeTimeline(
        "Original",
        xml_text=xml_text,
        export_result=export_result,
        clips=[source_clip],
    )
    media_pool = FakeMediaPool(
        import_result, target_accepts_color=target_accepts_color
    )
    project = FakeProject(
        timeline,
        media_pool,
        timeline_names=timeline_names,
        activate_result=activate_result,
    )
    project_manager = FakeProjectManager(project, save_result=save_result)
    return FakeResolve(project_manager), project, project_manager, media_pool


class RunTests(unittest.TestCase):
    def setUp(self):
        self.valid_root = timeline_xml(
            [
                'time="0s" value="0s" interp="linear"',
                'time="10s" value="10s" interp="linear"',
            ]
        )

    def assert_result_shape(self, result):
        self.assertEqual(
            set(result),
            {"success", "message", "timeline_name", "copied_pairs", "warnings"},
        )

    def test_missing_resolve_returns_structured_failure(self):
        result = run(None)

        self.assert_result_shape(result)
        self.assertFalse(result["success"])
        self.assertIsNone(result["timeline_name"])

    def test_missing_current_timeline_returns_structured_failure(self):
        media_pool = FakeMediaPool()
        project = FakeProject(FakeTimeline("placeholder"), media_pool)
        project.timeline = None
        resolve = FakeResolve(FakeProjectManager(project))

        result = run(resolve)

        self.assert_result_shape(result)
        self.assertFalse(result["success"])
        self.assertIn("No timeline", result["message"])

    def test_missing_project_returns_structured_failure(self):
        resolve = FakeResolve(FakeProjectManager(None))

        result = run(resolve)

        self.assert_result_shape(result)
        self.assertFalse(result["success"])
        self.assertIn("No timeline", result["message"])

    def test_export_failure_does_not_import(self):
        resolve, project, manager, media_pool = make_resolve(
            self.valid_root, export_result=False
        )

        result = run(resolve)

        self.assertFalse(result["success"])
        self.assertIsNone(media_pool.import_path)
        self.assertFalse(manager.saved)
        self.assertIsNone(project.activated_timeline)

    def test_no_matching_pair_does_not_import(self):
        root = timeline_xml(
            [
                'time="0s" value="0s" interp="linear"',
                'time="10s" value="10s" interp="linear"',
            ],
            target_lane="2",
        )
        resolve, project, manager, media_pool = make_resolve(root)

        result = run(resolve)

        self.assertFalse(result["success"])
        self.assertIn("No aligned clip", result["message"])
        self.assertIsNone(media_pool.import_path)

    def test_invalid_map_returns_failure_before_import(self):
        root = timeline_xml(
            ['time="0s" value="0s" interp="smooth2"']
        )
        resolve, project, manager, media_pool = make_resolve(root)

        result = run(resolve)

        self.assertFalse(result["success"])
        self.assertIn("fewer than two points", result["message"])
        self.assertIsNone(media_pool.import_path)

    def test_import_failure_returns_structured_failure(self):
        resolve, project, manager, media_pool = make_resolve(
            self.valid_root, import_result=False
        )

        result = run(resolve)

        self.assert_result_shape(result)
        self.assertFalse(result["success"])
        self.assertIn("import failed", result["message"])
        self.assertFalse(manager.saved)

    def test_success_uses_unique_name_and_finishes_resolve_workflow(self):
        resolve, project, manager, media_pool = make_resolve(
            self.valid_root,
            timeline_names=["Original", "Original - matched speed ramps"],
        )

        result = run(resolve)

        self.assert_result_shape(result)
        self.assertTrue(result["success"])
        self.assertEqual(
            result["timeline_name"], "Original - matched speed ramps 2"
        )
        self.assertEqual(
            result["copied_pairs"],
            [{"source": "source clip", "target": "target clip"}],
        )
        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            media_pool.import_options["timelineName"],
            "Original - matched speed ramps 2",
        )
        self.assertFalse(media_pool.import_options["importSourceClips"])
        self.assertEqual(
            media_pool.import_options["sourceClipsFolders"],
            [media_pool.root_folder, media_pool.root_folder.children[0]],
        )
        self.assertIn("<!DOCTYPE fcpxml>", media_pool.imported_xml)
        self.assertIs(project.activated_timeline, media_pool.imported_timeline)
        self.assertTrue(manager.saved)
        self.assertEqual(
            media_pool.imported_timeline.clips[0].applied_color, "Blue"
        )

    def test_post_import_failures_are_success_with_warnings(self):
        resolve, project, manager, media_pool = make_resolve(
            self.valid_root,
            activate_result=False,
            save_result=False,
            timeline_names=["Original", "Original - matched speed ramps"],
            target_accepts_color=False,
        )

        result = run(resolve)

        self.assertTrue(result["success"])
        self.assertEqual(len(result["warnings"]), 3)
        self.assertTrue(any("colour" in warning for warning in result["warnings"]))
        self.assertTrue(any("open" in warning for warning in result["warnings"]))
        self.assertTrue(any("saved" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
