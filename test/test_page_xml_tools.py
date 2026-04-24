from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


FRONTEND_DIR = Path(__file__).resolve().parents[1]
if str(FRONTEND_DIR) not in sys.path:
    sys.path.insert(0, str(FRONTEND_DIR))

import page_xmlTools as module


class PageXmlToolsTests(unittest.TestCase):
    def test_resolve_xml_clip_duration_keeps_xml_cut_length(self):
        config = module.ConfigureMixin.__new__(module.ConfigureMixin)

        duration = config.resolve_xml_clip_duration(
            "seq010",
            "63",
            "1001",
            "1063",
            10,
        )

        self.assertEqual(duration, 63)

    def test_resolve_xml_clip_duration_keeps_62_frame_cut_with_25_handles(self):
        config = module.ConfigureMixin.__new__(module.ConfigureMixin)

        duration = config.resolve_xml_clip_duration(
            "seq011",
            "62",
            "46",
            "88",
            25,
        )

        self.assertEqual(duration, 62)

    def test_resolve_xml_clip_duration_falls_back_to_inclusive_range(self):
        config = module.ConfigureMixin.__new__(module.ConfigureMixin)

        duration = config.resolve_xml_clip_duration(
            "seq020",
            None,
            "1001",
            "1063",
            10,
        )

        self.assertEqual(duration, 63)

    def test_resolve_xml_clip_duration_logs_mismatch_but_uses_xml_duration(self):
        config = module.ConfigureMixin.__new__(module.ConfigureMixin)

        with mock.patch("builtins.print") as mocked_print:
            duration = config.resolve_xml_clip_duration(
                "seq030",
                "63",
                "990",
                "1063",
                10,
            )

        self.assertEqual(duration, 63)
        mocked_print.assert_any_call(
            "[XMLTools] Duration mismatch for seq030: duration=63, inclusive_range=74. Using XML duration."
        )

    def test_folder_setup_uses_xml_duration_for_multi_shot_scripts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            clip_path = root / "source.mov"
            clip_path.write_bytes(b"clip")

            config = module.ConfigureMixin.__new__(module.ConfigureMixin)
            config.user = "Jack"
            config.localwork = False
            config.projectRoot = str(root)
            config.editname = "timeline_A"
            config.shotNumberPad = 3
            config.shoName = "sho"
            config.colourspace = "ACES"
            config.v1StartFrames = [1001]
            config.v1EndFrames = [1012]

            with mock.patch.object(config, "movDuration", return_value=100):
                with mock.patch.object(config, "movFormat", return_value="1920 1080"):
                    config.folderSetup(
                        shotIndex=1,
                        read_paths=[str(clip_path)],
                        thumb=None,
                        duration=12,
                        single=False,
                    )

            script_path = root / "Nuke" / "timeline_A" / "sho020" / "scripts" / "sho020_v01.nk"
            contents = script_path.read_text(encoding="utf-8")

            self.assertIn("last_frame 1012", contents)
            self.assertIn("last 12", contents)
            self.assertNotIn("last_frame 1100", contents)
            self.assertNotIn("last 100", contents)


if __name__ == "__main__":
    unittest.main()
