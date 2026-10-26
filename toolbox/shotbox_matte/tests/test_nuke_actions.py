"""Job diagnostics and completed-result selection through public actions."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'plugins/nuke'))
import shotbox_nuke
from shotbox_nuke import cache, qt_compat
import test_nuke_published_cache as published_tests

completed = published_tests.completed
project_precomp = published_tests.project_precomp
published_receipt = published_tests.published_receipt


class Knob:
    def __init__(self, value):
        self.saved = value

    def value(self):
        return self.saved


@pytest.fixture
def jobs(published_receipt, tmp_path, monkeypatch):
    active = published_receipt
    pending = tmp_path / 'failed regeneration/integration.json'
    data = cache.read_manifest(active)
    failed = deepcopy(data)
    failed.pop('storage')
    failed.pop('precomp')
    failed.update(state='failed', attempt_id=str(uuid.uuid4()), message='Latest generation failed: CUDA out of memory')
    cache.atomic_write_json(pending, failed)
    node = {'sb_active_manifest': Knob(str(active)), 'sb_pending_manifest': Knob(str(pending))}
    messages, opened = [], []
    fake = SimpleNamespace(thisNode=lambda: node, message=messages.append)
    monkeypatch.setitem(sys.modules, 'nuke', fake)
    monkeypatch.setitem(sys.modules, 'PySide6', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'PySide6.QtGui', SimpleNamespace(QDesktopServices=SimpleNamespace(openUrl=opened.append)))
    monkeypatch.setattr(qt_compat, 'qt_core', lambda: SimpleNamespace(QUrl=SimpleNamespace(fromLocalFile=lambda x: x)))
    return SimpleNamespace(node=node, active=active, pending=pending, data=data, messages=messages, opened=opened)


def test_open_log_shows_latest_failure_and_open_folder_still_uses_active_output(jobs):
    shotbox_nuke.action('log')
    assert len(jobs.messages) == 1
    assert 'CUDA out of memory' in jobs.messages[0] and str(jobs.pending) in jobs.messages[0]
    assert str(jobs.active) not in jobs.messages[0]
    shotbox_nuke.action('folder')
    assert jobs.opened == [jobs.data['storage']['output_directory']]


def test_open_log_opens_latest_live_log_instead_of_previous_success(jobs):
    log = jobs.pending.parent / 'stderr.log'
    log.write_text('Worker error details')
    shotbox_nuke.action('log')
    assert jobs.opened == [str(log)] and not jobs.messages


def test_failed_or_missing_pending_does_not_hide_reloadable_active_result(jobs):
    assert shotbox_nuke.result_manifest(jobs.node) == jobs.active
    jobs.pending.unlink()
    assert shotbox_nuke.result_manifest(jobs.node) == jobs.active
    assert shotbox_nuke.job_manifest(jobs.node) == jobs.pending


def test_new_completed_attempt_is_available_for_explicit_reload(jobs):
    data = deepcopy(jobs.data)
    data['attempt_id'] = str(uuid.uuid4())
    cache.atomic_write_json(jobs.pending, data)
    assert shotbox_nuke.result_manifest(jobs.node) == jobs.pending


def test_relinked_receipt_wins_over_old_receipt_for_same_attempt(jobs):
    cache.atomic_write_json(jobs.pending, jobs.data)
    assert shotbox_nuke.result_manifest(jobs.node) == jobs.active


def test_no_result_or_job_reports_clear_error(jobs):
    jobs.node['sb_active_manifest'] = Knob('')
    jobs.node['sb_pending_manifest'] = Knob('')
    with pytest.raises(RuntimeError, match='No completed matte'):
        shotbox_nuke.result_manifest(jobs.node)
    with pytest.raises(RuntimeError, match='No job'):
        shotbox_nuke.job_manifest(jobs.node)
