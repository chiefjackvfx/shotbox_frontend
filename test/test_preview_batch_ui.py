import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
import tempfile
from pathlib import Path
import app_cache
from unittest import mock
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QListWidget, QPushButton
from PyQt6.QtCore import Qt
import preview_batch_ui as module


class PreviewBatchUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tasks(self):
        return [{'shot': {'title': 'SH010'}, 'source_type': 'original_clip',
                 'source_path': f'/clip{i}.mov', 'plate_name': f'plate_0{i}', 'version': 1} for i in (1, 2)]

    def test_select_all_none_and_local_subset(self):
        tasks = self.tasks()
        def execute(dialog):
            clips = dialog.findChild(QListWidget)
            self.assertEqual(clips.count(), 2)
            start = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
            next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Select none').click()
            self.assertFalse(start.isEnabled())
            next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Select all').click()
            self.assertTrue(start.isEnabled())
            clips.item(0).setCheckState(Qt.CheckState.Unchecked)
            self.assertEqual(start.text(), 'Render locally')
            return QDialog.DialogCode.Accepted
        with mock.patch.object(QDialog, 'exec', execute):
            self.assertEqual(module.choose_preview_batch(None, tasks), {'tasks': tasks[1:], 'deadline': None, 'overwrite': False})

    def test_deadline_selection_and_cancel(self):
        tasks = self.tasks()
        def execute(dialog):
            destination = next(c for c in dialog.findChildren(QComboBox) if c.accessibleName() == 'MP4 render destination')
            destination.setCurrentIndex(1)
            self.assertEqual(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok).text(), 'Add to Deadline')
            return QDialog.DialogCode.Accepted
        with mock.patch.object(QDialog, 'exec', execute), \
                mock.patch.object(module.DeadlineOptions, 'options', return_value={'pool': 'farm'}) as options:
            self.assertEqual(module.choose_preview_batch(None, tasks)['deadline'], {'pool': 'farm'})
            options.assert_called_once_with(persist_chunking=False)
        with mock.patch.object(QDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            self.assertIsNone(module.choose_preview_batch(None, tasks))

    def test_defaults_match_plate_version_and_legacy_preview_files(self):
        with tempfile.TemporaryDirectory(dir=app_cache.cache_directory('temp')) as directory:
            root = Path(directory)
            def shot(title, multiple=False):
                data = {'title': title, 'base_path': str(root / title)}
                if multiple:
                    data['original_clips'] = ['/one.mov', '/two.mov']
                return data
            def task(data, version=1, plate=None, source_type='original_clip'):
                return {'shot': data, 'source_path': '/clip.mov', 'source_type': source_type,
                        'plate_name': plate, 'version': version}
            def existing(title, name, folder=False):
                parent = root / title / 'renders/precomp/previews'
                parent.mkdir(parents=True, exist_ok=True)
                (parent / name).mkdir() if folder else (parent / name).touch()
            multi = shot('SH010', True)
            existing('SH010', 'SH010_plate_01_v001.mp4')
            existing('SH020', 'SH020_v001_preview.mp4')
            existing('SH030', 'SH030_plate_01_v001.mp4', folder=True)
            tasks = [task(multi, plate='plate_01'), task(multi, plate='plate_02'),
                     task(multi, version=2, plate='plate_01'), task(multi, source_type='render'),
                     task(shot('SH020'), plate='plate_01'), task(shot('SH030'), plate='plate_01'),
                     task(shot('SH040'), plate='plate_01')]
            def execute(dialog):
                clips = dialog.findChild(QListWidget)
                self.assertEqual([clips.item(i).checkState() == Qt.CheckState.Checked for i in range(7)],
                                 [False, True, True, True, False, True, True])
                self.assertIn('Preview exists', clips.item(0).toolTip())
                # A manual tick can still include an existing preview.
                clips.item(0).setCheckState(Qt.CheckState.Checked)
                return QDialog.DialogCode.Accepted
            with mock.patch.object(QDialog, 'exec', execute):
                result = module.choose_preview_batch(None, tasks, overwrite=True)
            self.assertEqual(result['tasks'], [tasks[i] for i in (0, 1, 2, 3, 5, 6)])

    def test_all_existing_previews_disable_start_until_manually_selected(self):
        with tempfile.TemporaryDirectory(dir=app_cache.cache_directory('temp')) as directory:
            preview = Path(directory) / 'renders/precomp/previews/SH010_plate_01_v001.mp4'
            preview.parent.mkdir(parents=True)
            preview.touch()
            tasks = self.tasks()[:1]
            tasks[0]['shot']['base_path'] = directory
            def execute(dialog):
                start = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Ok)
                self.assertFalse(start.isEnabled())
                next(b for b in dialog.findChildren(QPushButton) if b.text() == 'Select all').click()
                self.assertTrue(start.isEnabled())
                return QDialog.DialogCode.Accepted
            with mock.patch.object(QDialog, 'exec', execute):
                self.assertEqual(module.choose_preview_batch(None, tasks)['tasks'], tasks)

    def test_overwrite_is_available_for_deadline_and_defaults_to_current_setting(self):
        def execute(dialog):
            overwrite = next(b for b in dialog.findChildren(QCheckBox) if b.text() == 'Overwrite existing MP4s')
            self.assertFalse(overwrite.isChecked())
            destination = next(c for c in dialog.findChildren(QComboBox) if c.accessibleName() == 'MP4 render destination')
            destination.setCurrentIndex(1)
            self.assertTrue(overwrite.isEnabled())
            overwrite.setChecked(True)
            return QDialog.DialogCode.Accepted
        with mock.patch.object(QDialog, 'exec', execute), \
                mock.patch.object(module.DeadlineOptions, 'options', return_value={'pool': 'farm'}):
            result = module.choose_preview_batch(None, self.tasks(), overwrite=False)
        self.assertTrue(result['overwrite'])
        self.assertEqual(result['deadline'], {'pool': 'farm'})
