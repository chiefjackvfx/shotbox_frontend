"""Exercise the real dashboard Denoise button against a four-clip fixture timeline.

Only backend responses/settings are isolated. Qt dialogs, local profiling, batch
controller, Deadline submission and shared output version allocation are real.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtCore import QProcess, QTimer
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QMessageBox
import app_cache
import denoise_controller as ui
from deadline_ui import DeadlineOptions
from test.test_nukedash_regressions import NukeDashRegressionTests


@app_cache.temporary_cache_task
def acceptance(previous_path, nuke, workers, pool):
    previous = json.loads(previous_path.read_text())
    assert len(previous['clips']) == 4
    directory = Path(tempfile.mkdtemp(prefix='deadline-ui-', dir=app_cache.cache_directory('temp')))
    print(directory, flush=True)
    fixture = NukeDashRegressionTests()
    fixture.setUpClass()
    fixture.setUp()
    report = {'staging': previous['staging'], 'clips': [], 'backend': 'isolated fixture timeline'}
    try:
        fixture.settings.set('nuke_exe_path', nuke)
        fixture.settings.set('deadline_qualified_workers', workers)
        fixture.settings.set('deadline_pool', pool)
        fixture.settings.set('deadline_shared_roots', [previous['staging']])
        shots = []
        for index, clip in enumerate(previous['clips'], 1):
            request = json.loads(Path(clip['request']).read_text())
            shots.append({'id': index, 'title': Path(request['deadline']['shot_dir']).name,
                          'base_path': request['deadline']['shot_dir'], 'original_clip': request['source'],
                          'colourspace': request['colourspace'], 'fps': request['fps'], 'tasks': []})
        page = fixture.page([{'id': 1, 'title': 'Mixed farm acceptance', 'timelines': [
            {'id': 1, 'title': 'Four shared clips', 'shots': shots}]}])
        controller = page._ensure_denoise_controller()
        finish_original = ui.DenoiseController._finish

        def finish(worker, result):
            report['clips'].append({'request': str(worker.request_path), 'result': result})
            (directory / 'acceptance.json').write_text(json.dumps(report, indent=2))
            finish_original(worker, result)

        def choose():
            dialog = fixture.app.activeModalWidget()
            if isinstance(dialog, QMessageBox):
                dialog.accept()
            elif isinstance(dialog, QDialog):
                form = dialog.findChild(DeadlineOptions)
                if form is not None:
                    destination = next(c for c in dialog.findChildren(QComboBox)
                                       if c.accessibleName() == 'Denoise destination')
                    destination.setCurrentText('Deadline')
                    form.allow_local.setChecked(False)
                    if form.repository is None:
                        if not getattr(form, '_acceptance_refresh_started', False):
                            form._acceptance_refresh_started = True
                            form.load_queues()
                        elif form.process.state() == QProcess.ProcessState.NotRunning:
                            report["queue_error"] = form.status.text()
                            dialog.reject()
                        return
                    dialog.grab().save(str(directory / 'dialog.png'))
                    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).click()

        timer = QTimer()
        timer.timeout.connect(choose)
        timer.start(100)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(controller.cancel)
        timeout.start(1200000)
        controller.idle.connect(fixture.app.quit)
        with mock.patch('deadline_ui.get_settings_manager', return_value=fixture.settings), \
                mock.patch.object(ui, 'get_settings_manager', return_value=fixture.settings), \
                mock.patch.object(ui.DenoiseController, '_finish', finish), \
                mock.patch.object(page, '_request_api_refresh'):
            page.btn_denoise.click()
            if not controller.busy:
                raise RuntimeError(report.get("queue_error", "Dashboard batch did not start"))
            fixture.app.exec()
        timer.stop()
        timeout.stop()
        report['batch_result'] = controller.last_batch_result
        (directory / 'acceptance.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report['batch_result']), flush=True)
        assert len(report['batch_result']['submitted']) == 4 and not report['batch_result']['failed']
    finally:
        fixture.tearDown()
        fixture.doCleanups()
    return directory / 'acceptance.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--nuke', required=True)
    parser.add_argument('--workers', nargs='+', required=True)
    parser.add_argument('--pool', default='none')
    args = parser.parse_args()
    acceptance(args.report, args.nuke, args.workers, args.pool)
