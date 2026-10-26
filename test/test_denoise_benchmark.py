from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app_cache import cache_directory
from test.denoise_benchmark import isolated_script, stage_timings


class DenoiseBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=cache_directory("temp"))
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "original shot" / "scripts" / "shot_v001.nk"
        self.source.parent.mkdir(parents=True)
        self.copy_directory = self.directory / "benchmark"
        self.copy_directory.mkdir()
        self.text = '''Root {
 name original.nk
 project_directory "[python {nuke.script_directory()}]"
 first_frame 1001
 last_frame 1059
}
Read {
 file ../plates/source.mov
 name Read1
}
OFXcom.absoft.NeatVideo6_v6 {
 ClipParams unchanged-profile-and-filter-settings
 name "Reduce Noise v6_1"
}
Write {
 file ../renders/other.####.exr
 name OtherWrite
}
Write {
 file ../renders/denoise.####.exr
 file_type exr
 name WriteDN1
}
'''
        self.source.write_text(self.text)

    def test_copy_preserves_source_and_filter_settings_and_redirects_only_denoise(self):
        copy = isolated_script(self.source, self.copy_directory)
        self.assertEqual(self.source.read_text(), self.text)
        self.assertEqual(copy.name, self.source.name)
        text = copy.read_text()
        self.assertIn(f' project_directory "{self.source.parent}"', text)
        self.assertIn(f' file "{self.copy_directory}/renders/denoise.####.exr"', text)
        for preserved in (" file ../plates/source.mov", " file ../renders/other.####.exr",
                          " ClipParams unchanged-profile-and-filter-settings", " last_frame 1059"):
            self.assertIn(preserved, text)
        self.assertFalse((self.source.parent.parent / "renders").exists())

    def test_missing_or_duplicate_target_fails_before_copying(self):
        for text in (self.text.replace("name WriteDN1", "name OtherWrite2"),
                     self.text + "Write {\n file unwanted.exr\n name WriteDN1\n}\n"):
            with self.subTest(text=text[-60:]):
                self.source.write_text(text)
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    isolated_script(self.source, self.copy_directory)
                self.assertFalse(list(self.copy_directory.iterdir()))

    def test_missing_output_path_or_project_directory_fails_closed(self):
        for removed in (" file ../renders/denoise.####.exr\n",
                        ' project_directory "[python {nuke.script_directory()}]"\n'):
            with self.subTest(removed=removed):
                self.source.write_text(self.text.replace(removed, ""))
                with self.assertRaises(ValueError):
                    isolated_script(self.source, self.copy_directory)
                self.assertFalse(list(self.copy_directory.iterdir()))

    def test_render_timing_excludes_process_shutdown(self):
        events = [
            {"stage": "starting", "elapsed_seconds": 0.2},
            {"stage": "debug", "elapsed_seconds": 0.5},
            {"stage": "rendering", "completed": 0, "elapsed_seconds": 20.0},
            {"stage": "rendering", "completed": 1, "elapsed_seconds": 21.0},
            {"stage": "rendering", "completed": 2, "elapsed_seconds": 22.0},
            {"stage": "complete", "elapsed_seconds": 25.0},
        ]
        stages = stage_timings(events, 25.0)
        self.assertIn({"stage": "rendering", "seconds": 2.0}, stages)
        self.assertIn({"stage": "shutdown", "seconds": 3.0}, stages)
        self.assertEqual(sum(stage["seconds"] for stage in stages), 25.0)


if __name__ == "__main__":
    unittest.main()
