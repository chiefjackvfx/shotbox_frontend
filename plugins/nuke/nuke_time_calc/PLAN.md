# nuke_time_calc development plan

## Confirmed automatic-setup requirements (supersedes manual setup)

The user requires Nuke time tracking to be **always on and automatically set up**, without a Nuke Time Settings panel, enable checkbox, Save button, credential-path fields or manual startup installation. Use the normal Shotbox server and linked artist, not a second connection profile. Store per-user/workstation state inside Shotbox `.cache/nuke-time`; Clear Cache must preserve credentials, configuration and unacknowledged uploads.

- [x] Enable ingestion on the existing `http://192.168.10.88:8000` deployment with explicit actual-peer RFC1918 policy.
- [x] Remove this workstation's legacy diagnostic startup, preserving unrelated Nuke startup.
- [x] Remove the webpage's expanded artist detail, policy/raw text and date controls; retain the compact chart and artist/hour legend.
- [x] Remove the manual settings panel and automatically enroll Linux/POSIX artist/workstation bindings; Windows remains unsupported.
- [x] Start/retry setup in the background on Shotbox launch and normal identity changes.
- [x] Automatically install the managed Nuke startup and expose read-only status, not timer controls.
- [x] Bind shot-mapping snapshots to their actual server/artist context.
- [x] Verify device-ready automatic setup, private cache protection and visible real Nuke GUI feedback. Collection still requires a verified shot; this is not a full manual editing/sleep acceptance claim.

Automatic LAN enrollment follows Shotbox's existing trusted-LAN artist selection; that selector is not a separate authenticated artist login. Enrollment must be explicitly enabled and restricted to configured actual-peer private subnets. Batch ingestion retains scoped device credentials. Do not imply that a trusted-LAN artist selection proves account ownership.

Automatic setup is installed on this Linux workstation. Live credential authentication and idempotent enrollment passed; Nuke 17.1v2 real GUI startup reports enabled and waiting for a verified shot. Verification: 176 collector tests, 80 targeted backend/web tests, 74 frontend tests plus 26 subtests, 12 cache tests and two isolated collector/API/real-Nuke pipeline tests. Earlier opt-in/manual credential instructions below describe superseded implementation stages, not artist setup requirements.

A full offscreen Shotbox startup reached automatic setup ready and confirmed the settings panel absent. Its teardown exposed an ActivityWorker shutdown problem; do not describe the full-app shutdown smoke test as passing. The isolated Nuke GUI and collector/API host gates passed.

## Implementation status

The Linux/POSIX collector and per-shot webpage reporting are implemented. **Webpage charts are installed and private-LAN HTTP ingestion is enabled. Automatic artist/device setup is installed for Linux/POSIX.** Later execution-status notes supersede the original first-phase scope below.

- Frontend implementation: existing `shotbox_frontend` checkout, including schema-2 collection, durable queue/uploader, verified shot handoff, runtime and explicit desktop setup.
- Reviewed webpage charts, chart filtering and expected-binding checks are installed in the serving backend. The durable development worktree remains `/home/gambit/Documents/shotbox-dev/nuke-time-backend`, branch `nuke-time-integration`, for remaining pilot work. Deployment backup: `/home/gambit/.local/state/shotbox/code-backups/web-time-charts-20261113T154934+0000`.
- Post-install verification: 63 isolated backend/web/binding tests passed from the serving checkout's source. Live C2236_Cirio returned HTTP 200 with 18 time panels; Chromium desktop/narrow checks found no overflow or script errors. Both chart assets returned HTTP 200. Time ingestion remains disabled.
- Parent verification: 131 plugin tests; 138 frontend regression tests plus 30 subtests; 66 targeted backend/integration tests including real Chromium and Nuke 17.1v2 terminal/Qt host acceptance. Full backend regression retains the previously documented unrelated edit-player Node fixture error.
- Real HTTP lost-ack/restart replay retained one accepted record and reconciled the chart; real Nuke callbacks/save produced positive credited spans and drained the queue. Saved non-secret results and fixture screenshots are in the backend worktree's `docs/validation/`.
- Independent reviews passed the webpage, collector/outbox/uploader, shot mapping, expected credential binding and final runtime/Qt lifecycle scopes. All reproduced blockers were fixed with regression tests and independently re-reviewed; final closure reported no unresolved blockers.
- Live automatic enrollment is deployed and one private device binding is provisioned for the existing linked artist. Live batch authentication passed without inserting a test observation. Managed Nuke startup points at protected Shotbox cache state.

