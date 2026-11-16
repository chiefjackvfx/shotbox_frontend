"""Isolated Nuke decoding for camera media without a PyAV decoder."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
from tempfile import TemporaryDirectory

from app_cache import cache_directory, temporary_cache_task


@temporary_cache_task
def extract_nuke_thumbnail(path, frame_index, input_colourspace, check_cancelled):
    from nuke_headless_tasks import find_nuke_executable
    from settings import get_settings_manager
    from thumbnail_updates import ThumbnailUpdateCancelled
    from thumbnail_colour import thumbnail_display_processor
    from review_media import EXRSequenceReader, display_image

    preferred = get_settings_manager().get('nuke_exe_path', '')
    executable = find_nuke_executable([preferred] if preferred else None)
    if not executable:
        raise ValueError('This camera codec requires Nuke. Set its executable in Settings.')
    with TemporaryDirectory(prefix='thumbnail-nuke-', dir=cache_directory('temp')) as directory:
        directory = Path(directory)
        output = directory / 'frame.exr'
        request = directory / 'request.json'
        request.write_text(json.dumps(dict(source=str(path), output=str(output),
                                          frame_index=frame_index, input_colourspace=input_colourspace)), encoding='utf-8')
        environment = os.environ.copy()
        for key in ('NUKE_TEMP_DIR', 'NUKE_DISK_CACHE', 'TMPDIR', 'TEMP', 'TMP', 'XDG_CACHE_HOME'):
            environment[key] = str(directory)
        if check_cancelled():
            raise ThumbnailUpdateCancelled()
        with subprocess.Popen([executable, '-t', str(Path(__file__).resolve()), str(request)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                              env=environment) as process:
            deadline = time.monotonic() + 120
            try:
                while True:
                    if check_cancelled():
                        raise ThumbnailUpdateCancelled()
                    if time.monotonic() > deadline:
                        raise ValueError('Nuke thumbnail decoding timed out.')
                    try:
                        log, _ = process.communicate(timeout=0.15)
                        break
                    except subprocess.TimeoutExpired:
                        continue
                if process.returncode or not output.is_file():
                    raise ValueError('Nuke thumbnail decoding failed: ' + log[-2000:])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
        if check_cancelled():
            raise ThumbnailUpdateCancelled()
        pixels = EXRSequenceReader.read_pixels(output)
        processor = thumbnail_display_processor(input_colourspace) if input_colourspace else None
        return display_image(pixels, processor)


def render_frame(request):
    import nuke

    read = nuke.nodes.Read(file=request['source'])
    read['localizationPolicy'].setValue('off')
    # Preserve camera-encoded RGB; the caller applies the reviewed OCIO IDT.
    read['raw'].setValue(True)
    colourspace = request.get('input_colourspace') or ''
    if read.knob('arri_colorspace') and 'LogC' in colourspace:
        target = 'LogC4' if 'LogC4' in colourspace else 'LogC3'
        choices = [value for value in read['arri_colorspace'].values() if target in value]
        if not choices:
            raise ValueError('Nuke ARRIRAW reader does not offer ' + target)
        read['arri_colorspace'].setValue(choices[0])
    first, last = int(read.firstFrame()), int(read.lastFrame())
    index = request.get('frame_index')
    frame = (first + last) // 2 if index is None else first + max(0, int(index))
    if frame > last:
        raise ValueError('Thumbnail frame lies outside the source clip.')
    read['first'].setValue(first)
    read['last'].setValue(last)
    scale = min(1.0, 1280 / read.width(), 720 / read.height())
    resize = nuke.nodes.Reformat(inputs=[read], type='scale', scale=scale)
    write = nuke.nodes.Write(inputs=[resize], file=request['output'], file_type='exr')
    write['channels'].setValue('rgb')
    write['raw'].setValue(True)
    nuke.execute(write, frame, frame)


if __name__ == '__main__':
    render_frame(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
