"""Exercise the actual dashboard launch method without loading a Qt window."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_managed_setup as setup_tests


class LaunchTests(unittest.TestCase):
    setUp = setup_tests.SetupTests.setUp

    def method(self, name='_on_nuke_lock_claimed'):
        source = Path(__file__).resolve().parents[4] / 'page_nukedash.py'
        tree = ast.parse(source.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'page_nukedash')
        method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
        method.decorator_list = []
        helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_nuke_time_identity')
        module = ast.Module(body=[helper, method], type_ignores=[])
        namespace = {'QMessageBox': SimpleNamespace(warning=lambda *a: self.fail('unexpected popup')),
                     'widgets': SimpleNamespace(ShotCard=object), 'parse_lock_info': lambda value: None}
        exec(compile(module, str(source), 'exec'), namespace)
        return namespace[method.name]

    def test_api_snapshot_carries_request_identity_not_later_settings(self):
        source = Path(__file__).resolve().parents[4] / 'page_nukedash.py'
        tree = ast.parse(source.read_text())
        worker = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ApiWorker')
        fetch = next(node for node in worker.body if isinstance(node, ast.FunctionDef) and node.name == 'fetch')
        fetch.decorator_list = []
        helpers = [node for node in tree.body if getattr(node, 'name', '') in
                   ('_nuke_time_identity', '_NukeJobsSnapshot')]
        settings = dict(server_url='https://shotbox.example', django_username=1)
        namespace = dict(get_settings_manager=lambda: settings,
                         QThread=SimpleNamespace(currentThread=lambda: SimpleNamespace(isInterruptionRequested=lambda: False)))
        exec(compile(ast.Module(body=helpers + [fetch], type_ignores=[]), str(source), 'exec'), namespace)
        for changed in (False, True, 'api_origin'):
            settings['django_username'] = 1
            results = []
            def get_jobs():
                if changed == 'api_origin':
                    api_worker.api.base_url = 'https://other.example/api/'
                elif changed:
                    api_worker._time_reporting_binding = (('https', 'shotbox.example', 2), 2)
                return [dict(id=12)]
            api_worker = SimpleNamespace(_time_reporting_binding=(('https', 'shotbox.example', 1), 1),
                api=SimpleNamespace(base_url='https://shotbox.example/api/',
                getAPI=get_jobs, get_users=lambda: []), _users_loaded_at=None,
                users_ready=SimpleNamespace(emit=lambda value: None),
                data_ready=SimpleNamespace(emit=results.append),
                error=SimpleNamespace(emit=lambda value: self.fail(value)))
            import time
            namespace['time'] = time
            namespace['fetch'](api_worker)
            self.assertEqual(getattr(results[0], 'source_context', None),
                             None if changed else ('https', 'shotbox.example', 1))

    def test_context_setter_invalidates_cached_and_pending_provenance(self):
        dashboard = SimpleNamespace(_worker=SimpleNamespace(),
            _time_reporting_binding=(('https', 'shotbox.example', 1), 1),
            _jobs_source_context=('https', 'shotbox.example', 1), _jobs_source_generation=1)
        setter = self.method('set_time_reporting_context')
        setter(dashboard, 'https://other.example', 2)
        self.assertEqual(dashboard._time_reporting_binding, (('https', 'other.example', 2), 2))
        self.assertEqual(dashboard._worker._time_reporting_binding, dashboard._time_reporting_binding)
        self.assertIsNone(dashboard._jobs_source_context)
        self.assertIsNone(dashboard._jobs_source_generation)
        setter(dashboard, 'https://shotbox.example', 1)
        self.assertEqual(dashboard._time_reporting_binding, (('https', 'shotbox.example', 1), 3))
        setter(dashboard, 'https://shotbox.example/api/', 1)
        self.assertEqual(dashboard._time_reporting_binding[1], 3, 'same identity is not a switch')

    def test_open_request_captures_fresh_shot_not_card_or_selected_job(self):
        fresh = dict(id=34, base_path='/show/current', timeline=7, nuke_in_use=None)
        card = SimpleNamespace(data=dict(id=34, base_path='/show/stale', title='Shot'), filesIO=object())
        requests = []
        jobs = {12: dict(id=12, timelines=[dict(id=7, shots=[fresh])])}
        dashboard = SimpleNamespace(_pending_nuke_opens={},
            _time_reporting_binding=(('https', 'shotbox.example', 1), 1),
            _jobs_source_generation=1,
            _jobs_source_context=('https', 'shotbox.example', 1), _jobs_by_id=jobs,
            _lock_read_api=SimpleNamespace(base_url='https://shotbox.example/api/', get_shot=lambda shot_id: fresh,
                get_lock_owner_id=lambda: 'owner', get_lock_owner_ids=lambda: ['owner']),
            _apply_shot_lock_payload=lambda shot: None,
            lock_claim_requested=SimpleNamespace(emit=requests.append))
        self.method('_handle_nuke_open_request')(dashboard, card, '/show/current/v001.nk')
        pending = dashboard._pending_nuke_opens[34]
        self.assertEqual(pending.get('time_shot'), fresh)
        self.assertEqual(pending.get('time_context'), ('https', 'shotbox.example', 1))
        self.assertEqual(pending.get('time_jobs'), list(jobs.values()))
        fresh['id'] = 999
        self.assertEqual(pending['time_shot']['id'], 34)
        self.assertEqual(pending['time_jobs'][0]['timelines'][0]['shots'][0]['id'], 34)
        self.assertEqual(requests[0]['base_path'], '/show/current')

    def test_stale_snapshot_or_api_origin_never_supplies_launch_provenance(self):
        for snapshot_context, snapshot_generation, api_server in (
                (('https', 'old.example', 1), 2, 'https://shotbox.example/api/'),
                (('https', 'shotbox.example', 2), 2, 'https://shotbox.example/api/'),
                (('https', 'shotbox.example', 1), 1, 'https://shotbox.example/api/'),
                (('https', 'shotbox.example', 1), 2, 'https://old.example/api/'),
                (None, None, 'https://shotbox.example/api/')):
            fresh = dict(id=34, base_path='/show/current', nuke_in_use=None)
            dashboard = SimpleNamespace(_pending_nuke_opens={},
                _time_reporting_binding=(('https', 'shotbox.example', 1), 2),
                _jobs_source_context=snapshot_context, _jobs_source_generation=snapshot_generation,
                _jobs_by_id={12: dict(id=12)},
                _lock_read_api=SimpleNamespace(base_url=api_server, get_shot=lambda shot_id: fresh,
                    get_lock_owner_id=lambda: 'owner', get_lock_owner_ids=lambda: ['owner']),
                _apply_shot_lock_payload=lambda shot: None,
                lock_claim_requested=SimpleNamespace(emit=lambda request: None))
            card = SimpleNamespace(data=dict(id=34), filesIO=object())
            self.method('_handle_nuke_open_request')(dashboard, card, '/show/current/v001.nk')
            self.assertIsNone(dashboard._pending_nuke_opens[34]['time_context'])

    def test_origin_change_during_fresh_shot_read_refuses_handoff(self):
        fresh = dict(id=34, base_path='/show/current', nuke_in_use=None)
        dashboard = SimpleNamespace(_pending_nuke_opens={},
            _time_reporting_binding=(('https', 'shotbox.example', 1), 1),
            _jobs_source_context=('https', 'shotbox.example', 1), _jobs_source_generation=1,
            _jobs_by_id={12: dict(id=12)}, _apply_shot_lock_payload=lambda shot: None,
            lock_claim_requested=SimpleNamespace(emit=lambda request: None))
        def read(shot_id):
            dashboard._lock_read_api.base_url = 'https://other.example/api/'
            return fresh
        dashboard._lock_read_api = SimpleNamespace(base_url='https://shotbox.example/api/',
            get_shot=read, get_lock_owner_id=lambda: 'owner', get_lock_owner_ids=lambda: ['owner'])
        self.method('_handle_nuke_open_request')(dashboard,
            SimpleNamespace(data=dict(id=34), filesIO=object()), '/show/current/v001.nk')
        self.assertIsNone(dashboard._pending_nuke_opens[34]['time_context'])

    def test_provenance_failure_cannot_block_normal_open_request(self):
        fresh = dict(id=34, base_path='/show/current', nuke_in_use=None)
        requests = []
        dashboard = SimpleNamespace(_pending_nuke_opens={},
            _time_reporting_binding=(('https', 'shotbox.example', 1), 1),
            _lock_read_api=SimpleNamespace(base_url='https://shotbox.example/api/',
                get_shot=lambda shot_id: fresh,
                get_lock_owner_id=lambda: 'owner', get_lock_owner_ids=lambda: ['owner']),
            _apply_shot_lock_payload=lambda shot: None,
            lock_claim_requested=SimpleNamespace(emit=requests.append))
        method = self.method('_handle_nuke_open_request')
        def unavailable(*args):
            raise RuntimeError('optional time plugin unavailable')
        method.__globals__['_nuke_time_identity'] = unavailable
        method(dashboard, SimpleNamespace(data=dict(id=34), filesIO=object()), '/show/current/v001.nk')
        self.assertEqual(len(requests), 1)
        self.assertIsNone(dashboard._pending_nuke_opens[34]['time_context'])

    def test_changed_desktop_identity_skips_handoff_but_launches(self):
        from plugins.nuke.nuke_time_calc import managed_setup as setup
        for current in (('https://other.example', 1), ('https://shotbox.example', 2),
                        ('https://shotbox.example', 1), (None, None)):
            launched = []
            dashboard = SimpleNamespace(_pending_nuke_opens={34: dict(
                files_io=SimpleNamespace(open_file=launched.append), shot_title='Shot',
                time_shot=dict(id=34), time_context=('https', 'shotbox.example', 1),
                time_jobs=[dict(id=12)], time_generation=1)},
                _time_reporting_binding=((('https', current[0].split('://')[1], current[1]) if current[0] else None), 2),
                _jobs_by_id={}, _shutting_down=False,
                lock_launch_finished=SimpleNamespace(emit=lambda *a: None))
            with self.subTest(current=current), patch.object(setup, 'record_launch_context') as handoff:
                self.method()(dashboard, dict(shot_id=34, script_path='/normal/shot.nk', result=dict(ok=True)))
                handoff.assert_not_called()
                self.assertEqual(launched, ['/normal/shot.nk'])

    def test_disabled_handoff_never_changes_normal_launch(self):
        from plugins.nuke.nuke_time_calc import managed_setup as setup
        launched, finished = [], []
        dashboard = SimpleNamespace(_pending_nuke_opens={34: dict(
            files_io=SimpleNamespace(open_file=launched.append), shot_title='Shot')},
            _jobs_by_id={}, _shutting_down=False,
            lock_launch_finished=SimpleNamespace(emit=lambda *a: finished.append(a)))
        with patch.object(setup, 'default_config_path', return_value=self.config):
            self.method()(dashboard, dict(shot_id=34, script_path='/normal/shot.nk', result=dict(ok=True)))
        self.assertEqual(launched, ['/normal/shot.nk'])
        self.assertEqual(finished, [(34, True)])
        self.assertFalse(self.config.exists())

    def test_invalid_private_config_does_not_block_launch_or_log_secrets(self):
        from plugins.nuke.nuke_time_calc import managed_setup as setup
        self.config.parent.mkdir()
        self.config.write_text('{"enabled": true, "token": "NEVER_LOG_THIS"}')
        self.config.chmod(0o600)
        launched = []
        dashboard = SimpleNamespace(_pending_nuke_opens={34: dict(
            files_io=SimpleNamespace(open_file=launched.append), shot_title='Shot',
            time_context=('https', 'shotbox.example', 1), time_generation=1)},
            _time_reporting_binding=(('https', 'shotbox.example', 1), 1),
            _jobs_by_id={}, _shutting_down=False,
            lock_launch_finished=SimpleNamespace(emit=lambda *a: None))
        with patch.object(setup, 'default_config_path', return_value=self.config), self.assertLogs(level='WARNING') as logs:
            self.method()(dashboard, dict(shot_id=34, script_path='/normal/shot.nk', result=dict(ok=True)))
        self.assertEqual(launched, ['/normal/shot.nk'])
        self.assertNotIn('NEVER_LOG_THIS', ' '.join(logs.output))

    def test_launch_uses_existing_os_association_after_persisting_known_context(self):
        import uuid
        from plugins.nuke.nuke_time_calc import managed_setup as setup
        from plugins.nuke.nuke_time_calc.context import ContextResolver
        folder = self.root / 'shot'
        folder.mkdir()
        script = folder / 'v001.nk'
        script.write_text('Root {}')
        shot = dict(id=34, timeline=7, base_path=str(folder))
        setup.save_config(dict(enabled=True, artist_id=1, server='https://shotbox.example',
                               device_id=str(uuid.uuid4()), token_file=str(self.root / 'token'),
                               outbox_dir=str(self.root / 'outbox')), self.config)
        launched, finished = [], []
        def launch(path):
            binding = ContextResolver(setup.load_config(self.config)['mappings'])(path)
            self.assertIsNotNone(binding, 'approved mapping must be durable before OS launch')
            self.assertEqual(binding['job_id'], 12)
            launched.append(path)
        dashboard = SimpleNamespace(_pending_nuke_opens={34: dict(
            files_io=SimpleNamespace(open_file=launch), shot_title='Shot', time_shot=shot,
            time_context=('https', 'shotbox.example', 1), time_generation=1,
            time_jobs=[dict(id=12, timelines=[dict(id=7, shots=[shot])])])},
            _time_reporting_binding=(('https', 'shotbox.example', 1), 1),
            _jobs_by_id={12: dict(id=12, timelines=[dict(id=7, shots=[shot])])},
            _shutting_down=False, lock_launch_finished=SimpleNamespace(emit=lambda *a: finished.append(a)))
        with patch.object(setup, 'default_config_path', return_value=self.config):
            self.method()(dashboard, dict(shot_id=34, script_path=str(script), result=dict(ok=True)))
        self.assertEqual(launched, [str(script)])
        self.assertEqual(finished, [(34, True)])