**Still required:** full production-shot editing acceptance; full manual GUI/render-cancellation/sleep/coverage checks; Windows durable private storage and actual Windows Nuke validation (current queue explicitly fails closed on non-POSIX). Native desktop hour charts and daily calendar charts are not implemented; desktop setup is available and breakdowns are on the webpage. Historical project ownership/reparenting policy remains a budget-reporting gate.

See `INTEGRATION.md` for setup, failure behaviour and platform limits; backend `docs/TIME_WEBPAGE.md` has reproducible isolated verification commands. No commit, version bump, tag or publication was performed.

## Goal

Build a small, automatic Nuke comp-activity estimator for better Shotbox budgets. Track by artist, project and shot without timer buttons, timesheets, popups or daily approval. Shotbox is the reporting/storage system; Nuke supplies observations. This is a personal development experiment, not a published release.

The output is **estimated comp activity**, not attendance, payroll, confirmed billable time or a measure of artist productivity. Thinking, reference work and conversations can fall outside activity detection.

## Scope authorised for this first change

- [x] Download the upstream source from GitHub into a scratch reference checkout.
- [x] Create the component in the existing frontend repository; now located at `shotbox_frontend/plugins/nuke/nuke_time_calc/`.
- [x] Adapt and rebrand the useful collector logic; remove encryption, standalone reader, UI and hidden activity override.
- [x] Exercise the isolated collector and adapter tests; document what is not live-tested.
- [x] Run an isolated real Nuke 17.1v2 host probe: callback activity, plain records and teardown passed with Qt 6.5.3 (`--safe --tg`, not GUI-menu validation).
- [x] Complete independent review and install the authorised local `shotbox` test menu.
- [x] Verify all 42 tests and separate real GUI startup, menu, callback, save-context, local JSONL and disable checks. Full manual acceptance remains open.
- [x] Save this implementation plan.

No GitHub repository creation, publication, commits or pushes. No edits to existing backend models/API, no production database migrations and no network reporting in this phase. **The user subsequently authorised a local Nuke test installation once the collector passes verification.** Parent installer work may back up and add an isolated, reversible registration to this user's Nuke startup; do not restart existing Nuke processes or affect workers. The first local test uses plain diagnostic output, not live Shotbox ingestion. Existing frontend changes unrelated to this task must remain untouched.

## Starting point

Upstream: `https://github.com/AlesUshakou/TimeTrackerForNuke`

Inspected/downloaded revision: `c15f220568e4925f9f89c11050fcf81d7f9cdc2a`.

Useful upstream concepts are script lifecycle callbacks, a lightweight Qt timer, activity/focus signals and separate rendering observations. The current upstream stores cumulative counters per filename-derived shot in `.enc` files. Its 20-second inactivity rule, wall-clock elapsed timing, filename-only identity and hidden `ales` override must not become the Shotbox design unchanged. Preserve the original notice and a short provenance record without bringing screenshots or the Reader dependencies into the new component.

Existing Shotbox integration points inspected:

| Area | Existing location | Planned use |
|---|---|---|
| Frontend entry point | `main.py` | Later enable/configure the integration |
| Nuke launching | `page_nukedash.py`, `filesIO.py` | Supply verified artist/project/shot context at launch |
| Nuke process detection | `nuke_detector.py` | Corroboration/diagnostics, not authoritative active time |
| Nuke locks | `nuke_lock_worker.py` | Keep lock semantics separate from time records |
| HTTP client | `http_help.py` | Later frontend API reporting/configuration methods |
| Existing plugin installer | `plugins_install.py` | Later managed, reversible installation; do not alter it now |
| Backend model hierarchy | `tasks/models.py` | Reuse User and Job/Timeline/Shot relations |
| Backend routes/serializers | `tasks/urls.py`, `tasks/serializers.py` | Later time ingestion and `working_hours` projection |

