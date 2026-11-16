"""Strict, offline context resolution: no filename inference or stale fallback."""
import unittest


class ContextTests(unittest.TestCase):
    def test_windows_mapped_drive_and_unc_target_share_verified_context(self):
        from nuke_time_calc import context
        from types import SimpleNamespace
        from unittest.mock import patch, Mock
        root = r'\\192.168.10.20\projects\PROJECTS'
        path_factory = Mock(return_value=Mock(resolve=Mock(return_value=root)))
        aliases = (('Z:/PROJECTS', '/mnt/projects/PROJECTS'),)
        with patch.object(context, 'os', SimpleNamespace(name='nt')), \
                patch.object(context, 'Path', path_factory), \
                patch.object(context, 'ROOT_ALIASES', aliases):
            resolver = context.ContextResolver([dict(job_id=12, shot_id=34,
                base_path='/mnt/projects/PROJECTS/show/SH010')])
            for script in ('Z:/PROJECTS/show/SH010/scripts/v002.nk',
                           root + r'\show\SH010\scripts\v002.nk',
                           root.lower() + r'\SHOW\sh010\scripts\v002.nk'):
                self.assertEqual(resolver(script)['shot_id'], 34)
            for script in (root + r'\show\SH011\scripts\v002.nk',
                           root + r'_other\show\SH010\v002.nk',
                           r'\\another-server\projects\PROJECTS\show\SH010\v002.nk'):
                self.assertIsNone(resolver(script))
            self.assertTrue(all(call.args == ('Z:/PROJECTS',)
                                for call in path_factory.call_args_list))

    def test_unavailable_windows_mount_does_not_invent_unc_alias(self):
        from nuke_time_calc import context
        from types import SimpleNamespace
        from unittest.mock import patch, Mock
        with patch.object(context, 'os', SimpleNamespace(name='nt')), \
                patch.object(context, 'Path', Mock(return_value=Mock(resolve=Mock(side_effect=OSError)))):
            resolver = context.ContextResolver([dict(job_id=12, shot_id=34,
                base_path='Z:/PROJECTS/show/SH010')])
            self.assertIsNone(resolver(r'\\unknown\projects\PROJECTS\show\SH010\v001.nk'))

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
