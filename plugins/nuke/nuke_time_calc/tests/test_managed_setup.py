"""Real-filesystem setup tests; never touch the user's Nuke startup."""
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest


class SetupTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[4] / '.cache' / 'nuke-time-setup-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=str(scratch))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'state' / 'config.json'

    def test_invalid_configuration_cannot_enable_or_overwrite(self):
        from nuke_time_calc.managed_setup import save_config, load_config
        import uuid
        valid = dict(enabled=True, server='https://shotbox.example', artist_id=1,
                     device_id=str(uuid.uuid4()), token_file=str(self.root / 'device.token'),
                     outbox_dir=str(self.root / 'outbox'), mappings=[])
        save_config(valid, self.config)
        before = self.config.read_bytes()
        for change in (dict(enabled='yes'), dict(server='http://shotbox.example'),
                       dict(server='https://name:secret@shotbox.example'),
                       dict(server='https://shotbox.example/api/'), dict(artist_id=True),
                       dict(device_id='not-a-uuid'), dict(token_file='relative-token'),
                       dict(outbox_dir='relative-outbox'), dict(token='secret')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                save_config(dict(valid, **change), self.config)
            self.assertEqual(self.config.read_bytes(), before)
        self.config.write_text('{"enabled": true}')
        with self.assertRaises(ValueError):
            load_config(self.config)

    def test_config_rejects_symlink_and_public_permissions(self):
        from nuke_time_calc.managed_setup import save_config, load_config
        target = self.root / 'unrelated.txt'
        target.write_text('preserve')
        self.config.parent.mkdir()
        self.config.symlink_to(target)
        with self.assertRaises(ValueError):
            save_config(dict(enabled=False), self.config)
        self.assertEqual(target.read_text(), 'preserve')
        self.config.unlink()
        save_config(dict(enabled=False), self.config)
        if os.name != 'nt':
            self.config.chmod(0o644)
            with self.assertRaises(ValueError):
                load_config(self.config)

    def test_malformed_or_duplicate_registration_never_mutates_files(self):
        from nuke_time_calc.managed_setup import install, uninstall
        nuke_dir = self.root / 'nuke'
        nuke_dir.mkdir()
        menu = nuke_dir / 'menu.py'
        begin = b'# >>> SHOTBOX NUKE TIME CALC MANAGED v1 >>>'
        end = b'# <<< SHOTBOX NUKE TIME CALC MANAGED v1 <<<'
        for data in (b'keep\n' + begin + b'\n', b'keep\n' + end + b'\n',
                     b'\n' + end + b'\n' + begin + b'\n',
                     (b'\n' + begin + b'\npass\n' + end + b'\n') * 2,
                     b'\n# >>> SHOTBOX NUKE TIME CALC MANAGED v2 >>>\npass\n'):
            for action in (install, uninstall):
                menu.write_bytes(data)
                with self.subTest(data=data, action=action), self.assertRaises(ValueError):
                    action(nuke_dir=nuke_dir, config_path=self.config)
                self.assertEqual(menu.read_bytes(), data)
                self.assertFalse(self.config.exists())

    def test_installer_rejects_symlink_startup_without_touching_target(self):
        from nuke_time_calc.managed_setup import install
        nuke_dir = self.root / 'nuke'
        nuke_dir.mkdir()
        target = self.root / 'important.py'
        target.write_text('keep = True')
        (nuke_dir / 'menu.py').symlink_to(target)
        with self.assertRaises(ValueError):
            install(nuke_dir=nuke_dir, config_path=self.config)
        self.assertEqual(target.read_text(), 'keep = True')

    def test_install_uninstall_is_idempotent_and_preserves_existing_blocks(self):
        from nuke_time_calc.managed_setup import install, uninstall
        nuke_dir = self.root / 'nuke'
        nuke_dir.mkdir()
        menu = nuke_dir / 'menu.py'
        original = (b'# Matte managed startup\r\nprint("matte")\r\n'
                    b'# local diagnostic test\r\nimport nuke_time_calc.local_test')
        menu.write_bytes(original)
        install(nuke_dir=nuke_dir, config_path=self.config)
        installed = menu.read_bytes()
        self.assertTrue(installed.startswith(original))
        self.assertIn(b'nuke_time_calc.runtime', installed)
        self.assertIn(b'.register(config_path=', installed)
        self.assertIs(json.loads(self.config.read_text())['enabled'], False)
        install(nuke_dir=nuke_dir, config_path=self.config)
        self.assertEqual(menu.read_bytes(), installed)
        uninstall(nuke_dir=nuke_dir, config_path=self.config)
        self.assertEqual(menu.read_bytes(), original)
        uninstall(nuke_dir=nuke_dir, config_path=self.config)
        self.assertEqual(menu.read_bytes(), original)
        self.assertTrue(self.config.exists(), 'rollback retains approved mappings by default')

    def test_missing_launch_provenance_preserves_existing_binding(self):
        from nuke_time_calc.managed_setup import save_config, record_launch_context
        import uuid
        folder = self.root / 'shot'
        folder.mkdir()
        script = folder / 'v001.nk'
        script.write_text('Root {}')
        shot = dict(id=35, base_path=str(folder), timeline=7)
        jobs = [dict(id=99, timelines=[dict(id=7, shots=[shot])])]
        save_config(dict(enabled=True, server='https://shotbox.example', artist_id=1,
                         device_id=str(uuid.uuid4()), token_file=str(self.root / 'token'),
                         outbox_dir=str(self.root / 'outbox'), mappings=[
                             dict(job_id=12, shot_id=34, base_path=str(folder))]), self.config)
        before = self.config.read_bytes()
        for provenance in ({}, dict(source_server='https://other.example/api/', source_artist_id=1),
                           dict(source_server='https://shotbox.example/api/', source_artist_id=2),
                           dict(source_server='https://shotbox.example:8443/api/', source_artist_id=1),
                           dict(source_server='https://shotbox.example/evil/api/', source_artist_id=1),
                           dict(source_server='https://shotbox.example/api/', source_artist_id=True)):
            for snapshot in (jobs, jobs + jobs):
                with self.subTest(provenance=provenance, conflicting=len(snapshot) > 1):
                    self.assertFalse(record_launch_context(snapshot, shot, str(script),
                                                          config_path=self.config, **provenance))
                    self.assertEqual(self.config.read_bytes(), before)
        self.assertFalse((self.root / 'outbox').exists())

    def test_opt_in_launch_records_real_snapshot_identity_for_direct_reopen(self):
        from nuke_time_calc.managed_setup import save_config, load_config, record_launch_context
        from nuke_time_calc.context import ContextResolver
        import uuid
        folder = self.root / 'show' / 'SH010'
        folder.mkdir(parents=True)
        script = folder / 'v001.nk'
        script.write_text('Root {}')
        shot = dict(id=34, base_path=str(folder), timeline=7)
        jobs = [dict(id=12, timelines=[dict(id=7, shots=[shot])])]
        self.assertFalse(record_launch_context(jobs, shot, str(script), source_server='https://shotbox.example/api/', source_artist_id=1, config_path=self.config))
        self.assertFalse(self.config.exists())
        save_config(dict(enabled=True, server='https://shotbox.example', artist_id=1,
                         device_id=str(uuid.uuid4()), token_file=str(self.root / 'token'),
                         outbox_dir=str(self.root / 'outbox')), self.config)
        self.assertTrue(record_launch_context(jobs, shot, str(script), source_server='https://shotbox.example/api/', source_artist_id=1, config_path=self.config))
        resolver = ContextResolver(load_config(self.config)['mappings'])
        self.assertEqual(resolver(str(folder / 'v002.nk'))['shot_id'], 34)
        before = self.config.read_bytes()
        for candidate, path in ((dict(shot, id='34'), str(script)),
                                (dict(shot, base_path=str(self.root / 'stale')), str(script)),
                                (dict(shot, timeline=99), str(script)),
                                (dict(shot, id=999), str(script)),
                                (shot, str(self.root / 'unknown.nk'))):
            self.assertFalse(record_launch_context(jobs, candidate, path, source_server='https://shotbox.example/api/', source_artist_id=1, config_path=self.config))
            self.assertEqual(self.config.read_bytes(), before)

    def test_launch_reassignment_replaces_persisted_overlapping_folders(self):
        from nuke_time_calc.managed_setup import save_config, load_config, record_launch_context
        from nuke_time_calc.context import ContextResolver
        from nuke_time_calc.runtime import BoundResolver
        from unittest.mock import patch
        import uuid
        folder = self.root / 'show' / 'SH010'
        folder.mkdir(parents=True)
        script = folder / 'comp' / 'v001.nk'
        script.parent.mkdir()
        script.write_text('Root {}')
        unrelated = dict(job_id=90, shot_id=91, base_path=str(self.root / 'other'))
        shot = dict(id=35, base_path=str(folder), timeline=7)
        jobs = [dict(id=12, timelines=[dict(id=7, shots=[shot])])]
        aliases = (('Z:/PROJECTS', str(self.root)),)
        for old_folder in (str(folder), str(folder.parent), str(script.parent),
                           'Z:/PROJECTS/show/SH010'):
            with self.subTest(old_folder=old_folder), patch('nuke_time_calc.context.ROOT_ALIASES', aliases):
                config = dict(enabled=True, server='https://shotbox.example', artist_id=1,
                              device_id=str(uuid.uuid4()), token_file=str(self.root / 'token'),
                              outbox_dir=str(self.root / 'outbox'), mappings=[
                                  dict(job_id=12, shot_id=34, base_path=old_folder), unrelated])
                save_config(config, self.config)
                running_resolver = BoundResolver(load_config(self.config), self.config)
                self.assertEqual(running_resolver(str(script))['shot_id'], 34)
                self.assertTrue(record_launch_context(jobs, shot, str(script), source_server='https://shotbox.example/api/', source_artist_id=1, config_path=self.config))
                persisted = load_config(self.config)
                self.assertEqual(ContextResolver(persisted['mappings'])(str(script))['shot_id'], 35)
                self.assertEqual(running_resolver(str(script))['shot_id'], 35)
                self.assertIn(unrelated, persisted['mappings'])
                self.assertEqual(len(persisted['mappings']), 2)

    def test_unverified_reassignment_persists_unresolved_not_stale_identity(self):
        from nuke_time_calc.managed_setup import save_config, load_config, record_launch_context
        from nuke_time_calc.context import ContextResolver
        from nuke_time_calc.runtime import BoundResolver
        import uuid
        folder = self.root / 'show' / 'SH010'
        folder.mkdir(parents=True)
        script = folder / 'v001.nk'
        script.write_text('Root {}')
        shot = dict(id=35, base_path=str(folder), timeline=7)
        job = dict(id=12, timelines=[dict(id=7, shots=[shot])])
        competing = dict(id=13, timelines=[dict(id=8, shots=[dict(shot, id=36)])])
        unrelated = dict(job_id=90, shot_id=91, base_path=str(folder.with_name('SH010_extra')))
        for jobs in ([job, job], [job, competing], [dict(job, id='12')]):
            with self.subTest(jobs=jobs):
                config = dict(enabled=True, server='https://shotbox.example', artist_id=1,
                              device_id=str(uuid.uuid4()), token_file=str(self.root / 'token'),
                              outbox_dir=str(self.root / 'outbox'), mappings=[
                                  dict(job_id=12, shot_id=34, base_path=str(folder)), unrelated])
                save_config(config, self.config)
                running_resolver = BoundResolver(load_config(self.config), self.config)
                self.assertFalse(record_launch_context(jobs, shot, str(script), source_server='https://shotbox.example/api/', source_artist_id=1, config_path=self.config))
                persisted = load_config(self.config)
                self.assertIsNone(ContextResolver(persisted['mappings'])(str(script)))
                self.assertIsNone(running_resolver(str(script)))
                self.assertEqual(persisted['mappings'], [unrelated])
                self.assertTrue(persisted['enabled'])

    def test_uninstall_only_removes_requested_config_never_token_or_outbox(self):
        from nuke_time_calc.managed_setup import install, uninstall
        nuke_dir = self.root / 'nuke'
        token = self.root / 'device.token'
        token.write_text('do-not-read-or-delete')
        outbox = self.root / 'outbox'
        outbox.mkdir()
        batch = outbox / 'unacknowledged.json'
        batch.write_text('{"pending":true}')
        install(nuke_dir=nuke_dir, config_path=self.config)
        uninstall(nuke_dir=nuke_dir, config_path=self.config, remove_config=True)
        self.assertFalse(self.config.exists())
        self.assertTrue(token.exists())
        self.assertTrue(batch.exists())

    def test_generated_registration_executes_literal_paths_without_eval(self):
        from nuke_time_calc.managed_setup import install
        import sys
        from unittest.mock import patch
        from types import ModuleType
        # Quotes in a state directory must stay data, never become startup code.
        config = self.root / "state' quoted" / 'config.json'
        menu = install(nuke_dir=self.root / 'nuke', config_path=config)
        runtime = ModuleType('nuke_time_calc.runtime')
        calls = []
        runtime.register = lambda **kw: calls.append(kw)
        import nuke_time_calc
        # Import may already have cached the submodule on its parent package.
        with patch.dict(sys.modules, {'nuke_time_calc.runtime': runtime}), \
                patch.object(nuke_time_calc, 'runtime', runtime, create=True):
            exec(compile(menu.read_bytes(), str(menu), 'exec'), {})
        self.assertEqual(calls, [dict(config_path=str(config))])

    def test_configuration_target_cannot_override_arbitrary_file(self):
        from nuke_time_calc.managed_setup import save_config, install, uninstall
        other = self.root / 'unrelated.py'
        other.write_text('must_survive = True')
        for action in (lambda: save_config(dict(enabled=False), other),
                       lambda: install(nuke_dir=self.root / 'nuke', config_path=other),
                       lambda: uninstall(nuke_dir=self.root / 'nuke', config_path=other, remove_config=True)):
            with self.assertRaises(ValueError):
                action()
            self.assertEqual(other.read_text(), 'must_survive = True')
        self.assertFalse((self.root / 'nuke' / 'menu.py').exists())

    def test_config_missing_is_disabled_and_private_atomic_round_trip(self):
        from nuke_time_calc.managed_setup import load_config, save_config, default_config_path
        self.assertIs(load_config(self.config)['enabled'], False)
        self.assertTrue(default_config_path().is_absolute())
        config = dict(enabled=False, mappings=[dict(job_id=1, shot_id=2, base_path='/show/SH010')])
        save_config(config, self.config)
        self.assertEqual(load_config(self.config)['mappings'], config['mappings'])
        self.assertEqual(json.loads(self.config.read_text())['enabled'], False)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)
        self.assertEqual([p.name for p in self.config.parent.iterdir()], ['config.json'])