The frontend's existing Nuke integration uses Nuke's own Qt binding, not PyQt6 imported into Nuke. Keep that separation. Upstream and local files are reference material; compatibility still needs a real Nuke test.

## Phase 1: isolated stripped collector

### Deliverable

A small namespaced Python component with a testable timing core and Nuke adapter. No HTTP connection yet. A caller supplies explicit context and an output callback receiving JSON-serializable session/interval records. Importing the package must not start tracking, register callbacks or write files.

### Keep

- Start/end and context-change boundaries.
- Save observations without starting a new session for every version.
- Activity/focus and render-state signals as coarse inputs.
- Session UUID, sequence and timestamped records.
- Separate estimated active, idle/unfocused and render categories.

### Remove

- Encryption/decryption and `.enc` storage.
- Tracker windows, menus, reader/export application and storage-folder preferences.
- The `ales` override.
- Persistent lifetime totals keyed only by a stripped filename.
- Global `settings` imports and other module names that may collide with existing plugins.

### Timing rules

- Use a monotonic clock for elapsed durations; UTC for reporting timestamps.
- Settle elapsed time before changing focus, render or session state.
- Split at the inactivity boundary rather than counting a complete delayed tick as active.
- Exclude long missing-observation/suspend gaps and expose them as uncertain rather than claiming continuous work.
- Explicitly configure the inactivity and missing-gap thresholds; record policy/version with observations. Initial values are development defaults, not agreed budgeting rules.
- Do not treat render duration as comp labour. Handle normal completion and cancellation conservatively.
- Do not store raw keystrokes, mouse positions, node changes or screenshots. Activity callbacks only update a last-activity timestamp.
- GUI sessions only; headless/farm work is excluded.
- Keep script/project/artist changes explicit. Do not infer artist from file ownership or a slate.

### First proof

Use deterministic clocks to demonstrate activity -> idle -> focus change -> render -> save -> shot switch -> close. Every interval is counted at most once and assigned to the correct context. A failing output sink cannot silently lose a batch. Any temporary in-memory buffering must be bounded and its crash-loss limitation documented.

## Phase 2: pin the Shotbox ingestion contract

The detailed backend implementation plan is now maintained in
`shotbox_backend/NUKE_TIME_BACKEND_PLAN.md` (sibling backend repository).
It supersedes the preliminary backend sketches below where they differ,
including schema-2 active spans required for overlap resolution, scoped device
authentication, permission-aware reporting and SQLite acceptance gates.

Implement one authenticated ingestion route, proposed as:

`POST /api/time/batches`

A batch identifies the collector/session, unique sequence, workstation, project, optional shot, script/version and observation interval. It contains duration categories and counts of relevant save/render events, not raw interaction history. The backend derives artist identity from authentication rather than trusting an arbitrary submitted username.

Before implementing transport, freeze the payload names against the tested collector output in a versioned contract. Include schema version, collector version and policy version. Duration units are seconds; use a documented precision/rounding policy and round only for presentation.

Required server rules:

- A unique `(session_uuid, sequence)` is accepted once; identical retry returns the original acknowledgement.
- The same key with different content is rejected, not silently overwritten.
- Validate project/shot relationship, actor permissions, timestamp order, duration bounds and schema version.
- Do not add cumulative snapshots together. Derive totals from accepted interval records.
- Multiple Nuke processes/machines for one artist must not double-count overlapping time. Preserve raw observations and apply a deterministic overlap policy before publishing totals. Unresolved cross-shot overlap stays ambiguous, not charged to both shots.
- Missing heartbeats must never extrapolate time indefinitely.
- Preserve history if an artist becomes inactive or a shot is hidden.

### Identity/security prerequisite

The inspected desktop middleware accepts a client-supplied `X-ShotBox-User` identity. This is not authentication. Choose a minimal authenticated device/artist session for the new route. Keep credentials out of `.nk` files, source and logs. Use HTTPS for credentialed reporting. The existing User serializer uses `fields = '__all__'`; replace it with an explicit safe field list in the backend work, preserving the fields the frontend actually needs.

