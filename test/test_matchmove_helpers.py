from __future__ import annotations

import os
import sys
from pathlib import Path
import unittest
from unittest import mock

FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import matchmove_helpers
import settings


class DummySettingsManager:
    def __init__(self, values: dict[str, object] | None = None):
        self._values = dict(values or {})

    def get(self, key: str, default=None):
        return self._values.get(key, default)


class MatchmoveHelpersTests(unittest.TestCase):
    def test_resolve_3de_executable_prefers_settings_path(self):
        configured_path = os.path.abspath("/tmp/fake_3de/run_3DE4")
        original_manager = settings._settings_manager
        settings._settings_manager = DummySettingsManager({"threede_exe_path": configured_path})
        self.addCleanup(setattr, settings, "_settings_manager", original_manager)

        with mock.patch.object(matchmove_helpers, "THREEDE_CMD", ""), \
            mock.patch.object(matchmove_helpers, "POSIX_THREEDE_CANDIDATES", ("/opt/fallback/run_3DE4",)), \
            mock.patch.object(matchmove_helpers.os.path, "isfile", side_effect=lambda path: path == configured_path), \
            mock.patch.object(matchmove_helpers.shutil, "which", return_value=None):
            resolved = matchmove_helpers.resolve_3de_executable()

        self.assertEqual(resolved, configured_path)

    def test_build_3de_frame_mapping_keeps_source_range_and_local_playback(self):
        mapping = matchmove_helpers.build_3de_frame_mapping(1005, 1010)

        self.assertEqual(
            mapping,
            {
                "sequence_start": 1005,
                "sequence_end": 1010,
                "playback_start": 1,
                "playback_end": 6,
                "frame_offset": 1005,
                "frame_count": 6,
            },
        )

    def test_runtime_script_uses_local_frame_indices_for_3de_ranges(self):
        sequence_info = matchmove_helpers.SequenceInfo(
            folder_path="/tmp/plate",
            example_file="/tmp/plate/plate.1001.exr",
            sequence_path_pattern="/tmp/plate/plate.####.exr",
            display_pattern="plate.####.exr",
            prefix="plate.",
            suffix=".exr",
            padding=4,
            first_frame=1001,
            last_frame=1012,
            frames=tuple(range(1001, 1013)),
            width=2048,
            height=1152,
            header_pixel_aspect=1.0,
        )
        clip = matchmove_helpers.MatchmoveClipRequest(
            slot_index=0,
            clip_name="plate",
            camera_name="cam_01",
            lens_name="35mm",
            sequence_info=sequence_info,
            focal_length_mm=35.0,
            sequence_start_frame=1005,
            sequence_end_frame=1010,
        )
        request = matchmove_helpers.MatchmoveProjectRequest(
            project_name="sho010",
            shot_name="sho010",
            clips=(clip,),
            camera_preset_name="Alexa 35",
            matchmove_dir="/tmp/matchmove",
            export_dir="/tmp/matchmove/export",
            fps=24.0,
            project_dir="/tmp/matchmove/work",
            project_path="/tmp/matchmove/work/sho010_matchmove_v001.3de",
            version=1,
        )

        script = matchmove_helpers.build_3de_runtime_script(
            request,
            "/tmp/runtime.py",
            "/tmp/status.json",
        )

        self.assertIn('"sequence_start": 1005', script)
        self.assertIn('"sequence_end": 1010', script)
        self.assertIn('"playback_start": 1', script)
        self.assertIn('"playback_end": 6', script)
        self.assertIn('"frame_offset": 1005', script)
        self.assertIn('tde4.setCameraFrameOffset(camera, clip["frame_offset"])', script)
        self.assertIn(
            'tde4.setCameraPlaybackRange(camera, clip["playback_start"], clip["playback_end"])',
            script,
        )
        self.assertIn(
            'tde4.setCameraCalculationRange(camera, clip["playback_start"], clip["playback_end"])',
            script,
        )
        self.assertIn('tde4.setCurrentFrame(camera, clip["playback_start"])', script)
        self.assertNotIn(
            'tde4.setCameraPlaybackRange(camera, clip["sequence_start"], clip["sequence_end"])',
            script,
        )


if __name__ == "__main__":
    unittest.main()
