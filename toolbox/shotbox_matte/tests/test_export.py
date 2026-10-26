import errno
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from shotbox_matte import export
from shotbox_matte.export import ExportSession, LEGACY_SIDECARS, MANIFEST, frame_filename, job_storage_root, output_target, preview_output


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name) / "bam190_DN_v001"
        self.source.mkdir()
        self.stage_parent = self.source.parent
        self.job_environment = patch.dict(os.environ, {"SHOTBOX_JOB_DIR": str(Path(self.temp.name) / "jobs"),
                                                      "SHOTBOX_TEMP_DIR": ""})
        self.job_environment.start()

    def tearDown(self):
        self.job_environment.stop()
        self.temp.cleanup()

    def test_suffix_precedes_version_and_preview_has_no_side_effect(self):
        target = preview_output(self.source, "_alpha")
        self.assertEqual(target.name, "bam190_DN_alpha_v001")
        self.assertFalse(target.exists())
        target.mkdir()
        target.with_name("bam190_DN_alpha_v007").mkdir()
        self.assertEqual(preview_output(self.source, "alpha").name, "bam190_DN_alpha_v008")
        self.assertEqual(preview_output(self.source, "_alpha", "overwrite"), target)
        self.assertEqual(frame_filename(target, 1001, 6), "bam190_DN_alpha_v001.001001.exr")

    def test_concurrent_versions_are_reserved_uniquely(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            sessions = list(pool.map(lambda _: ExportSession.reserve(self.source, "_roto", "version-up"), range(8)))
        self.assertEqual(len({s.output_dir for s in sessions}), 8)
        self.assertEqual(len({s.job_dir for s in sessions}), 8)
        for session in sessions:
            self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "running")
            self.assertFalse(session.output_dir.exists())
            self.assertEqual(session.stage_dir.parent, self.stage_parent)
            session.close()

    def test_overwrite_preserves_old_until_commit_then_removes_only_exact_stale(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        first = frame_filename(output, 1001)
        stale = frame_filename(output, 1002)
        (output / first).write_text("old")
        (output / stale).write_text("tail")
        unrelated = ("notes.txt", "other.1002.exr", output.name + ".backup.exr")
        for name in unrelated:
            (output / name).write_text("keep")
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        (session.stage_dir / first).write_text("new")
        self.assertEqual((output / first).read_text(), "old")
        self.assertTrue((output / stale).exists())
        session.commit([first])
        self.assertEqual((output / first).read_text(), "new")
        self.assertFalse((output / stale).exists())
        self.assertTrue(all((output / name).exists() for name in unrelated))
        self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "complete")
        self.assertFalse((output / MANIFEST).exists())
        self.assertFalse(session.lock_dir.exists())

    def test_failed_commit_rolls_back_overwritten_frames_and_old_manifest(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        names = [frame_filename(output, n) for n in (1001, 1002)]
        for name in names:
            (output / name).write_text("old")
        old_manifest = '{"status":"complete","old":true}'
        (output / MANIFEST).write_text(old_manifest)
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        for name in names:
            (session.stage_dir / name).write_text("new")
        original = Path.replace

        def fail_second(path, target):
            if path.parent == session.stage_dir and path.name == names[1]:
                raise OSError("simulated disk failure")
            return original(path, target)

        with patch.object(Path, "replace", fail_second):
            with self.assertRaisesRegex(OSError, "disk failure"):
                session.commit(names)
        session.fail(RuntimeError("simulated disk failure"))
        self.assertTrue(all((output / name).read_text() == "old" for name in names))
        self.assertEqual((output / MANIFEST).read_text(), old_manifest)
        self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "failed")
        self.assertEqual((session.stage_dir / names[0]).read_text(), "new")

    def test_simultaneous_overwrites_rejected(self):
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        try:
            with self.assertRaisesRegex(RuntimeError, "Another overwrite"):
                ExportSession.reserve(self.source, "_roto", "overwrite")
        finally:
            session.close()

    def test_overwrite_rejects_active_version_up(self):
        session = ExportSession.reserve(self.source, "_roto", "version-up")
        try:
            with self.assertRaisesRegex(RuntimeError, "Another overwrite"):
                ExportSession.reserve(self.source, "_roto", "overwrite")
            self.assertFalse(json.loads(session.manifest_path.read_text())["output_is_provisional"])
        finally:
            session.close()

    def test_version_up_skips_broken_symlink(self):
        target = output_target(self.source, "_roto")
        try:
            target.symlink_to(target.with_name("missing"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation is unavailable on this platform")
        session = ExportSession.reserve(self.source, "_roto", "version-up")
        try:
            self.assertEqual(session.output_dir.name, "bam190_DN_roto_v002")
            self.assertTrue(target.is_symlink())
        finally:
            session.close()

    def test_invalid_suffix(self):
        for suffix in ("", "_", "../bad", "_roto ", "_x:y"):
            with self.assertRaises(ValueError):
                output_target(self.source, suffix)

    def test_job_storage_root_precedence(self):
        portable = Path(self.temp.name) / "portable"
        explicit = Path(self.temp.name) / "explicit"
        with patch.dict(os.environ, {"SHOTBOX_PORTABLE_DIR": str(portable), "SHOTBOX_JOB_DIR": str(explicit)}):
            self.assertEqual(job_storage_root(), explicit)
            os.environ.pop("SHOTBOX_JOB_DIR")
            self.assertEqual(job_storage_root(), portable / "logs" / "jobs")
            os.environ.pop("SHOTBOX_PORTABLE_DIR")
            self.assertEqual(job_storage_root(), Path(export.__file__).resolve().parents[1] / "logs" / "jobs")

    def test_clean_result_folder_and_explicit_payload_allowlist(self):
        session = ExportSession.reserve(self.source, "_roto", "version-up")
        name = frame_filename(session.output_dir, 1001)
        (session.stage_dir / name).write_text("new")
        ignored = ("debug.txt", "_shotbox_source.png", MANIFEST, frame_filename(session.output_dir, 1002))
        for extra in ignored:
            (session.stage_dir / extra).write_text("ignored")
        session.commit([name])
        self.assertEqual([p.name for p in session.output_dir.iterdir()], [name])
        self.assertFalse(session.stage_dir.exists())
        self.assertEqual(session.manifest_path.parent, session.job_dir)
        manifest = json.loads(session.manifest_path.read_text())
        self.assertEqual(manifest["frames"], [name])
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["stage_dir"], str(session.stage_dir))

    def test_new_output_is_absent_until_commit_in_both_modes(self):
        for existing in ("version-up", "overwrite"):
            with self.subTest(existing=existing):
                session = ExportSession.reserve(self.source, "_" + existing, existing)
                name = frame_filename(session.output_dir, 1001)
                self.assertFalse(session.output_dir.exists())
                (session.stage_dir / name).write_text("complete")
                self.assertFalse(session.output_dir.exists())
                session.commit([name])
                self.assertEqual({p.name: p.read_text() for p in session.output_dir.iterdir()}, {name: "complete"})

    def test_new_output_commit_failure_removes_folder_and_keeps_staged_frames(self):
        session = ExportSession.reserve(self.source, "_roto", "version-up")
        names = [frame_filename(session.output_dir, number) for number in (1001, 1002)]
        for name in names:
            (session.stage_dir / name).write_text(name)
        move = export._move_file

        def fail_second(source, destination):
            if source == session.stage_dir / names[1]:
                raise OSError("publication failed")
            move(source, destination)

        with patch.object(export, "_move_file", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "publication failed"):
                session.commit(names)
        self.assertFalse(session.output_dir.exists())
        self.assertEqual([(session.stage_dir / name).read_text() for name in names], names)
        self.assertFalse(session.lock_dir.exists())

    def test_late_destination_collision_preserves_external_folder(self):
        for existing in ("version-up", "overwrite"):
            with self.subTest(existing=existing):
                session = ExportSession.reserve(self.source, "_" + existing, existing)
                name = frame_filename(session.output_dir, 1001)
                (session.stage_dir / name).write_text("new")
                session.output_dir.mkdir()
                sentinel = session.output_dir / "artist-file.txt"
                sentinel.write_text("preserve")
                with self.assertRaises(FileExistsError):
                    session.commit([name])
                self.assertEqual(list(session.output_dir.iterdir()), [sentinel])
                self.assertEqual(sentinel.read_text(), "preserve")
                self.assertEqual((session.stage_dir / name).read_text(), "new")

    def test_new_output_manifest_failure_removes_published_frames_and_folder(self):
        session = ExportSession.reserve(self.source, "_roto", "version-up")
        name = frame_filename(session.output_dir, 1001)
        (session.stage_dir / name).write_text("new")
        original_write = export._write_json

        def fail_complete(path, payload):
            if payload["status"] == "complete":
                raise OSError("metadata write failed")
            original_write(path, payload)

        with patch.object(export, "_write_json", side_effect=fail_complete):
            with self.assertRaisesRegex(OSError, "metadata write failed"):
                session.commit([name])
        self.assertFalse(session.output_dir.exists())
        self.assertEqual((session.stage_dir / name).read_text(), "new")
        self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "failed")

    def test_cancel_and_failure_keep_partial_frames_outside_output(self):
        for cancelled in (False, True):
            with self.subTest(cancelled=cancelled):
                session = ExportSession.reserve(self.source, "_roto", "version-up")
                name = frame_filename(session.output_dir, 1001)
                (session.stage_dir / name).write_text("unfinished")
                session.fail(RuntimeError("stopped"), cancelled=cancelled)
                self.assertFalse(session.output_dir.exists())
                self.assertEqual((session.stage_dir / name).read_text(), "unfinished")
                self.assertFalse(session.stage_dir.is_relative_to(session.output_dir))
                self.assertFalse((session.stage_dir / MANIFEST).exists())
                self.assertFalse(session.lock_dir.exists())
                manifest = json.loads(session.manifest_path.read_text())
                self.assertEqual(manifest["status"], "cancelled" if cancelled else "failed")
                self.assertEqual(manifest["stage_dir"], str(session.stage_dir))

    def test_successful_overwrite_migrates_exact_legacy_sidecars(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        name = frame_filename(output, 1001)
        (output / name).write_text("old")
        for sidecar in LEGACY_SIDECARS:
            (output / sidecar).write_text("previous " + sidecar)
        unrelated = output / "_shotbox_unrelated.png"
        unrelated.write_text("keep")
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        (session.stage_dir / name).write_text("new")
        session.commit([name])
        self.assertEqual((output / name).read_text(), "new")
        self.assertEqual(unrelated.read_text(), "keep")
        for sidecar in LEGACY_SIDECARS:
            self.assertFalse((output / sidecar).exists())
            self.assertEqual((session.job_dir / "previous-export" / sidecar).read_text(), "previous " + sidecar)
        self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "complete")

    def test_complete_metadata_failure_rolls_back_frames_stale_and_legacy(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        first, stale = [frame_filename(output, n) for n in (1001, 1002)]
        originals = {first: "old", stale: "old tail", **{s: "legacy " + s for s in LEGACY_SIDECARS}}
        for name, content in originals.items():
            (output / name).write_text(content)
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        (session.stage_dir / first).write_text("new")
        original_write = export._write_json

        def fail_complete(path, payload):
            if payload["status"] == "complete":
                raise OSError("metadata write failed")
            original_write(path, payload)

        with patch("shotbox_matte.export._write_json", side_effect=fail_complete):
            with self.assertRaisesRegex(OSError, "metadata write failed"):
                session.commit([first])
        self.assertEqual({p.name: p.read_text() for p in output.iterdir()}, originals)
        self.assertEqual((session.stage_dir / first).read_text(), "new")
        self.assertFalse((session.job_dir / "previous-export").exists())
        self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "failed")

    def test_ready_metadata_failure_keeps_old_output(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        name = frame_filename(output, 1001)
        (output / name).write_text("old")
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        (session.stage_dir / name).write_text("new")
        original_write = export._write_json

        def fail_ready(path, payload):
            if payload["status"] == "ready":
                raise OSError("metadata write failed")
            original_write(path, payload)

        with patch("shotbox_matte.export._write_json", side_effect=fail_ready):
            with self.assertRaisesRegex(OSError, "metadata write failed"):
                session.commit([name])
        self.assertEqual((output / name).read_text(), "old")
        self.assertEqual((session.stage_dir / name).read_text(), "new")
        self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "failed")

    def test_reservation_metadata_failure_removes_empty_output_and_lock(self):
        with patch("shotbox_matte.export._write_json", side_effect=OSError("metadata unavailable")):
            with self.assertRaisesRegex(OSError, "metadata unavailable"):
                ExportSession.reserve(self.source, "_roto", "version-up")
        output = output_target(self.source, "_roto")
        self.assertFalse(output.exists())
        self.assertFalse(output.with_name(f".{output.name}.shotbox-lock").exists())
        self.assertFalse(export._lock_path(output).exists())
        self.assertEqual(list(output.parent.glob(".shotbox-partial-*")), [])

    def test_invalid_commit_payloads_do_not_enter_output(self):
        for invalid in ([], ["debug.png"], ["../escape.exr"], ["other.1001.exr"]):
            with self.subTest(names=invalid):
                session = ExportSession.reserve(self.source, "_roto", "version-up")
                with self.assertRaisesRegex(ValueError, "explicitly named"):
                    session.commit(invalid)
                self.assertFalse(session.output_dir.exists())
                self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "failed")

    def test_duplicate_and_missing_staged_frames_are_rejected(self):
        for duplicate in (False, True):
            session = ExportSession.reserve(self.source, "_roto", "version-up")
            name = frame_filename(session.output_dir, 1001)
            if duplicate:
                (session.stage_dir / name).write_text("new")
            with self.assertRaises(ValueError):
                session.commit([name, name] if duplicate else [name])
            self.assertFalse(session.output_dir.exists())

    def test_job_storage_cannot_be_inside_result_folder(self):
        output = output_target(self.source, "_roto")
        with patch.dict(os.environ, {"SHOTBOX_JOB_DIR": str(output / "jobs")}):
            with self.assertRaisesRegex(ValueError, "outside the output"):
                ExportSession.reserve(self.source, "_roto", "version-up")
        self.assertFalse(output.exists())

    def test_closed_session_never_removes_a_reacquired_lock(self):
        first = ExportSession.reserve(self.source, "_roto", "overwrite")
        first.close()
        second = ExportSession.reserve(self.source, "_roto", "overwrite")
        try:
            first.close()
            self.assertTrue(second.lock_dir.exists())
            first.fail(RuntimeError("already stopped"))
            self.assertTrue(second.lock_dir.exists())
        finally:
            second.close()

    def test_broken_destination_symlink_is_preserved(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        name = frame_filename(output, 1001)
        link = output / name
        try:
            link.symlink_to(output / "missing.exr")
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation unavailable")
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        (session.stage_dir / name).write_text("new")
        with self.assertRaisesRegex(ValueError, "non-file output"):
            session.commit([name])
        self.assertTrue(link.is_symlink())
        self.assertEqual((session.stage_dir / name).read_text(), "new")

    def test_overwrite_replaces_frame_with_differently_cased_old_name(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        name = frame_filename(output, 1001)
        old_name = output.name.upper() + ".1001.exr"
        (output / old_name).write_text("old")
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        (session.stage_dir / name).write_text("new")
        session.commit([name])
        self.assertEqual((output / name).read_text(), "new")
        self.assertEqual(len(list(output.iterdir())), 1)


class CachedExportTests(ExportTests):
    """Exercise the same reservation and recovery guarantees in Toolbox mode."""

    def setUp(self):
        super().setUp()
        self.cache = Path(self.temp.name) / ".cache" / "temp" / "shotbox-matte"
        self.cache_environment = patch.dict(os.environ, {"SHOTBOX_TEMP_DIR": str(self.cache)})
        self.cache_environment.start()
        self.stage_parent = self.cache / "exports"

    def tearDown(self):
        self.cache_environment.stop()
        super().tearDown()

    def test_working_files_stay_in_cache_on_cancellation(self):
        session = ExportSession.reserve(self.source, "_roto", "version-up")
        name = frame_filename(session.output_dir, 1001)
        (session.stage_dir / name).write_text("unfinished")
        self.assertTrue(session.stage_dir.is_relative_to(self.cache))
        self.assertTrue(session.lock_dir.is_relative_to(self.cache))
        self.assertFalse(session.output_dir.exists())
        self.assertEqual(set(self.source.parent.iterdir()), {self.source, self.cache.parents[1], session.job_dir.parent})
        session.fail(RuntimeError("stopped"), cancelled=True)
        self.assertEqual((session.stage_dir / name).read_text(), "unfinished")
        self.assertFalse(session.lock_dir.exists())

    def test_cached_locks_distinguish_same_named_shots(self):
        first = ExportSession.reserve(self.source, "_roto", "overwrite")
        other_source = self.source.parent / "other-shot" / self.source.name
        other_source.mkdir(parents=True)
        second = ExportSession.reserve(other_source, "_roto", "overwrite")
        try:
            self.assertNotEqual(first.lock_dir, second.lock_dir)
        finally:
            first.close()
            second.close()

    def test_cached_version_preview_counts_locks_without_creating_files(self):
        output = output_target(self.source, "_roto")
        lock = export._lock_path(output)
        lock.parent.mkdir(parents=True)
        lock.mkdir()
        before = set(self.cache.rglob("*"))
        self.assertEqual(preview_output(self.source).name, "bam190_DN_roto_v002")
        self.assertEqual(set(self.cache.rglob("*")), before)

    def test_temporary_storage_cannot_be_inside_result_folder(self):
        output = output_target(self.source, "_roto")
        with patch.dict(os.environ, {"SHOTBOX_TEMP_DIR": str(output / "temp")}):
            with self.assertRaisesRegex(ValueError, "outside the output"):
                ExportSession.reserve(self.source, "_roto", "version-up")
        self.assertFalse(output.exists())


class CrossDeviceExportTests(CachedExportTests):
    """Model Windows drives and production mounts where rename raises EXDEV."""

    def setUp(self):
        super().setUp()
        original = Path.replace

        def cross_device(path, destination):
            if path.is_relative_to(self.cache) != Path(destination).is_relative_to(self.cache):
                raise OSError(errno.EXDEV, "cross-device move")
            return original(path, destination)

        self.cross_device = patch.object(Path, "replace", cross_device)
        self.cross_device.start()

    def tearDown(self):
        self.cross_device.stop()
        super().tearDown()

    def test_partial_copy_failure_restores_original_output(self):
        output = output_target(self.source, "_roto")
        output.mkdir()
        name = frame_filename(output, 1001)
        (output / name).write_text("old")
        session = ExportSession.reserve(self.source, "_roto", "overwrite")
        (session.stage_dir / name).write_text("new")
        original = export.shutil.copy2

        def fail_copy(source, destination):
            if source == session.stage_dir / name:
                Path(destination).write_text("incomplete")
                raise OSError("copy failed")
            return original(source, destination)

        with patch.object(export.shutil, "copy2", fail_copy):
            with self.assertRaisesRegex(OSError, "copy failed"):
                session.commit([name])
        self.assertEqual((output / name).read_text(), "old")
        self.assertEqual((session.stage_dir / name).read_text(), "new")
        self.assertFalse(session.lock_dir.exists())
        self.assertEqual(json.loads(session.manifest_path.read_text())["status"], "failed")

    def test_partial_copy_failure_leaves_no_new_output_folder(self):
        session = ExportSession.reserve(self.source, "_roto", "version-up")
        names = [frame_filename(session.output_dir, number) for number in (1001, 1002)]
        for name in names:
            (session.stage_dir / name).write_text(name)
        copy = export.shutil.copy2

        def fail_copy(source, destination):
            if source == session.stage_dir / names[1]:
                Path(destination).write_text("incomplete")
                raise OSError("copy failed")
            return copy(source, destination)

        with patch.object(export.shutil, "copy2", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "copy failed"):
                session.commit(names)
        self.assertFalse(session.output_dir.exists())
        self.assertEqual([(session.stage_dir / name).read_text() for name in names], names)
        self.assertFalse(session.lock_dir.exists())


if __name__ == "__main__":
    unittest.main()
