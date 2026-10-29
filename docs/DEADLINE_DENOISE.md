# Deadline denoising (Linux submission, Windows/Linux workers)

Choose **Deadline** in the single-clip or batch Denoise dialog. Local remains
the default. Deadline always reserves a new output version, profiles each clip
locally, and submits its saved profile as a Nuke auxiliary file. Each clip is
one job covering its complete frame range. Submission
does not mean the frames have rendered; track jobs in Deadline Monitor.

Resolution, frame numbering, colourspace, Neat settings, half-float RGBA EXRs,
one-scanline ZIP compression and the existing render-thread cap are preserved.
Workers must have the exact profiling Nuke build and a compatible Neat plugin.
The saved script checks the exact Nuke build and selected worker platforms before rendering.
Windows Shotbox submission is not supported in this phase; local Windows denoising is unchanged.

## Shared and local projects

Settings → **Deadline Denoise** configures the client executable, shared project
roots (separated by semicolons), default pool, group and priority. An empty
client path uses `DEADLINE_PATH`, PATH or the standard Deadline installation.
The client supplies the repository connection; no repository path is hardcoded.
Empty shared-root settings discover existing Shotbox project mounts.

For this installation, the primary shared root is `/Volumes/projects/PROJECTS`.
Existing pool preferences are preserved (`linux` for a fresh installation).
Choose a pool containing both platforms, such as this farm's `houdini` pool,
when submitting mixed jobs. Settings → **Qualified Workers** contains exact
worker names separated by commas. Leave workers out until native render tests
confirm their Nuke build, Neat profile compatibility, licences, codecs and
shared-storage access. An empty allowlist blocks shared-farm submission.

Queue refresh displays eligible workers. Every submission rechecks enabled
workers against the allowlist, pool, group and any explicit test-worker
restriction, then writes the resulting Deadline allowlist. Offline but enabled
qualified workers may receive queued jobs. Pool `none` and group `none` use
Deadline's unrestricted scheduling convention, subject to the repository's
ExcludeInNonePool/Group settings, which preflight also checks. No new pool is created.

For Windows workers, enable **Nuke → Enable Path Mapping** and configure
repository mappings from the Linux source and output roots to Windows paths
accessible to the Worker account. This farm maps `/Volumes/projects` to `Z:`.
Preflight requires literal, All-region rules and checks absolute destinations;
regex/region-specific configurations fail with setup guidance. Source paths
remain unchanged in the auxiliary script; Deadline maps its worker-local copy.
The application only reads repository configuration during submission.

Receipts record selected workers, platforms and the mapping snapshot, as well
as the profile digest and job ID. These are diagnostic records, not proof that
all listed workers rendered a task. Worker qualification is explicit and must
be repeated after relevant software, licence or storage changes.

**Allow local projects (this Linux computer only)** is enabled by default on
Linux. A source or output outside the configured shared roots triggers a
read-only check for an enabled Linux Deadline worker named after this computer.
Its reported host must match. The job is then restricted to that worker using
Deadline's allowlist, with pool/group `none` so shared queue membership cannot
prevent the local worker accepting it. Other workers cannot pick up this job.
For example, the desktop `000test` project can render through `gambit-local`
without moving footage. Disable the checkbox to require shared storage.

Restart Shotbox after installing updated code. Existing running Settings pages
can hold old values; the Deadline form and shared-root discovery load with the
new code. Queue refresh is asynchronous and reports the connected repository.

## Cancellation, failures and receipts

Cancelling in Shotbox stops local preparation and further submissions. Accepted
jobs remain queued and are listed with their IDs; cancel them in Deadline
Monitor. Successful submissions survive failures in other clips. Retry a failed
clip by explicitly selecting it again; no failed jobs are retried automatically.

Submission receipts and source locks are temporary files under
`.cache/temp/deadline-submissions/<shot-key>/`. New submissions do not create
`shot_assets/deadline`. A receipt records the source, output, frame range,
profile digest, repository and job ID while preparation and submission run.
Per-shot/source cache locks prevent concurrent or unresolved submissions from
the same Shotbox installation.

After Shotbox reads a confirmed submission, cancellation or failure and stops
its local child processes, it removes the receipt and the matching
`.cache/temp/denoise-*` workspace, including the submission package and logs.
The progress dialog keeps its activity text; Open Logs is disabled after cleanup.
Queued job IDs remain in the batch summary and Deadline Monitor. Deadline owns
the submitted auxiliary script after acceptance, so deleting the local cache
package does not remove scripts needed by queued jobs. Rendered frames, source
footage, and artist assets are preserved.

If submission is interrupted or Deadline returns no unambiguous ID, the cached
receipt, lock and workspace remain available until the outcome is reconciled.
The progress dialog reports the receipt path. Reconcile it with the configured
Deadline client:

```sh
venv/bin/python deadline_denoise.py --reconcile .cache/temp/deadline-submissions/SHOT_KEY/ID.json
```

