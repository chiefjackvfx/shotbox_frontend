"""Read-only Deadline repository inspection, executed by deadlinecommand."""
import json


def __main__(*args):
    from Deadline.Scripting import RepositoryUtils
    if args and args[0] == "reconcile":
        jobs = [job.JobId for job in RepositoryUtils.GetJobs(True)
                if job.GetJobExtraInfoKeyValue("ShotboxSubmission") == args[1]]
        print("SHOTBOX_DEADLINE " + json.dumps({"jobs": jobs}))
        return
    config = RepositoryUtils.GetPluginConfig("Nuke")
    keys = [str(k) for k in config.GetConfigKeys() if str(k).startswith("RenderExecutable")]
    print("SHOTBOX_DEADLINE " + json.dumps({
        "repository": RepositoryUtils.GetRepositoryPath("", False),
        "pools": list(RepositoryUtils.GetPoolNames()),
        "groups": list(RepositoryUtils.GetGroupNames()),
        "max_priority": RepositoryUtils.GetMaximumPriority(),
        "path_mapping_enabled": config.GetBooleanConfigEntryWithDefault("EnablePathMapping", True),
        "executables": {k: config.GetConfigEntryWithDefault(k, "") for k in keys},
    }))