### Acceptance

Backend tests prove duplicate delivery, conflicting payload, wrong artist, wrong project, invalid durations, session overlap and stable aggregation. Use an isolated test database. No production backfill of fabricated historical time.

## Phase 3: related artist totals on Shotbox shots

Expose the requested `working_hours` API property as a list of artist breakdowns:

```text
Shot.working_hours[]
    artist_id
    artist_name
    hours
```

It is a read-only projection of the interval records, not a client-editable JSON list or counter in the shot row. A unique shot/artist aggregate table may be added if necessary, but it must remain rebuildable from the accepted records. Start with a query-based projection unless performance requires a cache.

Use stable User IDs, not artist names, for identity. Store seconds internally. Report a total and an `estimated` label alongside the breakdown if useful. Project totals roll up shot intervals plus explicitly project-only intervals; never count a shot interval twice as both shot work and separate project work.

Reports must distinguish:

- Estimated active comp time.
- Observed session time and render time, separately.
- Distinct local calendar dates with activity.
- Equivalent artist-days based on an explicit configured day length.
- Unknown attribution, missing coverage and collector/policy versions.

### Acceptance

Two artists on one shot each appear once. One artist on several shots rolls up correctly. Renaming an artist retains their history. Retried batches do not change totals. Summing reports reconciles with deduplicated source intervals. Midnight and Europe/London DST boundaries are tested.

## Phase 4: reliable background delivery

Add a small bounded worker using standard-library networking where practical. Nuke/Qt callbacks only capture state; they must never make synchronous HTTP requests. Batch periodically (proposed starting point: about once a minute), and flush pending records at lifecycle boundaries without blocking script closure.

Keep an ordinary local outbox outside VFX project folders. Unacknowledged records are durable application state, not disposable cache: agree their per-user storage location before implementation and ensure Clear Cache cannot erase them. For disposable working files, follow the frontend's `AGENTS.md`: resolve locations through `app_cache` under `shotbox_frontend/.cache` and protect active tasks from cache clearing. Use atomic writes, restrictive permissions, per-session identities, explicit acknowledgements and bounded retry/backoff. No encrypted per-shot files are needed. Do not remove unacknowledged data; surface queue limits and stop claiming coverage if the outbox cannot accept more. Partition queues by server/artist so account switches cannot resend data under the wrong identity.

A collector can send directly to the backend, avoiding a requirement for the desktop Shotbox window to remain open. If direct credentials/configuration prove cumbersome, evaluate a local Shotbox bridge separately; do not implement both transport paths by default.

### Acceptance

A real localhost HTTP test receiver verifies payloads, retries and deduplication. Simulate timeout after server acceptance, server outage, shutdown/restart, authentication failure and account/server changes. Report this as transport validation, not proof of live Shotbox integration.

## Phase 5: managed Nuke setup and frontend reporting

**Local test UI requirement:** create/reuse a top-level Nuke menu named exactly `shotbox`. Put the `nuke_time_calc` test/status and enable/disable access beneath it. Tracking remains automatic; artists do not need to operate timer controls. Preserve any existing menu items and avoid duplicate registrations on reload. This small integration menu replaces, rather than restores, the upstream tracker window.

- Add a reversible installer/configuration entry to Shotbox settings, following existing managed plugin-registration conventions.
- Support launching through Shotbox and reopening recognised scripts directly. Resolve project/shot IDs using explicit launch context and the existing path mapping; validate any persistent context against the current script path.
- Unresolved contexts are not assigned to the last known shot. Record a coverage issue without a blocking popup.
- Add a small project/shot report or expandable working-time breakdown. Avoid putting timer controls in the artist's workflow.
- Provide a quiet integration status/diagnostic view and global enable/disable setting; artists should know what is collected, without repeated prompts.
- Make uninstall remove only this component's registration/configuration, preserving unrelated Nuke startup code.

## Live acceptance gate

Test in the user's actual Nuke version on Linux and Windows before deployment. No automatic production installation as part of this first experiment.

