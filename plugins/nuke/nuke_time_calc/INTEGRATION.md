# Shotbox Nuke time integration

## Deployment boundary

Implementation is opt-in. No production credentials were issued, ingestion was
not enabled, and no user/worker startup was installed by this development task.
The original local diagnostic installation remains separate and must not be
mistaken for live Shotbox reporting. Do not import old schema-1 logs as hours.

The reviewed webpage charts, summary presentation and expected-binding checks
are now installed in the serving backend with the user's approval. Time
collection remains disabled; no production credentials or Nuke startup were
installed. The separate development worktree remains available for further
pilot work. `PLAN.md` records locations and outstanding acceptance gates.

## Components

- `core.Collector(..., schema_version=2)` emits exact backend batch dictionaries,
  not envelopes. Numeric `job_id`, `shot_id`, script path and caller identity are
  explicit; artist authority is the scoped device credential at the server.
- `Outbox(root, server=..., artist_id=..., device_id=...)` accepts `.put(batch)`
  only after a locally committed transaction; `.pending()` and `.status()` expose
  durable backlog. Each binding gets its own partition. Immutable digests remain
  after acknowledgement, so changed payloads cannot silently reuse a key.
- `Uploader(outbox, token_file=...)` starts only via `.start()` or `.run_once()`.
  `.stop(timeout=...)` returns whether its thread has stopped. An in-flight
  request may finish after a zero-wait stop; queued data is never deleted merely
  because the worker was stopped. Only exact accepted/duplicate acknowledgements
  remove queued payloads.
- `ContextResolver(mappings)` resolves approved shot-folder mappings only.
  `runtime.BoundResolver` reloads mappings at script lifecycle boundaries,
  rejects changed server/artist/device/storage bindings, and checks physical
  paths for direct-open symlink misattribution.
- `runtime.register(config_path=...)` creates quiet status/enable/disable menu
  actions under `shotbox → nuke_time_calc`. Unknown scripts wait for a recognised
  load/save; headless/farm startup performs no collection. A pre-existing local
  diagnostic collector is not replaced automatically.
- Desktop Settings → Nuke time reporting provides explicit Save and reversible
  managed startup installation. Merely opening settings changes nothing. These
  settings apply to new Nuke sessions; session disable is available inside Nuke.

## Private setup for an approved Linux pilot

An operator must first establish trusted HTTPS, approve the deployment and
provision one write-only device credential for the intended active artist with
`tasks.ingest_nuke_time`. Reporting uses normal Django login/session permissions,
not this device token and not `X-ShotBox-User`.

The operator supplies the artist's numeric ID, stable device UUID and private
credential file path. The UI never reads/displays the token. Select a durable
local outbox directory outside VFX folders and `.cache`; do not put live SQLite
queues on network/synchronised storage. The outbox and all its ancestor
locations must be controlled by trusted users; pathname symlink checks are not
a defence against an attacker concurrently replacing parent directories.
Save while disabled, review the binding,
then enable and install startup explicitly only for the approved workstation.

Default non-secret configuration is
`~/.local/state/shotbox/nuke_time_calc/config.json` (or `XDG_STATE_HOME`). Its keys:

```text
enabled: boolean (default false)
server: HTTPS origin, without /api/, userinfo, query or fragment
artist_id: positive integer
device_id: canonical UUID
token_file: absolute path to operator-issued private selector.secret file
outbox_dir: absolute path to private durable local directory
mappings: [{job_id: positive integer, shot_id: positive integer, base_path: absolute shot folder}]
```

Launching a recognised shot from Shotbox records a verified mapping before the
normal OS-associated launch. Version-up stays within that shot's folder; unknown
or ambiguous contexts do not inherit the last shot. Existing paths use explicit
Linux/Windows project-root aliases, not basename or workstation-name guessing.
Account/server changes require disabling/saving first and a fresh outbox;
existing queues remain bound to the old identity. Rotation of the credential
file for the same device is reread at each attempt.

## Timing and failure policy

`comp-v1` retains the initial estimator's 20-second activity allowance and
conservative focus/render exclusions. Monotonic measurements map to a fixed UTC
epoch. Clock corrections outside tolerance and missing-observation/suspend gaps
end the epoch and require fresh activity; no overnight labour is extrapolated.
Active spans use integer microseconds with exact endpoint/duration agreement.
They are coarse estimated-active ranges, not key/mouse/node event history.

Default outbox bounds: 10,000 pending records and 16 MiB SQLite database.
Acknowledged digest tombstones count toward storage and are not automatically
pruned. The rollback journal may temporarily need roughly another database-sized
allocation. Full/corrupt/unwritable state fails closed and retains preceding
records; status reports the issue rather than quietly dropping oldest work.
The collector can additionally retain up to 128 sealed records and a bounded
unsealed interval in memory. Those not committed locally can be lost in a crash.

The worker rejects plain LAN HTTP, redirects, untrusted TLS, ambient proxies,
public/symlink credential files and malformed acknowledgements. The explicit
literal-loopback HTTP allowance exists only for isolated tests. Retries are
bounded with backoff; permanent auth/configuration failures remain visible.
Socket timeouts do not prove a platform DNS call can be killed on demand.

## Web reporting

The backend job webpage shows per-shot credited-hour donuts, consistent artist
colours, readable artist/hour legends, optional whole-team budget usage, overlap
ambiguity and coverage warnings. Expand for raw-versus-credited values and UTC
range filtering. Personal scope says **Your recorded time**; unauthorized team
data is not supplied. No records, zero credit and unavailable reports are
separate states. Daily calendar charts and native desktop hour charts are not
implemented. The desktop provides setup; authenticated breakdowns are on the
webpage.

## Verified and still outstanding

Automated suites cover timing, local persistence, independent-process writes,
crash rollback, network outages/lost acknowledgements, exact replay, TLS rejection,
permissions, webpage filtering/rendering, settings and installation isolation.
The backend cross-component test exercises the real collector → outbox → HTTP
uploader → accepted database record → session-authenticated chart path.

An opt-in Nuke 17.1v2 / Qt 6.5.3 `--safe --tg` probe exercised actual node and save
callbacks, positive span collection, durable writes, HTTP acknowledgements,
read-back credited totals and teardown. It is terminal/Qt host validation,
**not** proof of normal GUI editing/focus/render-cancellation behaviour.

Outstanding rollout gates:

- Independent-review blockers are closed; recorded scope verdicts are in the
  backend worktree's `docs/validation/review.json`. This is development acceptance,
  not approval to enable the production pilot.
- Trusted production HTTPS/proxy limits, explicit pilot enablement and provisioning.
- Real GUI editing, viewer/navigation coverage, render completion/cancellation,
  concurrent instances, sleep/resume and workstation overhead acceptance.
- **Windows durable storage is not implemented:** current outbox fails closed on
  non-POSIX platforms. Do not enable Windows reporting until private ACL/file
  durability support and real Windows Nuke acceptance are completed.
- Historical project totals follow the shot's current hierarchy. Decide whether
  to prohibit reparenting or implement ownership snapshots before relying on
  historical project-budget attribution.

Rollback disables ingestion/collection and preserves queued and accepted data;
do not reverse the data-bearing migration or delete unacknowledged queues.
