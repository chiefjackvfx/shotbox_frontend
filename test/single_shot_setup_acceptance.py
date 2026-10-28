"""Run with frontend Python: test/single_shot_setup_acceptance.py /path/to/Nuke."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt6.QtWidgets import QApplication
import import_xml_v2 as module
from app_cache import cache_directory, temporary_cache_task
from test_single_shot_media import SingleShotMediaTests


@temporary_cache_task
def main(nuke):
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory(dir=cache_directory('temp'), prefix='single-shot-acceptance-') as directory:
        root = Path(directory)
        frames = SingleShotMediaTests().frames(root)
        page = module.XMLImportPage.__new__(module.XMLImportPage)
        shot = page._build_parsed_shot_from_clip_files('sho010', [str(frames[1])])
        shot.notes = 'Remove the marker.'
        folder = page._create_single_shot_folder_structure(str(root / 'timeline'), shot.name, shot)
        shot.folder_path = folder
        thumbnail = str(Path(folder) / 'thumb.jpeg')
        assert module.ThumbnailGenerator.generate(shot.primary_clip.filepath, thumbnail,
                                                  input_colourspace='ACES - ACEScg') == thumbnail
        generator = module.NukeScriptGenerator(nuke_path=nuke)
        script = generator.create_nuke_script_with_project_name(shot, folder, '', 'Acceptance')
        verify = root / 'verify.py'
        verify.write_text('''import nuke, sys
nuke.scriptOpen(sys.argv[1])
read, retime = nuke.toNode('Read1'), nuke.toNode('Retime1')
assert any(token in read['file'].value() for token in ('####.exr', '%04d.exr')), read['file'].value()
assert read['first'].value() == 1041
assert read['last'].value() == 1043
assert retime.Class() == 'Retime'
assert retime['input.first'].value() == 1041
assert retime['output.first'].value() == 1001
assert nuke.root()['last_frame'].value() == 1003
assert nuke.toNode('ReadDN1')['last'].value() == 1003
assert 'Remove the marker.' in nuke.toNode('BackdropINFO')['label'].value()
nuke.frame(1001)
assert abs(retime.sample('red', 2, 2) - 0.18) < 0.005
print('SINGLE_SHOT_EXR_ACCEPTANCE_OK')
''')
        result = subprocess.run([nuke, '-t', str(verify), script], capture_output=True, text=True, timeout=90)
        if result.returncode or 'SINGLE_SHOT_EXR_ACCEPTANCE_OK' not in result.stdout:
            raise RuntimeError(result.stdout + result.stderr)
        print('SINGLE_SHOT_EXR_ACCEPTANCE_OK: thumbnail, full copy, native frames, duration, notes, Nuke pixels')


if __name__ == '__main__':
    main(sys.argv[1])
