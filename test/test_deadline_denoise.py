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
            "executables": {"RenderExecutable17_1": "/opt/Nuke17.1v2/Nuke17.1"}}


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=app_cache.cache_directory("temp"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source with spaces.mxf"
        self.source.touch()
        self.request = {"source": str(self.source), "nuke": "/opt/Nuke17.1v2/Nuke17.1",
                        "output_pattern": str(self.root / "shot/renders/new_v001/new_####.exr"),
                        "deadline": {"submission_id": "a" * 32, "shot_dir": str(self.root / "shot"),
                                     "batch_name": "Batch with spaces", "pool": "linux", "group": "none",
                                     "priority": 50, "shared_roots": [str(self.root)], "client": "/client"}}
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

    def test_shared_roots_are_required_but_mapping_is_deferred(self):
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
        options = {"shared_roots": [str(self.root)], "pool": "linux", "allow_local": True}
        with mock.patch.object(client, "command") as command:
            client.validate_destination([str(self.source)], options, repository(), "/client")
        command.assert_not_called()
        self.assertNotIn("machine_list", options)
        self.assertEqual(options["pool"], "linux")

    def test_rejects_invalid_queue_priority_and_injected_fields(self):
        for options in ({"pool": "missing"}, {"priority": 101}, {"group": "missing"}):
            with self.assertRaises(RuntimeError):
                client.validate_options(options, repository())
        with self.assertRaises(ValueError):
            client.write_info(self.root / "bad.job", {"Name": "a\nPool=other"})

    def test_missing_client_is_actionable(self):
        with self.assertRaisesRegex(RuntimeError, "Settings"):
            client.discover_client(str(self.root / "missing client"))

    def test_submission_persists_receipt_and_never_resubmits_same_request(self):
        result, profiling, command = self.run_workflow(return_value="JobID=" + self.job_id)
        self.assertEqual(result["stage"], "submitted")
        self.assertEqual(result["job_id"], self.job_id)
        receipt = json.loads(Path(result["receipt"]).read_text())
        self.assertEqual(receipt["status"], "submitted")
        self.assertEqual(receipt["first"], 1001)
        self.assertFalse(Path(receipt["lock"]).exists())
        self.assertTrue(json.loads(self.path.read_text())["profile_only"])
        again, profiling, command = self.run_workflow(side_effect=AssertionError("duplicate"))
        self.assertEqual(again["job_id"], self.job_id)
        command.assert_not_called()
        profiling.assert_not_called()

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