Add `--client /path/to/deadlinecommand` if discovery is unavailable. Exactly one
matching `ShotboxSubmission` job resolves the receipt and removes its temporary
files. Zero or multiple matches retain recovery data for inspection in Monitor.
A preparation-stage receipt whose supervisor has exited can also be reconciled;
its recorded local children must stop before files are removed. Existing legacy
receipts and unresolved source locks in shot assets remain recognized, and
reconciling a selected legacy receipt removes only that receipt and its lock.

These files use the existing leased `temp` cache category. Cache clearing skips
them while local submission/preparation is active. Unresolved recovery data is
disposable cache, so inspect Deadline Monitor before retrying if it was cleared.
The feature does not stage or copy source footage automatically.

## Chunk size and concurrency

**Chunked rendering** defaults to enabled with **10 frames per task**. Change
the task size in the submission dialog or set its default in Settings. Disable
chunking to render the entire clip as one task. Each task opens the same saved
profile and retains the complete source range for temporal filtering. Nuke batch
mode keeps the script loaded between tasks when chunking is enabled.

**Concurrent tasks per worker** defaults to **1**, with a selectable range of
1–16. This sets Deadline's job concurrency; the worker's task/CPU limit still
applies. Each concurrent Nuke process retains the existing render-thread cap.
Local-project jobs remain restricted to this computer even with concurrency
above one. Pool, group, priority, chunk size and concurrency are remembered.

## Historical Linux verification

`test/deadline_denoise_acceptance.py` explicitly stages exactly four `000test`
clips in a unique test folder and restricts their jobs to a named Linux worker.
This staging is a test fixture, not a product feature. On 2026-10-27 all four
test jobs completed on `gambit-local`, producing 331 frames in total. Evidence:
`.cache/temp/deadline-four-qtjvmwwj/acceptance.json`. The fixture lives at
`/Volumes/projects/PROJECTS/000test_ShotboxDeadline_08bbc3d0`.

`test/deadline_verify_outputs.py` checks frame counts, nonempty files, dimensions
and RGBA channels for every frame. It renders the same saved profiles locally
as continuous full clips and compares the first, middle and last frames of each
clip, reporting numerical differences rather than assuming pixel equality.
The full-clip comparison checked all 331 farm frames and compared 12 frames
against continuous local renders using the same saved profiles. The samples
were not pixel-identical: mean absolute channel difference was 0.00003254 and
the maximum absolute difference was 0.1875. These measurements do not establish
pixel equality or a perceptual quality threshold. Image processing settings
and output resolution are preserved. Evidence:
`.cache/temp/deadline-output-check-79uu4qud/verification.json`.

Automated coverage includes profile-only behavior, full-clip jobs, auxiliary
files, missing clients, repository errors, local-worker pinning, invalid queues,
new-version selection, cancellation, partial success, uncertain outcomes,
duplicate prevention and reconciliation. Windows workers were not qualified by those original tests.

The final focused suite passed 147 tests, including chunk sizes and concurrency.
A live local `000test` job (`6ae111dabb68121d97da560a`, `sho010_DN_v005`)
completed all eight tasks with 25-frame chunks and concurrency 10, with zero
failed tasks. Four additional test submissions with 10-frame chunks and
concurrency 2 are recorded in
`.cache/temp/deadline-four-chunked-6ik7yaja/acceptance.json`; their IDs were no
longer listed during follow-up, so complete execution of those four jobs is
not claimed and they were not automatically resubmitted.


## Mixed-farm acceptance tools

`test/deadline_mixed_acceptance.py REPORT --nuke EXECUTABLE --workers WORKER... --pool POOL`
reuses the four saved profiles from an existing acceptance report, reserving new
output versions for each candidate. Start with `--smoke` (one frame of the first
clip per candidate), then run full clips. This is an explicit qualification
harness; its candidate list never updates the application's qualified workers.
Reports record each accepted job and stop on an uncertain submission rather
than automatically retrying. Inspect existing reports before rerunning.

`test/deadline_ui_acceptance.py` accepts the same arguments and drives the real
dashboard Denoise dialog/controller with an isolated four-clip backend fixture.
Local profiling and farm submissions are real. It does not alter production
backend records or the operator's settings.

`test/deadline_verify_outputs.py REPORT --nuke EXECUTABLE` decodes every output,
checks dimensions, finite half-float RGBA pixels and single-scanline ZIP, and
compares first/middle/last frames plus both sides of each ten-frame boundary
against continuous local renders of the same profiles. It reports numerical
differences; it does not claim bit equality or impose an invented perceptual
quality threshold. Temporary reports and reference renders use leased app cache.

The repository change for this rollout is recorded in
[DEADLINE_MAPPING_ROLLBACK.json](DEADLINE_MAPPING_ROLLBACK.json): Nuke's
`EnablePathMapping` changed from `False` to `True`; mapping rules were preserved.
To roll back, restore that value in Deadline Monitor → Tools → Configure Plugins
→ Nuke. Mixed Windows submissions will then fail preflight until it is enabled.
Do not use this installed Deadline version's `AddOrUpdatePluginConfigSetting`
helper for rollback: it writes event-plugin configuration instead.

See [mixed-farm verification](DEADLINE_MIXED_VERIFICATION.md) for native Windows/Linux results and current qualified workers.