1. Open recognised comp -> correct artist/project/shot.
2. Edit, study a frame, save and version up -> no duplicate sessions or invented idle time.
3. Switch shot and Save As across projects -> context changes correctly.
4. Run/cancel local render -> separate render category; no stuck state.
5. Open another Nuke instance -> overlapping work not double-counted.
6. Close Shotbox while Nuke stays open -> documented delivery behaviour.
7. Disconnect network, sleep/resume, crash/restart -> bounded loss/uncertainty, no overnight growth.
8. Launch Deadline/headless Nuke -> no artist time.
9. Open frontend report -> totals reconcile with accepted API records.

Use captured real output and repeatable tests. Fake Nuke/Qt tests verify adapter logic only; they do not prove callback behaviour or performance inside real Nuke.

## Integration execution plan

This section supersedes preliminary phase sketches above wherever they differ. The authoritative implemented wire contract is `shotbox_backend/docs/NUKE_TIME_API.md`: schema 2, collector `2.0`, policy `comp-v1`, integer microseconds, required real shot/job IDs, exact span-duration equality, operator-only credential provisioning and session-authenticated reporting. Earlier seconds-based, optional-shot and project-only sketches are not the implementation target.

### Pre-implementation baseline

- Local schema-1 diagnostic collector and this user's Nuke bootstrap exist. Recorded validation: 43 collector/adapter/bootstrap tests and real Nuke GUI startup/save/teardown probes passed. Full interactive acceptance remains open.
- Backend ingestion, immutable observations, overlap-aware summaries and read-only admin pages are implemented. Live migration `0040_nuke_time` was separately approved and applied. API enablement remains off; no device credentials have been provisioned.
- No collector-to-API delivery, durable upload queue, authenticated desktop reporting or workstation rollout is complete. Existing diagnostic logs must not be backfilled as real work.
- This is a planning update, not approval to enable production ingestion, issue credentials, modify workstation startup or restart services.

### Milestone 0 — isolate development and freeze boundaries

Inspect current frontend launch/context/authentication/installer code, repository instructions, backend serving process and local changes before editing implementation. Preserve unrelated work and existing local diagnostic startup. Use a separate development checkout for any backend changes that could trigger live auto-reload; tests must use isolated database/media/settings.

Define collector → durable sink → uploader interfaces against the existing API, with no network work in Nuke callbacks. Use explicit configuration for server, credential reference and context resolver. Never place secrets in scripts, launch arguments or logs.

**Gate:** current baseline test results recorded; contract fixtures accepted by the isolated backend; development cannot write to the live database.

### Milestone 1 — schema-2 collector

- Emit coarse half-open active spans and integer microseconds rather than scalar schema-1 seconds. Split/coalesce spans within API window/span limits without truncation or fabrication.
- Map monotonic measurements onto a fixed UTC epoch. Terminate uncertain epochs on clock changes, exclude suspend/stall gaps, and ensure span endpoints and durations agree exactly at microsecond precision.
- Preserve idle/focus/render exclusions, session identity, immutable sequence payloads and lifecycle/context flush boundaries. Start a new session where clock-epoch or ownership changes require one.
- Keep old diagnostic records separate; no conversion that invents active-span positions.

**Gate:** deterministic tests cover activity/idle, render/cancel, delayed timers, forward/backward clock changes, suspend/resume, rounding, batch limits, save/version-up and shot switches. Real emitted records pass the backend validator and overlap tests.

### Milestone 2 — durable outbox and uploader

- Proposed Linux storage: a distinct `outbox/` beneath `~/.local/state/shotbox/nuke_time_calc/`; select the Windows per-user application-state equivalent during implementation. Confirm locations before live installation. Keep it outside project folders and disposable Clear Cache paths.
- Persist immutable batches atomically with restricted permissions before acknowledging the collector sink. Partition by server and authenticated artist/device binding, with safe multi-process ownership and restart recovery.
- Upload from a bounded background worker using TLS validation and finite timeouts. Delete only on explicit `accepted` or `duplicate` acknowledgements.
- Retry transient failures with bounded backoff and Retry-After support. Retain and surface authentication, validation and conflict failures without busy loops or silent deletion. Rotation may reuse the stable device binding; account/server changes must not reattribute queued records.
- Set documented queue bounds and fail closed with a visible coverage warning if persistence is unavailable/full. Document the remaining unsealed-interval crash-loss window rather than promising zero loss.

