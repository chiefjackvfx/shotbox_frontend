"""Render existing saved profiles on explicit candidate workers; never qualifies them automatically."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_cache
import deadline_client as client
from denoise_template import output_directory


@app_cache.temporary_cache_task
def submit(report_path, nuke, workers, pool, smoke=False):
    previous = json.loads(report_path.read_text())
    if len(previous['clips']) != 4:
        raise ValueError('Expected the existing four-clip acceptance fixture.')
    executable = client.discover_client()
    info = client.repository_info(executable)
    directory = Path(tempfile.mkdtemp(prefix='deadline-mixed-', dir=app_cache.cache_directory('temp')))
    print(directory, flush=True)
    report = {'staging': previous['staging'], 'smoke': smoke, 'clips': [], 'qualification': 'pending'}
    batch = 'Shotbox mixed qualification ' + uuid.uuid4().hex[:8]
    cases = []
    for worker in workers:
        for clip in previous['clips'][:1] if smoke else previous['clips']:
            old_path = Path(clip['request'])
            old = json.loads(old_path.read_text())
            old_receipt = json.loads(Path(clip['result']['receipt']).read_text())
            job = directory / str(len(cases) + 1)
            job.mkdir()
            options = {**old['deadline'], 'pool': pool, 'group': 'none', 'machine_list': worker,
                       'qualified_workers': workers, 'allow_local': False, 'submission_id': uuid.uuid4().hex,
                       'batch_name': batch, 'chunked': True, 'chunk_size': 10, 'concurrent_tasks': 1}
            client.validate_options(options, info)
            client.validate_destination([old['source'], old['output_pattern']], options, info, executable)
            client.validate_version(old_receipt['nuke_version'], info, options['platforms'])
            output = output_directory({'shot_dir': old['deadline']['shot_dir'],
                                       'shot_name': Path(old['deadline']['shot_dir']).name, 'mode': 'new'})
            request = {**old, 'output_pattern': (output / (output.name + '_####.exr')).as_posix(),
                       'nuke': nuke, 'deadline': options}
            request_path = job / 'request.json'
            request_path.write_text(json.dumps(request, indent=2))
            shutil.copy2(old_path.parent / 'media.json', job / 'media.json')
            receipt = {'first': old_receipt['first'], 'last': old_receipt['first'] if smoke else old_receipt['last'],
                       'nuke_version': old_receipt['nuke_version'], 'chunk_size': 10, 'status': 'prepared'}
            receipt_path = job / 'receipt.json'
            receipt_path.write_text(json.dumps(receipt))
            entry = {'request': str(request_path), 'worker': worker,
                     'result': {'receipt': str(receipt_path), 'stage': 'prepared'}}
            report['clips'].append(entry)
            cases.append({'source_script': str(old_path.parent / 'profiled.nk'), 'script': str(job / 'profiled.nk'),
                          'output': request['output_pattern'],
                          'guard': client.render_guard(receipt['nuke_version'], options['platforms'])})
    cases_path = directory / 'cases.json'
    cases_path.write_text(json.dumps(cases))
    script = directory / 'prepare.py'
    script.write_text('import nuke, json\n'
        + 'cases=json.load(open(' + repr(str(cases_path)) + '))\n'
        + "for case in cases:\n nuke.scriptClear(ignoreUnsavedChanges=True)\n nuke.scriptOpen(case['source_script'])\n"
          " write=nuke.toNode('Write1')\n write['file'].setValue(case['output'])\n"
          " write['beforeRender'].setValue(case['guard'])\n nuke.scriptSaveAs(case['script'], overwrite=True)\n")
    report_path = directory / 'acceptance.json'
    report_path.write_text(json.dumps(report, indent=2))
    with (directory / 'prepare.log').open('w') as log:
        subprocess.run([nuke, '--nukex', '-t', str(script)], stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=120)
    for entry in report['clips']:
        request_path = Path(entry['request'])
        request = json.loads(request_path.read_text())
        receipt_path = Path(entry['result']['receipt'])
        receipt = json.loads(receipt_path.read_text())
        args = client.submission_files(request_path.parent, request, receipt, info)
        with Path(args[0]).open('a') as stream:
            stream.write('OverrideJobFailureDetection=True\nFailureDetectionJobErrors=1\n')
        entry['result']['stage'] = 'submission_unknown'
        report_path.write_text(json.dumps(report, indent=2))
        response = client.command(executable, args, timeout=120)
        job_id = client.parse_job_id(response)
        entry['result'].update(stage='submitted', job_id=job_id)
        receipt.update(status='submitted', job_id=job_id)
        receipt_path.write_text(json.dumps(receipt, indent=2))
        report_path.write_text(json.dumps(report, indent=2))
        print(entry['worker'], job_id, flush=True)
    return report_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--nuke', required=True)
    parser.add_argument('--workers', nargs='+', required=True)
    parser.add_argument('--pool', default='none')
    parser.add_argument('--smoke', action='store_true', help='Render one frame of one clip per candidate first.')
    args = parser.parse_args()
    submit(args.report, args.nuke, args.workers, args.pool, args.smoke)
