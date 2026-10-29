from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import app_cache
import deadline_client as client
import deadline_denoise as workflow


def repository():
    return {"repository": "/repo", "pools": ["linux", "none"], "groups": ["none"], "max_priority": 100,
            "path_mapping_enabled": False, "mapping": {},
            "workers": [{"name": "linux-worker", "platform": "linux", "enabled": True, "pools": ["linux"], "groups": []}],
            "executables": {"RenderExecutable17_1": "/opt/Nuke17.1v2/Nuke17.1"}}


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        cache = mock.patch.object(app_cache, "CACHE_ROOT", self.root / "frontend-cache")
        cache.start()
        self.addCleanup(cache.stop)
        self.source = self.root / "source with spaces.mxf"
        self.source.touch()
        self.request = {"source": str(self.source), "nuke": "/opt/Nuke17.1v2/Nuke17.1",
                        "output_pattern": str(self.root / "shot/renders/new_v001/new_####.exr"),
                        "deadline": {"submission_id": "a" * 32, "shot_dir": str(self.root / "shot"),
                                     "batch_name": "Batch with spaces", "pool": "linux", "group": "none",
                                     "qualified_workers": ["linux-worker"], "priority": 50, "shared_roots": [str(self.root)], "client": "/client"}}
        self.path = self.root / "request.json"
        self.path.write_text(json.dumps(self.request))
        self.prepared = {"stage": "prepared", "first": 1001, "last": 1179, "nuke_version": "17.1v2"}
        (self.root / "profiled.nk").write_text("prepared profile")
        self.job_id = "b" * 24

    def patches(self):
        return (mock.patch.object(client, "discover_client", return_value="/client"),
                mock.patch.object(client, "repository_info", return_value=repository()),
                mock.patch.object(workflow, "profile", return_value=self.prepared))

    def run_workflow(self, **command_options):
        a, b, c = self.patches()
        with a, b, c as profiling, mock.patch.object(client, "command", **command_options) as command:
            result = workflow.run(self.path, notify=lambda event: None)
        return result, profiling, command

    def test_full_clip_job_uses_auxiliary_script_and_original_threads(self):
        self.request["deadline"]["chunked"] = False
        paths = client.submission_files(self.root, self.request, self.prepared, repository())
        job = Path(paths[0]).read_text()
        plugin = Path(paths[1]).read_text()
        self.assertIn("Frames=1001-1179\nChunkSize=179", job)
        self.assertIn("ConcurrentTasks=1", job)
        self.assertIn("WriteNode=Write1", plugin)
        self.assertIn("Version=17.1", plugin)
        self.assertNotIn("SceneFile", plugin)
        self.assertEqual(paths[2], str(self.root / "profiled.nk"))
        self.assertIn("RenderMode=Render Full Resolution", plugin)

    def test_default_chunks_are_ten_frames_and_concurrency_is_configurable(self):
        paths = client.submission_files(self.root, self.request, self.prepared, repository())
        self.assertIn("Frames=1001-1179\nChunkSize=10", Path(paths[0]).read_text())
        self.assertIn("BatchMode=True", Path(paths[1]).read_text())
        self.request["deadline"].update(chunk_size=25, concurrent_tasks=4)
        paths = client.submission_files(self.root, self.request, self.prepared, repository())
        job = Path(paths[0]).read_text()
        self.assertIn("ChunkSize=25", job)
        self.assertIn("ConcurrentTasks=4", job)
        self.assertIn("LimitConcurrentTasksToNumberOfCpus=True", job)
        self.assertIn("Frames=1001-1179", job)

    def test_short_clips_and_invalid_chunk_or_concurrency_values(self):
        self.prepared["last"] = 1005
        paths = client.submission_files(self.root, self.request, self.prepared, repository())
        self.assertIn("ChunkSize=5", Path(paths[0]).read_text())
        for invalid in (0, -1, "ten", True, 1000001):
            with self.subTest(chunk_size=invalid), self.assertRaises(RuntimeError):
                client.validate_options({"chunk_size": invalid}, repository())
        for invalid in (0, 17, "four", True):
            with self.subTest(concurrent_tasks=invalid), self.assertRaises(RuntimeError):
                client.validate_options({"concurrent_tasks": invalid}, repository())

    def test_shared_roots_are_required(self):
        client.validate_shared(["/show/A/clip.mxf"], ["/show"], repository())
        for path in ("/show-other/clip.mxf", "/home/local/clip.mxf"):
            with self.assertRaisesRegex(RuntimeError, "Local-only"):
                client.validate_shared([path], ["/show"], repository())
        with self.assertRaisesRegex(RuntimeError, "Configure shared"):
            client.validate_shared(["/show/clip"], [], repository())

    def test_version_must_match_exact_linux_build(self):
        self.assertEqual(client.validate_version("17.1v2", repository()), "17.1")
        with self.assertRaisesRegex(RuntimeError, "exact Nuke"):
            client.validate_version("17.1v3", repository())
        with self.assertRaisesRegex(RuntimeError, "exact Nuke"):
            client.validate_version("17.0v1", repository())

    def test_local_project_is_pinned_to_verified_linux_worker(self):
        options = {"allow_local": True, "shared_roots": [], "pool": "linux", "group": "none"}
        response = "HostName=local-box\nID=local-box\nMachineOperatingSystem=Linux\nEnabled=True"
        with mock.patch.object(client.sys, "platform", "linux"), \
                mock.patch.object(client.socket, "gethostname", return_value="local-box"), \
                mock.patch.object(client, "command", return_value=response) as command:
            client.validate_destination([str(self.source)], options, repository(), "/client")
        self.assertEqual(options["machine_list"], "local-box")
        self.assertEqual(options["pool"], "none")
        self.assertEqual(options["group"], "none")
        self.assertTrue(options["local_project"])
        command.assert_called_once_with("/client", ["-GetSlave", "local-box"])
        self.request["deadline"].update(options)
        paths = client.submission_files(self.root, self.request, self.prepared, repository())
        self.assertIn("Whitelist=local-box\n", Path(paths[0]).read_text())

    def test_local_project_rejects_foreign_or_disabled_worker(self):
        for response in ("HostName=other\nID=local-box\nMachineOperatingSystem=Linux\nEnabled=True",
                         "HostName=local-box\nID=local-box\nMachineOperatingSystem=Linux\nEnabled=False"):
            with mock.patch.object(client.sys, "platform", "linux"), \
                    mock.patch.object(client.socket, "gethostname", return_value="local-box"), \
                    mock.patch.object(client, "command", return_value=response), self.assertRaisesRegex(RuntimeError, "enabled Linux"):
                client.validate_destination([str(self.source)], {"allow_local": True}, repository(), "/client")

    def test_local_project_requires_opt_in_and_shared_paths_keep_pool(self):
        with self.assertRaises(RuntimeError):
            client.validate_destination([str(self.source)], {}, repository(), "/client")
        options = {"shared_roots": [str(self.root)], "pool": "linux", "allow_local": True, "qualified_workers": ["linux-worker"]}
        with mock.patch.object(client, "command") as command:
            client.validate_destination([str(self.source)], options, repository(), "/client")
        command.assert_not_called()
        self.assertEqual(options["machine_list"], "linux-worker")
        self.assertEqual(options["pool"], "linux")

    def test_rejects_invalid_queue_priority_and_injected_fields(self):
        for options in ({"pool": "missing"}, {"priority": 101}, {"group": "missing"}):
            with self.assertRaises(RuntimeError):
                client.validate_options(options, repository())
        with self.assertRaises(ValueError):
            client.write_info(self.root / "bad.job", {"Name": "a\nPool=other"})

    def test_worker_intersection_and_missing_qualification(self):
        info = repository()
        info["workers"] += [{"name": "windows-worker", "platform": "win32", "enabled": True, "pools": ["houdini"], "groups": []},
                            {"name": "disabled", "platform": "linux", "enabled": False, "pools": ["linux"], "groups": []}]
        with self.assertRaisesRegex(RuntimeError, "Configure qualified"):
            client.select_workers({}, info)
        options = {"qualified_workers": ["linux-worker", "windows-worker", "disabled"], "pool": "none"}
        client.select_workers(options, info)
        self.assertEqual(options["machine_list"], "linux-worker,windows-worker")
        self.assertEqual(options["platforms"], ["linux", "win32"])
        options.update(pool="houdini", machine_list="")
        client.select_workers(options, info)
        self.assertEqual(options["machine_list"], "windows-worker")
        options.update(group="missing")
        with self.assertRaisesRegex(RuntimeError, "No qualified"):
            client.select_workers(options, info)

    def test_windows_mapping_requires_enabled_covered_absolute_destination(self):
        info = repository()
        paths = ["/show/a/source.mxf", "/show/a/render/out_####.exr"]
        with self.assertRaisesRegex(RuntimeError, "Enable Path"):
            client.validate_mapping_coverage(paths, ["linux", "win32"], info)
        info.update(path_mapping_enabled=True, path_mappings={"GlobalRules": [
            {"Path": "/show", "WindowsPath": "Z:", "CaseSensitive": True}]})
        client.validate_mapping_coverage(paths, ["linux", "win32"], info)
        for path in ("/show-other/a", "/SHOW/a"):
            with self.assertRaisesRegex(RuntimeError, "No Windows"):
                client.validate_mapping_coverage([path], ["win32"], info)
        rule = info["path_mappings"]["GlobalRules"][0]
        rule.update(CaseSensitive=False, WindowsPath=r"\\server\share")
        client.validate_mapping_coverage(["/SHOW/a"], ["win32"], info)
        rule["WindowsPath"] = "relative/path"
        with self.assertRaisesRegex(RuntimeError, "absolute Windows"):
            client.validate_mapping_coverage(paths, ["win32"], info)

    def test_none_queue_respects_repository_exclusion(self):
        info = repository()
        options = {"qualified_workers": ["linux-worker"], "pool": "none"}
        self.assertEqual(len(client.eligible_workers(options, info)), 1)
        info["exclude_in_none_pool"] = True
        self.assertEqual(client.eligible_workers(options, info), [])
        info["workers"][0]["pools"] = []
        self.assertEqual(len(client.eligible_workers(options, info)), 1)

    def test_invalid_linux_mapping_and_complex_rules_fail_closed(self):
        info = repository()
        rule = {"Path": "/show", "LinuxPath": "relative", "WindowsPath": "Z:"}
        info.update(path_mapping_enabled=True, path_mappings={"GlobalRules": [rule]})
        with self.assertRaisesRegex(RuntimeError, "absolute Linux"):
            client.validate_mapping_coverage(["/show/a"], ["linux", "win32"], info)
        rule.update(LinuxPath="/mnt/show", RegularExpression=True)
        with self.assertRaisesRegex(RuntimeError, "literal"):
            client.validate_mapping_coverage(["/show/a"], ["linux", "win32"], info)

    def test_exact_build_validation_on_both_platforms(self):
        info = repository()
        with self.assertRaisesRegex(RuntimeError, "win32"):
            client.validate_version("17.1v2", info, ["linux", "win32"])
        info["executables"]["RenderExecutable17_1"] += r";C:\Program Files\Nuke17.1v2\Nuke17.1.exe"
        client.validate_version("17.1v2", info, ["linux", "win32"])
        info["executables"]["RenderExecutable17_1"] = "/Applications/Nuke17.1v2/Nuke17.1v2.app/Contents/MacOS/Nuke17.1"
        with self.assertRaisesRegex(RuntimeError, "exact Nuke"):
            client.validate_version("17.1v2", info, ["linux"])
        info["executables"]["RenderExecutable17_1"] = "/opt/Nuke17.1v20/Nuke17.1"
        with self.assertRaisesRegex(RuntimeError, "exact Nuke"):
            client.validate_version("17.1v2", info)

    def test_guard_allows_only_selected_platforms_and_exact_build(self):
        from types import SimpleNamespace
        guard = client.render_guard("17.1v2", ["linux", "win32"])
        for platform, version, allowed in (("win32", "17.1v2", True), ("linux", "17.1v2", True),
                                           ("darwin", "17.1v2", False), ("win32", "17.1v3", False)):
            with mock.patch.dict("sys.modules", {"nuke": SimpleNamespace(NUKE_VERSION_STRING=version),
                                                 "sys": SimpleNamespace(platform=platform)}):
                if allowed:
                    exec(guard, {})
                else:
                    with self.assertRaises(RuntimeError):
                        exec(guard, {})

    def test_windows_submission_is_rejected_before_repository_or_profiling(self):
        with mock.patch.object(workflow.sys, "platform", "win32"):
            result, profile, command = self.run_workflow()
        self.assertEqual(result["stage"], "failed")
        self.assertIn("requires Linux", result["error"])
        profile.assert_not_called()
        command.assert_not_called()

    def test_unqualified_submission_never_profiles(self):
        self.request["deadline"]["qualified_workers"] = []
        self.path.write_text(json.dumps(self.request))
        result, profile, command = self.run_workflow()
        self.assertEqual(result["stage"], "failed")
        self.assertIn("qualified", result["error"])
        profile.assert_not_called()
        command.assert_not_called()

    def test_missing_client_is_actionable(self):
        with self.assertRaisesRegex(RuntimeError, "Settings"):
            client.discover_client(str(self.root / "missing client"))

    def test_submission_persists_receipt_and_never_resubmits_same_request(self):
        result, profiling, command = self.run_workflow(return_value="JobID=" + self.job_id)
        self.assertEqual(result["stage"], "submitted")
        self.assertEqual(result["job_id"], self.job_id)
        receipt = json.loads(Path(result["receipt"]).read_text())
        self.assertEqual(receipt["status"], "submitted")
        self.assertEqual(receipt["platforms"], ["linux"])
        self.assertEqual(receipt["machine_list"], "linux-worker")
        self.assertEqual(receipt["first"], 1001)
        self.assertFalse(Path(receipt["lock"]).exists())
        self.assertTrue(json.loads(self.path.read_text())["profile_only"])
        again, profiling, command = self.run_workflow(side_effect=AssertionError("duplicate"))
        self.assertEqual(again["job_id"], self.job_id)
        command.assert_not_called()
        profiling.assert_not_called()

    def test_known_submission_is_cached_then_removed_without_shot_metadata(self):
        result, _, _ = self.run_workflow(return_value="JobID=" + self.job_id)
        receipt = Path(result["receipt"])
        self.assertTrue(receipt.is_relative_to(app_cache.cache_directory("temp")))
        self.assertFalse((Path(self.request["deadline"]["shot_dir"]) / "shot_assets/deadline").exists())
        self.assertEqual(workflow.recover_result(self.request, {})["job_id"], self.job_id)
        self.assertTrue(workflow.cleanup_submission(self.request, result))
        self.assertFalse(receipt.exists())
        self.assertFalse(receipt.parent.exists())

    def test_unknown_submission_keeps_cache_recovery_until_reconciled(self):
        result, _, _ = self.run_workflow(side_effect=client.CommandInterrupted("timeout"))
        receipt = Path(result["receipt"])
        lock = Path(json.loads(receipt.read_text())["lock"])
        self.assertFalse(workflow.cleanup_submission(self.request, result))
        self.assertTrue(receipt.exists())
        self.assertTrue(lock.exists())
        self.assertFalse((Path(self.request["deadline"]["shot_dir"]) / "shot_assets").exists())
        with mock.patch.object(client, "discover_client", return_value="/client"), \
                mock.patch.object(client, "command", return_value='SHOTBOX_DEADLINE ' + json.dumps({"jobs": [self.job_id]})):
            resolved = workflow.reconcile(receipt, "/client")
        self.assertEqual(resolved["job_id"], self.job_id)
        self.assertFalse(receipt.exists())
        self.assertFalse(lock.exists())

    def test_reconciliation_waits_for_children_then_removes_cached_package(self):
        result, _, _ = self.run_workflow(side_effect=client.CommandInterrupted("timeout"))
        receipt_path = Path(result["receipt"])
        receipt = json.loads(receipt_path.read_text())
        lock = Path(receipt["lock"])
        workspace = Path(tempfile.mkdtemp(prefix="denoise-", dir=app_cache.cache_directory("temp")))
        (workspace / "profiled.nk").write_text("farm package")
        receipt["directory"] = str(workspace)
        receipt_path.write_text(json.dumps(receipt))
        with mock.patch.object(client, "discover_client", return_value="/client"), \
                mock.patch.object(client, "command", return_value='SHOTBOX_DEADLINE ' + json.dumps({"jobs": [self.job_id]})) as command, \
                mock.patch("nuke_denoise_display.stop_recorded_children", side_effect=[True, False]):
            with self.assertRaisesRegex(RuntimeError, "cleanup is still running"):
                workflow.reconcile(receipt_path, "/client")
            self.assertTrue(workspace.exists())
            self.assertTrue(receipt_path.exists())
            self.assertTrue(lock.exists())
            resolved = workflow.reconcile(receipt_path, "/client")
            command.assert_called_once()
        self.assertEqual(resolved["job_id"], self.job_id)
        self.assertFalse(workspace.exists())
        self.assertFalse(receipt_path.exists())
        self.assertFalse(lock.exists())

    def test_active_submission_receipt_survives_clear_cache(self):
        path, _ = workflow.submission_paths(self.request)
        def profiled(*args, **kwargs):
            result = app_cache.clear_cache_files()
            self.assertTrue(result["active_temp"])
            self.assertTrue(path.exists())
            return self.prepared
        a, b, _ = self.patches()
        with a, b, mock.patch.object(workflow, "profile", side_effect=profiled), \
                mock.patch.object(client, "command", return_value="JobID=" + self.job_id):
            result = workflow.run(self.path, notify=lambda event: None)
        self.assertEqual(result["stage"], "submitted")

    def test_cleanup_does_not_remove_another_live_preparation_receipt(self):
        import os
        import psutil
        path, lock = workflow.submission_paths(self.request)
        path.parent.mkdir(parents=True)
        lock.write_text(str(path))
        path.write_text(json.dumps({"status": "preparing", "lock": str(lock),
                                    "supervisor_pid": os.getpid(),
                                    "supervisor_created": psutil.Process().create_time()}))
        self.assertFalse(workflow.cleanup_submission(self.request, {"stage": "failed"}))
        self.assertTrue(path.exists())
        self.assertTrue(lock.exists())

    def test_cleanup_workspace_only_removes_owned_temp_cache_directories(self):
        directory = Path(tempfile.mkdtemp(prefix="denoise-", dir=app_cache.cache_directory("temp")))
        (directory / "profiled.nk").touch()
        self.assertTrue(workflow.cleanup_workspace(directory))
        self.assertFalse(directory.exists())
        outside = self.root / "denoise-project"
        outside.mkdir()
        (outside / "artist.nk").touch()
        self.assertFalse(workflow.cleanup_workspace(outside))
        self.assertTrue((outside / "artist.nk").exists())

    def test_legacy_receipt_recovers_without_resubmission_and_cleans_only_its_files(self):
        cached, lock = workflow.submission_paths(self.request)
        legacy = Path(self.request["deadline"]["shot_dir"]) / "shot_assets/deadline"
        legacy.mkdir(parents=True)
        path = legacy / cached.name
        legacy_lock = legacy / lock.name
        path.write_text(json.dumps({"status": "submitted", "job_id": self.job_id, "lock": str(legacy_lock)}))
        legacy_lock.write_text(str(path))
        (legacy / "keep.txt").write_text("unrelated")
        self.assertEqual(workflow.recover_result(self.request, {})["job_id"], self.job_id)
        result, profile, command = self.run_workflow(side_effect=AssertionError("duplicate"))
        self.assertEqual(result["job_id"], self.job_id)
        profile.assert_not_called()
        command.assert_not_called()
        self.assertTrue(workflow.cleanup_submission(self.request, result))
        self.assertFalse(path.exists())
        self.assertFalse(legacy_lock.exists())
        self.assertEqual((legacy / "keep.txt").read_text(), "unrelated")

    def test_legacy_unresolved_source_lock_blocks_new_submission(self):
        cached, lock = workflow.submission_paths(self.request)
        legacy = Path(self.request["deadline"]["shot_dir"]) / "shot_assets/deadline"
        legacy.mkdir(parents=True)
        (legacy / lock.name).write_text(str(legacy / "previous.json"))
        result, profile, command = self.run_workflow(side_effect=AssertionError("duplicate"))
        self.assertEqual(result["stage"], "failed")
        self.assertIn("unresolved legacy", result["error"])
        profile.assert_not_called()
        command.assert_not_called()
        self.assertFalse(cached.exists())

    def test_timeout_keeps_lock_and_blocks_another_submission_for_source(self):
        result, _, _ = self.run_workflow(side_effect=client.CommandInterrupted("timeout"))
        self.assertEqual(result["stage"], "submission_unknown")
        receipt = json.loads(Path(result["receipt"]).read_text())
        self.assertTrue(Path(receipt["lock"]).exists())
        self.request["deadline"]["submission_id"] = "c" * 32
        self.path.write_text(json.dumps(self.request))
        result, profiling, command = self.run_workflow(side_effect=AssertionError("duplicate"))
        self.assertEqual(result["stage"], "failed")
        self.assertIn("unresolved", result["error"])
        profiling.assert_not_called()
        command.assert_not_called()

    def test_missing_job_id_is_unknown_not_retryable_failure(self):
        result, _, _ = self.run_workflow(return_value="Success")
        self.assertEqual(result["stage"], "submission_unknown")

    def test_cancel_after_profile_creates_no_farm_job(self):
        def profiled(*args, **kwargs):
            (self.root / "cancel").touch()
            return self.prepared
        a, b, _ = self.patches()
        with a, b, mock.patch.object(workflow, "profile", side_effect=profiled), mock.patch.object(client, "command") as command:
            result = workflow.run(self.path, notify=lambda event: None)
        self.assertEqual(result["stage"], "cancelled")
        command.assert_not_called()

    def test_repository_failure_stops_before_profiling(self):
        with mock.patch.object(client, "discover_client", return_value="/client"), \
                mock.patch.object(client, "repository_info", side_effect=RuntimeError("unreachable")), \
                mock.patch.object(workflow, "profile") as profile:
            result = workflow.run(self.path, notify=lambda event: None)
        self.assertEqual(result["stage"], "failed")
        profile.assert_not_called()

    def test_crash_recovery_preserves_submitted_or_unknown_result(self):
        result, _, _ = self.run_workflow(return_value="JobID=" + self.job_id)
        recovered = workflow.recover_result(self.request, {"stage": "failed"})
        self.assertEqual(recovered["job_id"], self.job_id)
        path = Path(result["receipt"])
        receipt = json.loads(path.read_text())
        receipt["status"] = "submitting"
        path.write_text(json.dumps(receipt))
        self.assertEqual(workflow.recover_result(self.request, {"stage": "prepared"})["stage"], "submission_unknown")

    def test_reconciliation_releases_unknown_lock_only_for_one_matching_job(self):
        result, _, _ = self.run_workflow(side_effect=client.CommandInterrupted("timeout"))
        path = Path(result["receipt"])
        with mock.patch.object(client, "discover_client", return_value="/client"), \
                mock.patch.object(client, "command", return_value='SHOTBOX_DEADLINE {"jobs": []}'):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                workflow.reconcile(path, "/client")
        self.assertTrue(Path(json.loads(path.read_text())["lock"]).exists())
        with mock.patch.object(client, "discover_client", return_value="/client"), \
                mock.patch.object(client, "command", return_value='SHOTBOX_DEADLINE ' + json.dumps({"jobs": [self.job_id]})):
            receipt = workflow.reconcile(path, "/client")
        self.assertEqual(receipt["status"], "submitted")
        self.assertEqual(receipt["job_id"], self.job_id)
        self.assertFalse(Path(receipt["lock"]).exists())


if __name__ == "__main__":
    unittest.main()
