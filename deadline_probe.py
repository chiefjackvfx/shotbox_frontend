"""Read-only Deadline repository inspection, executed by deadlinecommand."""
import json


def __main__(*args):
    from Deadline.Scripting import RepositoryUtils, ClientUtils
    if args and args[0] == "reconcile":
        jobs = [job.JobId for job in RepositoryUtils.GetJobs(True)
                if job.GetJobExtraInfoKeyValue("ShotboxSubmission") == args[1]]
        print("SHOTBOX_DEADLINE " + json.dumps({"jobs": jobs}))
        return
    config = RepositoryUtils.GetPluginConfig("Nuke")
    keys = [str(k) for k in config.GetConfigKeys() if str(k).startswith("RenderExecutable")]
    settings = {str(s.SlaveName): s for s in RepositoryUtils.GetSlaveSettingsList(True)}
    workers = []
    for worker in RepositoryUtils.GetSlaveInfos(True):
        setting = settings.get(str(worker.SlaveName))
        if setting is None:
            continue
        os_name = str(worker.MachineOperatingSystem)
        workers.append({"name": str(worker.SlaveName),
                        "platform": "win32" if os_name.startswith("Windows") else "linux" if os_name == "Linux" else "unsupported",
                        "os": os_name, "state": str(worker.SlaveState),
                        "enabled": bool(setting.SlaveEnabled),
                        "pools": list(setting.SlavePools), "groups": list(setting.SlaveGroups)})
    mappings = json.loads(ClientUtils.ExecuteCommandAndGetOutput(
        ["ExportRepositorySettings", "-Settings", "PathMapping"]))["PathMapping"]["Settings"]
    scheduling = json.loads(ClientUtils.ExecuteCommandAndGetOutput(
        ["ExportRepositorySettings", "-Settings", "WorkerSettings"]))["WorkerSettings"]["Settings"]
    print("SHOTBOX_DEADLINE " + json.dumps({
        "exclude_in_none_pool": scheduling.get("ExcludeInNonePool", False),
        "exclude_in_none_group": scheduling.get("ExcludeInNoneGroup", False),
        "workers": workers, "path_mappings": mappings,
        "repository": RepositoryUtils.GetRepositoryPath("", False),
        "pools": list(RepositoryUtils.GetPoolNames()),
        "groups": list(RepositoryUtils.GetGroupNames()),
        "max_priority": RepositoryUtils.GetMaximumPriority(),
        "path_mapping_enabled": config.GetBooleanConfigEntryWithDefault("EnablePathMapping", True),
        "executables": {k: config.GetConfigEntryWithDefault(k, "") for k in keys},
    }))
