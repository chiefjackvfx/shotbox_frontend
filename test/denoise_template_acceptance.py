"""Native template acceptance. All generated artifacts stay under .cache/temp."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock

FRONTEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRONTEND))
import app_cache  # noqa: E402
from denoise_template import TEMPLATE, digest, create_template_request  # noqa: E402


def native(directory, mode):
    import nuke
    from denoise_template import prepare
    data = json.loads((directory / 'fixture.json').read_text())
    if mode == 'prepare':
        results = []
        for case in data['cases']:
            nuke.scriptClear(ignoreUnsavedChanges=True)
            nuke.scriptOpen(str(TEMPLATE))
            ocio = nuke.root()['OCIO_config'].toScript()
            profile = next(n for n in nuke.allNodes() if n.Class() == 'OFXcom.absoft.NeatVideo6_v6')['ClipParams'].value()
            request = dict(case, output_pattern=(directory / 'unused.####.exr').as_posix())
            result = prepare(nuke, request)
            assert (result['width'], result['height']) == tuple(case['expected']), result
            assert int(nuke.root()['last_frame'].value()) == 1000 + case['frames'], result
            assert abs(result['pixel_aspect'] - case.get('aspect', 1)) < .001, result
            assert abs(result['fps'] - case['fps']) < .002, result
            assert nuke.toNode('Reformat1')['disable'].value() == (result['source_width'] <= 4096)
            resized = nuke.toNode('Reformat1')
            assert (resized.width(), resized.height()) == tuple(case['expected'])
            assert abs(resized.pixelAspect() - case.get('aspect', 1)) < .001
            assert nuke.root()['OCIO_config'].toScript() == ocio
            assert next(n for n in nuke.allNodes() if n.Class() == 'OFXcom.absoft.NeatVideo6_v6')['ClipParams'].value() == profile
            assert nuke.toNode('Write1')['colorspace'].value() == 'scene_linear'
            # Force media and colour evaluation, without writing any output.
            read = nuke.toNode('Read1')
            if case.get('colourspace'):
                assert read['colorspace'].value() == case['colourspace'], result
            sample = read.sample('red', read.width()/2, read.height()/2)
            assert sample >= 0
            if case['source'].endswith('.exr'):
                assert abs(sample - .18) < .001, sample
            results.append(result)
        (directory / 'prepare.json').write_text(json.dumps(results, indent=2))


def reopen_cached(directory):
    import nuke
    data = json.loads((directory / 'fixture.json').read_text())
    cached = Path(data['cached_script'])
    nuke.scriptOpen(str(TEMPLATE))
    original = next(n for n in nuke.allNodes() if n.Class() == 'OFXcom.absoft.NeatVideo6_v6')['ClipParams'].value()
    nuke.scriptClear(ignoreUnsavedChanges=True)
    nuke.scriptOpen(str(cached))
    noise = next(n for n in nuke.allNodes() if n.Class() == 'OFXcom.absoft.NeatVideo6_v6')
    assert noise['ClipParams'].value() != original
    write = nuke.toNode('Write1')
    write['file'].setValue((directory / 'reopened.####.exr').as_posix())
    nuke.execute(write, 1001, 1001)
    (directory / 'reopen.json').write_text(json.dumps({'cached_profile_reopened': True, 'rendered': True}))


def fixtures(directory):
    import numpy as np
    import OpenEXR
    from PIL import Image
    cases = []
    for width, height, aspect in ((512, 384, 1), (4096, 128, 1), (5120, 160, 2), (256, 4200, 1)):
        folder = directory / f'size {width}'
        folder.mkdir()
        path = folder / 'still.exr'
        pixels = np.full((height, width, 3), .18, dtype=np.float16)
        with OpenEXR.File({'pixelAspectRatio': float(aspect)}, {'RGB': pixels}) as image:
            image.write(str(path))
        expected = [4096, round(height * 4096 / width)] if width > 4096 else [width, height]
        cases.append(dict(source=str(path), expected=expected, frames=1, fps=24, aspect=aspect))
    for frame in range(42, 45):
        pixels = np.clip(100 + np.random.default_rng(frame).normal(0, 9, (512, 512, 3)), 0, 255).astype('uint8')
        Image.fromarray(pixels).save(directory / f'plate.{frame:04d}.png')
    for name in ('plate.####.png', 'plate.0042.png'):
        cases.append(dict(source=str(directory / name), expected=[512, 512], frames=3, fps=30))
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg is required for native movie codec acceptance.')
    for codec, suffix, fps in (('libx264', 'mp4', '24000/1001'), ('prores_ks', 'mov', '25')):
        path = directory / f'movie with spaces.{suffix}'
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-framerate', fps, '-start_number', '42', '-i',
                        str(directory / 'plate.%04d.png'), '-frames:v', '3', '-c:v', codec,
                        '-pix_fmt', 'yuv420p' if suffix == 'mp4' else 'yuv422p10le', str(path)], check=True)
        cases.append(dict(source=str(path), expected=[512, 512], frames=3, fps=24000/1001 if suffix == 'mp4' else 25))
        if suffix == 'mov':
            cases.append(dict(cases[-1], colourspace='ARRI LogC3 (EI800)'))
    return cases


def batch(executable, directory, source):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication, QMainWindow
    import denoise_controller as ui
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    window = QMainWindow()
    controller = ui.DenoiseController(window)
    bad = directory / 'unreadable.clip'
    bad.write_bytes(b'not readable media')
    tasks = [{'shot': {'id': index, 'title': f'clip {index}'}, 'media':
              {'source': str(path), 'shot_dir': str(directory / 'multiple plates'), 'shot_name': 'shot',
               'plate': f'plate_{index:02d}', 'mode': 'new'}} for index, path in enumerate((source, bad, source), 1)]
    timer = QTimer()
    started = time.monotonic()
    def poll():
        if not controller.busy:
            app.quit()
        elif time.monotonic() - started > 400:
            controller.cancel()
    timer.timeout.connect(poll)
    timer.start(25)
    try:
        with mock.patch.object(ui, 'find_nuke_executable', return_value=executable), mock.patch.object(ui.QMessageBox, 'information'):
            assert controller.start_batch(tasks)
            assert not controller.start_batch(tasks)
            app.exec()
        result = controller.last_batch_result
        assert result['completed'] == 2 and result['failed'] == 1 and not result['cancelled'], result
        assert controller.process is None and controller._lease is None
        assert len(list((directory / 'multiple plates').rglob('*.exr'))) == 6
        assert not list((directory / 'multiple plates').rglob('*.nk'))
        return result
    finally:
        timer.stop()
        window.deleteLater()


@app_cache.temporary_cache_task
def acceptance(executable, *, expected_version='17.1v2'):
    from test.denoise_test_support import host_command, gui_job
    import numpy as np
    import OpenEXR
    from nuke_denoise import run
    directory = Path(tempfile.mkdtemp(prefix='denoise-template-verification-', dir=app_cache.cache_directory('temp')))
    print('TEMPLATE_ACCEPTANCE ' + str(directory), flush=True)
    environment = dict(os.environ)
    for name in ('NUKE_TEMP_DIR', 'NUKE_DISK_CACHE', 'TMPDIR', 'XDG_CACHE_HOME'):
        path = directory / name.lower()
        path.mkdir()
        environment[name] = str(path)
    environment['TEMP'] = environment['TMP'] = environment['TMPDIR']
    host_command(executable, 'preflight', directory, environment, expected_version=expected_version)
    source = FRONTEND / 'test/noise_test.exr'
    before = {str(path): digest(path) for path in (source, TEMPLATE)}
    report = {'directory': str(directory), 'target_nuke': expected_version, 'success': False}
    try:
        cases = fixtures(directory)
        (directory / 'fixture.json').write_text(json.dumps({'cases': cases}))
        host_command(executable, 'prepare', directory, environment, script_path=__file__)
        report['media'] = json.loads((directory / 'prepare.json').read_text())
        worker = directory / 'worker'
        worker.mkdir()
        media = dict(source=str(source), shot_dir=str(directory / 'shot with spaces'), shot_name='noise_test')
        request = create_template_request(worker, media, executable)
        result = run(request)
        assert result['stage'] == 'complete', result
        report['single'] = result
        report['neat'] = json.loads((worker / 'neat-version.json').read_text())
        output = Path(json.loads(request.read_text())['output_pattern']).parent
        outputs = list(output.glob('*.exr'))
        assert len(outputs) == 1 and outputs[0].name.endswith('_1001.exr')
        assert not list(output.glob('*.nk')), 'Denoise must not save a generated Nuke script'
        with OpenEXR.File(str(outputs[0])) as image:
            for channel in image.channels().values():
                assert channel.pixels.shape[:2] == (2160, 4096) and np.isfinite(channel.pixels).all()
        report['exr_only_output'] = True
        cached = worker / 'profiled.nk'
        assert cached.is_file()
        (directory / 'fixture.json').write_text(json.dumps({'cached_script': str(cached)}))
        host_command(executable, 'reopen', directory, environment, script_path=__file__)
        with OpenEXR.File(str(directory / 'reopened.1001.exr')) as image:
            assert all(channel.pixels.shape[:2] == (2160, 4096) and np.isfinite(channel.pixels).all()
                       for channel in image.channels().values())
        report['cached_script'] = str(cached)
        report['reopen'] = json.loads((directory / 'reopen.json').read_text())
        # Small media for lifecycle and queue tests, using the same template worker.
        media['source'] = str(directory / 'plate.####.png')
        media['shot_dir'] = str(directory / 'lifecycle shot')
        (directory / 'fixture.json').write_text(json.dumps({'media': media}))
        for name, stage, options in [('cancel_before_profile', 'profiling', {}), ('cancel_during_render', 'rendering', {}),
                                     ('shutdown', 'profiling', {'shutdown': True}), ('supervisor_failure', 'profiling', {'kill': True})]:
            report[name] = gui_job(directory, executable, stage, **options)
        report['batch'] = batch(executable, directory, directory / 'plate.####.png')
        assert all(digest(path) == value for path, value in before.items())
        report['source_and_template_unchanged'] = True
        report['success'] = True
        return report
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        (directory / 'verification.json').write_text(json.dumps(report, indent=2))
        print('Report: ' + str(directory / 'verification.json'), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nuke')
    parser.add_argument('--mode', choices=('prepare', 'reopen'))
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    if args.mode == 'reopen':
        reopen_cached(args.directory)
    elif args.mode:
        native(args.directory, args.mode)
    elif args.nuke:
        acceptance(args.nuke)
    else:
        parser.error('--nuke is required')
