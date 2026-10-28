# Deadline denoising (Linux)

Choose **Deadline** in the single-clip or batch Denoise dialog. Local remains
the default. Deadline always reserves a new output version, profiles each clip
locally, and submits its saved profile as a Nuke auxiliary file. Each clip is
one job covering its complete frame range. Submission
does not mean the frames have rendered; track jobs in Deadline Monitor.

Resolution, frame numbering, colourspace, Neat settings, half-float RGBA EXRs,
one-scanline ZIP compression and the existing render-thread cap are preserved.
Workers must have the exact profiling Nuke build and a compatible Neat plugin.
The saved script checks the Nuke build and Linux platform before rendering.

## Shared and local projects

Settings → **Deadline Denoise** configures the client executable, shared project
roots (separated by semicolons), default pool, group and priority. An empty
client path uses `DEADLINE_PATH`, PATH or the standard Deadline installation.
The client supplies the repository connection; no repository path is hardcoded.
Empty shared-root settings discover existing Shotbox project mounts.

For this installation, the primary shared root is `/Volumes/projects/PROJECTS`.
The `linux` pool is the default. Cross-platform mapping enforcement and Windows
worker verification are deferred. Source and output paths remain as saved in
the script; repository path mapping settings are never modified by Shotbox.

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

Persistent receipts live in `shot_assets/deadline/<submission-id>.json`. They
record the source, output, frame range, profile digest, repository and job ID.
A per-source lock prevents concurrent or unresolved submissions for the same
shot. If a submission is interrupted or Deadline returns no unambiguous ID,
Shotbox marks it uncertain and blocks a blind retry.

Reconcile an uncertain receipt with the configured Deadline client:

```sh
venv/bin/python deadline_denoise.py --reconcile /path/to/shot/shot_assets/deadline/ID.json
```

Add `--client /path/to/deadlinecommand` if discovery is unavailable. The command
matches the unique `ShotboxSubmission` metadata in Deadline. Exactly one match
records its ID and releases the lock. Zero or multiple matches retain the lock
for inspection in Monitor. A preparation-stage receipt whose supervisor has
exited can be reconciled safely without submitting a job. Do not remove an
uncertain submission lock until its farm outcome is established.

Temporary launch files and submission packages stay in leased `.cache/temp`
directories. Deadline owns the submitted auxiliary script after acceptance, so
clearing Shotbox's cache does not remove scripts needed by queued jobs. The
feature does not stage or copy source footage automatically.

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

## Verification

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
duplicate prevention and reconciliation. Windows workers are not qualified.

The final focused suite passed 147 tests, including chunk sizes and concurrency.
A live local `000test` job (`6ae111dabb68121d97da560a`, `sho010_DN_v005`)
completed all eight tasks with 25-frame chunks and concurrency 10, with zero
failed tasks. Four additional test submissions with 10-frame chunks and
concurrency 2 are recorded in
`.cache/temp/deadline-four-chunked-6ik7yaja/acceptance.json`; their IDs were no
longer listed during follow-up, so complete execution of those four jobs is
not claimed and they were not automatically resubmitted.
