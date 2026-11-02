"""Prepare and submit complete MP4 movies through Deadline's Nuke plugin."""
import json
from pathlib import Path
import tempfile
import uuid

import app_cache
import deadline_client as client
from nuke_denoise_display import DenoiseCancelled
from nuke_headless_tasks import (build_preview_output_path, preview_input_exists,
                                  resolve_preview_input_colourspace)


def submission_files(directory, scene, output, prepared, options, info):
    version = client.validate_version(prepared['nuke_version'], info, options.get('platforms', ['linux']))
    first, last = prepared['first'], prepared['last']
    if last < first:
        raise ValueError('Invalid MP4 frame range.')
    job, plugin = Path(directory) / 'job-info.job', Path(directory) / 'plugin-info.job'
    values = {'Plugin': 'Nuke', 'Name': output.stem, 'BatchName': options['batch_name'],
              'Frames': f'{first}-{last}', 'ChunkSize': last-first+1,
              'ConcurrentTasks': options.get('concurrent_tasks', 1),
              'LimitConcurrentTasksToNumberOfCpus': True,
              'Pool': options.get('pool', 'none'), 'Group': options.get('group', 'none'),
              'Priority': options.get('priority', 50),
              'OutputDirectory0': output.parent.as_posix(), 'OutputFilename0': output.name,
              'ExtraInfoKeyValue0': 'ShotboxSubmission=' + options['submission_id']}
    if options.get('machine_list'):
        values['Whitelist'] = options['machine_list']
    client.write_info(job, values)
    client.write_info(plugin, {'Version': version, 'NukeX': False, 'BatchMode': False,
                              'WriteNode': 'WriteCompMP4', 'ContinueOnError': False,
                              'RenderMode': 'Render Full Resolution', 'UseGpu': False})
    return [str(job), str(plugin), str(scene)]


@app_cache.temporary_cache_task
def submit_preview(task, generator, options, files_io, *, quality, overwrite, cancelled=lambda: False):
    """Return a queued ID, skipped result, or an actionable failure to the UI."""
    if cancelled():
        raise DenoiseCancelled('Cancelled before MP4 submission.')
    shot = task['shot']
    source = files_io.convert_path(task['source_path'])
    shot_dir = files_io.convert_path(shot['base_path'])
    if not preview_input_exists(source):
        return {'stage': 'skipped', 'reason': 'Source not found'}
    output, _, exists = build_preview_output_path(shot_dir, shot['title'], version=task['version'], plate_name=task.get('plate_name'))
    if exists and not overwrite:
        return {'stage': 'skipped', 'reason': 'MP4 already exists'}
    options = dict(options, chunked=False, submission_id=uuid.uuid4().hex)
    executable = client.discover_client(options.get('client', ''))
    info = client.repository_info(executable, cancelled=cancelled)
    client.validate_destination([source, str(output)], options, info, executable, cancelled=cancelled)
    client.validate_options(options, info)
    with tempfile.TemporaryDirectory(prefix='preview-deadline-', dir=app_cache.cache_directory('temp')) as temp:
        directory = Path(temp)
        scene, result = directory / (output.stem + '.nk'), directory / 'prepared.json'
        request = directory / 'request.json'
        arguments = {'input_path': source, 'output_path': str(output), 'shot_name': shot['title'],
                     'project': task['job_name'], 'artist': 'ShotBox',
                     'colourspace': resolve_preview_input_colourspace(source_type=task['source_type'],
                         media_type=task.get('media_type'), input_path=source,
                         requested_colourspace=shot.get('colourspace', 'sRGB')),
                     'fps': shot.get('fps', 25), 'quality': quality}
        request.write_text(json.dumps({'arguments': arguments, 'scene': str(scene), 'result': str(result)}), encoding='utf-8')
        client.command(generator.nuke_exe, ['-t', str(Path(__file__).with_name('preview_deadline_prepare.py')), str(request)],
                       cancelled=cancelled, timeout=300)
        prepared = json.loads(result.read_text(encoding='utf-8'))
        files = submission_files(directory, scene, output, prepared, options, info)
        if cancelled():
            raise DenoiseCancelled('Cancelled before MP4 submission.')
        try:
            job_id = client.parse_job_id(client.command(executable, files, cancelled=cancelled))
        except client.CommandInterrupted as exc:
            return {'stage': 'unknown', 'error': f'{exc} Submission reference: {options["submission_id"]}'}
        return {'stage': 'submitted', 'job_id': job_id}


def submit_batch(parent, tasks, generator, options, files_io, *, quality, overwrite):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QMessageBox, QProgressDialog
    options = dict(options, batch_name='Shotbox MP4s — ' + uuid.uuid4().hex[:8])
    progress = QProgressDialog('Preparing MP4 jobs…', 'Cancel', 0, len(tasks), parent)
    progress.setWindowTitle('Batch MP4s — Deadline')
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.show()
    submitted, errors, skipped = [], [], 0
    def cancelled():
        QApplication.processEvents()
        return progress.wasCanceled()
    parent.set_auto_refresh_paused(True, reason='previews')
    try:
        for index, task in enumerate(tasks):
            if cancelled():
                break
            progress.setLabelText(f"Preparing {index+1} of {len(tasks)}: {task['shot'].get('title', '')}\n{Path(task['source_path']).name}")
            try:
                result = submit_preview(task, generator, options, files_io, quality=quality,
                                        overwrite=overwrite, cancelled=cancelled)
                if result['stage'] == 'submitted':
                    submitted.append(result['job_id'])
                elif result['stage'] == 'skipped':
                    skipped += 1
                else:
                    errors.append(result['error'])
                    break  # An uncertain submission must be reconciled before retrying.
            except DenoiseCancelled:
                break
            except Exception as exc:
                errors.append(f"{task['shot'].get('title', '')}: {exc}")
            progress.setValue(index+1)
    finally:
        was_cancelled = progress.wasCanceled()
        progress.close()
        parent.set_auto_refresh_paused(False, reason='previews')
    summary = ('Cancelled.' if was_cancelled else 'Submission finished.')
    summary += f'\n\nSubmitted: {len(submitted)} (queued, not rendered)\nSkipped: {skipped}\nErrors: {len(errors)}'
    if submitted:
        summary += '\n\n' + '\n'.join(submitted) + '\nManage these jobs in Deadline Monitor.'
    if errors:
        summary += '\n\n' + '\n\n'.join(errors)
    QMessageBox.information(parent, 'Batch MP4s', summary)
