from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from denoise_paths import normalized_sources, output_stem, plate_identifier, resolve_output, source_identity


class DenoisePathsTests(unittest.TestCase):
    def test_normalization_and_plate_identity(self):
        self.assertEqual(normalized_sources([' a.mov ', 'offline', 'a.mov', None, '—', 'b.mov']), ['a.mov', 'b.mov'])
        self.assertIsNone(plate_identifier(1, 1))
        self.assertEqual(plate_identifier(2, 3), 'plate_02')
        self.assertEqual(source_identity('./a.mov'), source_identity('a.mov'))
        self.assertEqual(output_stem('shot: name', 'plate_02'), 'shot_ name_plate_02_DN')

    def test_latest_populated_version_per_plate_and_future_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, first, last = resolve_output(root, 'shot name', 'plate_01')
            self.assertIn('shot name_plate_01_DN_v001', str(path))
            self.assertIsNone(first)
            self.assertFalse((root/'renders').exists())
            for plate, version in [('plate_01', 2), ('plate_01', 10), ('plate_01', 11), ('plate_02', 3)]:
                stem = f'shot name_{plate}_DN_v{version:03}'
                folder = root/'renders/precomp'/stem
                folder.mkdir(parents=True)
                if version != 11:
                    for frame in (1001, 1010):
                        (folder/f'{stem}_{frame}.exr').touch()
            path, first, last = resolve_output(root, 'shot name', 'plate_01')
            self.assertIn('_v010', str(path))
            self.assertEqual((first, last), (1001, 1010))
            self.assertIn('_v003', str(resolve_output(root, 'shot name', 'plate_02')[0]))
            self.assertIn('shot name_DN_v001', str(resolve_output(root, 'shot name')[0]))