**Gate:** real isolated HTTP tests prove outage/restart replay, lost acknowledgement after commit, duplicates, conflicting payloads, partial/truncated local writes, disk-write failure, queue saturation, credential rotation and multiple Nuke processes. UI callbacks remain free of network waits.

### Milestone 3 — real Shotbox context and setup

- Supply verified job/shot IDs through the existing Shotbox Nuke launch path; support direct reopening using validated path/context mappings, including Linux/Windows project roots.
- Revalidate on load/save/Save As. Flush the previous context first; unresolved context stops attribution and reports coverage loss without a blocking popup.
- Bind artist identity through a scoped device credential, not the desktop attribution header, OS username or filename. Separate permission/provisioning from collection.
- Preserve headless/farm exclusion and allow uploads while the Shotbox desktop window is closed.

**Gate:** integration tests and real Nuke checks cover launch, direct reopen, version-up, cross-shot/project Save As, unknown scripts and account changes, with no stale-shot attribution or credential exposure.

### Milestone 4 — isolated end-to-end Nuke acceptance

Connect real Nuke to an isolated backend/test artist/test shot first. Exercise editing, idle/focus changes, local render completion/cancellation, concurrent instances, closing Shotbox, network loss, Nuke restart and sleep/resume. Inspect accepted database records and authenticated summary/shot responses.

**Gate:** known activity is attributed correctly; retries leave totals unchanged; same-shot overlap is counted once and cross-shot overlap remains ambiguous. No unattended overnight growth, farm labour or invented historical hours. Assess viewer/navigation/thinking coverage and document the existing policy's undercounting before changing policy defaults.

### Milestone 5 — desktop reporting and managed installation

- Add authenticated user-session access to time summaries; device credentials remain ingestion-only. Do not use `X-ShotBox-User` as report authentication.
- Show per-artist estimated hours in existing shot/project UI, with ambiguity, unavailable-report and missing-coverage states distinct from zero. Keep budgets, tasks, locks and activity history unchanged.
- Add reversible managed installation, quiet status/error/outbox diagnostics and enable/disable configuration. Preserve unrelated Nuke startup/menu registrations; avoid duplicate collectors.
- No timer buttons, approval workflow, standalone reader or new dashboard framework.

**Gate:** artist versus privileged-report permissions pass; displayed totals reconcile with API records; disabled/unavailable states are honest; install/reload/uninstall preserve existing integrations. Validate both Linux and Windows before wider rollout.

### Webpage addition — per-shot artist-hours chart

Confirmed scope: add a compact donut chart beside each shot on the Shotbox webpage, with credited estimated comp hours split by artist. This is a web UI deliverable, not just the desktop reporting work above. Inspect the actual web page/template and its authentication/data-loading path before implementation; do not assume the desktop UI is the webpage.

First-version display:

- Total credited estimated comp hours in the donut centre.
- Visible artist-name/hour legend beside the donut; artist percentages in tooltips. Use consistent artist colours across shots, with text labels so colour and hover are not the only ways to read the values.
- Label the component **Estimated comp time**. It is not attendance, total labour, billable time or a productivity score.
- Optional separate budget-usage bar when a shot budget exists. Do not mix remaining budget into the artist-share donut or modify the stored budget. Use the same authorized reporting scope for comparisons; do not present one artist's hours as whole-shot budget usage.
- Small coverage indicator for missing observations and a separate ambiguity warning. Ambiguous cross-shot overlap is excluded from the credited total; never build the chart from raw overlapping durations.
- Clicking/expanding the chart shows artist credited hours, raw versus credited time where relevant, coverage/policy information and date-range filtering through the existing authenticated summary contract.

Explicit states:

- No observations: **No time recorded**, not a misleading zero-hour chart.
- Observations with zero credited time: show the zero/ambiguity or coverage explanation distinctly from no observations. If the current API cannot distinguish these states, add a minimal tested response field rather than guessing from the hours value.
- Unavailable/disabled/failed reporting: **Time unavailable**, not zero.
- One contributing artist: single-colour ring with readable name and hours.
- Whole-team breakdown requires authorized report access. An own-artist-only response is labelled **Your recorded time** and must not appear to be the shot total. Preserve server-side permissions and do not fetch unauthorized artist data merely to hide it in the browser.

Implementation should reuse existing shot-page layout and charting dependencies where suitable, avoid a new dashboard framework, batch page-level data loading where practical and respect report-size limits without showing partial totals as complete. Provide a readable text fallback and a compact responsive layout.

**Gate:** authenticated webpage renders real accepted test records; artist slices and displayed total reconcile with credited API microseconds before display rounding; colours remain consistent; budget bar uses the correct scope; expansion/date filters work; no-data, zero-credit, unavailable, ambiguity and permission-limited cases are tested. Verify rendered desktop and narrow layouts. Mock charts alone do not establish live integration.

Deferred: a daily stacked artist-hours chart showing when work occurred. Daily/calendar grouping is not currently implemented; add and test backend grouping, including Europe/London day/DST boundaries, before offering this chart. Do not fabricate daily allocation from cumulative totals.

### Milestone 6 — approved single-workstation pilot, then rollout

Before live enablement, explicitly approve the deployment target and scope; verify backup/recovery, trusted HTTPS/proxy configuration, request/rate limits and provisioning permissions. Issue one artist/device credential securely and enable only the approved pilot integration. Read back exact accepted records and reports before claiming live success.

Resolve the documented historical job-attribution limitation before relying on project-budget history: current reports follow the shot's current hierarchy. Choose to prohibit relevant reparenting or implement historical ownership snapshots as a separately tested change.

**Gate:** pilot passes the same end-to-end checks with no duplicate/misattributed records, acceptable coverage and bounded overhead. Expand to other workstations only after review. Operational rollback disables ingestion/collection and preserves queued and accepted observations; do not reverse a data-bearing migration or delete unacknowledged records.

### Windows automatic installation and rollout — planned, not implemented

**Confirmed requirement:** Shotbox automatically installs and maintains the Nuke time plugin on supported Windows workstations. Artists do not copy plugin files, edit `menu.py`, choose credential paths, click an Install button or enable tracking. This extends the existing automatic Linux workflow; it is not a separate manual Windows installer.

#### 1. Establish the Windows pilot and storage boundary

- [ ] Inspect one approved Windows workstation's Shotbox installation, Nuke version/Python/Qt, actual per-user Nuke startup location and project-drive mappings. Do not assume the Linux `.nuke` location or a particular Windows account path.
- [ ] Confirm whether Shotbox `.cache` is workstation-local, network-mounted or synchronized before choosing the queue location. Retain the requested `.cache/nuke-time` layout and Clear Cache protection; do not silently relocate state or claim an SMB/shared SQLite queue is safe. Resolve any deployment-layout conflict before rollout.
- [ ] Keep cache namespaces separate by Windows user and workstation, with additional immutable server/artist/device binding. Never distribute another user's configuration, credentials or pending queue with the plugin/update package.

**Gate:** record the actual pilot paths and filesystem constraints, without issuing production credentials or altering the Windows workstation during planning.

#### 2. Add platform-specific private durable storage

- [ ] Introduce a small storage boundary shared by automatic setup, managed config writes, credential reads and the outbox. Preserve the working POSIX implementation; implement and test Windows file permissions/ACLs, handle safety, atomic replacement and persistence separately.
- [ ] Replace assumptions about `getuid`, POSIX mode bits, `O_NOFOLLOW`, `O_DIRECTORY` and directory fsync with checked Windows equivalents or an explicitly tested Windows durability policy. Do not merely remove the unsupported-platform checks.
- [ ] Reject unsafe credential access, links/junctions or other reparse-point redirection and hard-link substitution according to the selected Windows policy. Test multiple Nuke instances sharing the intended local queue without rebinding or duplicate credit.
- [ ] Retain pending records through process crashes, interrupted writes, outages, disk-full/queue-full conditions and application updates. Clear Cache must preserve tracking state even when Nuke is closed.

