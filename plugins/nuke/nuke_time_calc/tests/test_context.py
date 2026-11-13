"""Strict, offline context resolution: no filename inference or stale fallback."""
import unittest


class ContextTests(unittest.TestCase):
    def test_invalid_binding_is_rejected_before_resolving(self):
        from nuke_time_calc.context import ContextResolver
        for item in (dict(job_id=True, shot_id=2, base_path='/show/SH010'),
                     dict(job_id='12', shot_id=2, base_path='/show/SH010'),
                     dict(job_id=12, shot_id=0, base_path='/show/SH010'),
                     dict(job_id=12, shot_id=2, base_path='SH010'),
                     dict(job_id=12, shot_id=2, base_path='/')):
            with self.subTest(item=item), self.assertRaises(ValueError):
                ContextResolver([item])

    def test_ambiguous_and_traversing_paths_do_not_reuse_context(self):
        from nuke_time_calc.context import ContextResolver
        resolver = ContextResolver([dict(job_id=12, shot_id=1, base_path='/show/SH010'),
                                    dict(job_id=12, shot_id=2, base_path='/show/SH010/other')])
        self.assertIsNone(resolver('/show/SH010/other/v001.nk'))
        self.assertIsNone(resolver('/show/SH010/../unknown/v001.nk'))
        self.assertIsNone(resolver('/show/SH010/v001.txt'))

    def test_verified_folder_resolves_versions_and_unknown_stops(self):
        from nuke_time_calc.context import ContextResolver
        resolver = ContextResolver([dict(job_id=12, shot_id=34,
                                        base_path='/Volumes/projects/PROJECTS/show/SH010')])
        self.assertEqual(resolver.resolve('Z:/PROJECTS/show/SH010/nuke/v002.nk'),
                         dict(job_id=12, shot_id=34, script_path='Z:/PROJECTS/show/SH010/nuke/v002.nk'))
        self.assertEqual(resolver('/Volumes/projects/PROJECTS/show/SH010/v003.nk')['shot_id'], 34)
        for path in ('', 'Root', '/elsewhere/SH010/v002.nk',
                     'Z:/PROJECTS/show/SH010_other/v002.nk',
                     'Z:/PROJECTS_other/show/SH010/v002.nk'):
            self.assertIsNone(resolver.resolve(path), path)