**Gate:** native Windows tests pass for private credentials, committed-record recovery, concurrent queue access, lost-ack replay and corruption handling. Any weaker power-loss guarantee must be documented rather than described as POSIX-equivalent without evidence.

#### 3. Make Shotbox install and maintain Nuke startup automatically

- [ ] On Shotbox launch, reuse the normal server and linked artist to run background setup, create/reuse a private device identity, enroll its write-only credential and automatically install the managed Nuke startup block in the correct user's profile.
- [ ] Create the required startup directory when appropriate; handle a first-run user profile without requiring a prior manual plugin installation. Detect a non-writable startup location and expose a quiet actionable status, without elevated installation or blocking normal Shotbox/Nuke use.
- [ ] Repeated launches and Shotbox updates must repair/update only the managed registration, resolve the current installed plugin source path, and preserve unrelated Nuke startup bytes. Avoid duplicate collectors and conflicting old diagnostic registrations.
- [ ] Keep Nuke startup lightweight and GUI-only. If enrollment/configuration is not ready when Nuke starts, retry automatically; opening a recognised shot must not require clicking Enable or reopening a settings panel.
- [ ] Package required runtime code with Shotbox and verify imports in Nuke's shipped Python/Qt. Do not run package installation or rely on Shotbox's PyQt environment from Nuke startup.
- [ ] Retain the agreed 30-second Nuke timer, roughly one-minute checkpoints and lifecycle flushes. Device credentials remain ingestion-only, and uploads continue when the Shotbox window closes.

**Gate:** a clean Windows user profile goes from starting Shotbox with its existing server/artist link to a managed plugin in the next Nuke session, with zero Nuke-specific setup actions. Repeating setup/updates preserves identity, existing startup and queued data.

#### 4. Verify Windows shot attribution and identity switches

- [ ] Exercise actual Windows drive-letter and UNC paths, separator/case handling, version-up and direct reopen using verified server job/shot mappings. Do not guess a shot from a basename or assume that mapping a script location also maps its internal Read paths.
- [ ] Preserve source-server/artist snapshot provenance across queued API results and shot launches. Switching server or artist must isolate credentials/outboxes and refuse stale mappings without breaking the normal OS-associated Nuke launch.
- [ ] Unknown, ambiguous or inaccessible scripts pause attribution rather than charging the previous shot. Headless/farm Nuke must not record artist labour.

**Gate:** native Windows tests plus real Nuke checks demonstrate correct shot/artist binding, no cross-account queue reuse and no stale-shot charges.

#### 5. Run one real Windows pilot before the team rollout

- [ ] Start with one approved Windows workstation and artist. Verify automatic installation and enabled collection in the real Nuke GUI, not just mocked host tests or successful enrollment.
- [ ] Perform real activity on a verified pilot shot; read back its accepted batches and credited webpage artist/hour totals. Keep synthetic test observations out of production reports.
- [ ] Check save/load/close, two Nuke instances, focus/idle, foreground render completion/cancellation, outage/restart, sleep/resume and Shotbox-closed delivery. Check ordinary Shotbox shutdown separately from collector correctness.
- [ ] Confirm an update and repeat launch do not reinstall duplicate startup blocks, reset credentials or discard pending records. Keep Linux regressions green.
- [ ] Only then mark Windows supported and roll the automatic installation out through the normal Shotbox update mechanism. Preserve an operational rollback that leaves accepted and unsent records intact.

**Release gate:** verified native Windows storage + clean-profile automatic install + real Nuke collection/upload + correctly attributed webpage totals. Until all pass, Windows remains explicitly unsupported; pushing the current Linux build is not a completed Windows rollout.

### Next executable slice

Implement and prove the Windows private-storage boundary first, then wire the existing automatic setup to Windows startup discovery and run one clean-profile Nuke pilot. Automatic plugin installation is mandatory; a manual installer or settings panel does not satisfy this work. Do not broaden the team rollout before the Windows release gate above passes. Native desktop hour charts remain separate follow-up work.

Do not build a new dashboard framework, standalone reader, approval workflow, detailed interaction database or background service fleet for this experiment.
