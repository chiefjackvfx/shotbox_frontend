# Shotbox Frontend — Freeze & Hang Analysis

**Date:** 2026-05-11  
**Scope:** All Python source files in `shotbox_frontend/`

---

## Root Cause Summary

The application freezes because **blocking operations (network calls, file I/O, subprocess calls) run directly on the Qt main thread**, preventing the event loop from processing repaints, input events, or any other UI work during those calls. The codebase already uses correct async patterns in several places — the problem areas simply need the same treatment.

---

## Issues Found

### CRITICAL — Three lock timers make blocking API calls every 45 seconds

**File:** [page_nukedash.py](page_nukedash.py)  
**Timer setup:** Lines 473–483 (`_lock_interval_ms = 45_000`)

Three `QTimer` slots fire every 45 seconds and each makes synchronous HTTP calls on the **main thread**:

| Timer | Slot | Blocking call | Line |
|---|---|---|---|
| `_lock_sync_timer` | `_on_lock_state_sync_tick()` | `api.get_job()` — HTTP GET | [1708](page_nukedash.py#L1708) |
| `_lock_detector_timer` | `_on_lock_detector_tick()` | `nuke_detector.detect_open_nuke_scripts()` (process enumeration) then `api.update_shot_lock()` in a loop | [1759](page_nukedash.py#L1759), [1779](page_nukedash.py#L1779) |
| `_lock_heartbeat_timer` | `_on_lock_heartbeat_tick()` | `api.update_shot_lock()` in a loop over all owned locks | [1790](page_nukedash.py#L1790) |

All three share the same 45-second interval and are not staggered, so they can fire simultaneously — tripling the freeze duration. Each freezes the UI for the full server round-trip. If the server is slow or unreachable and retry backoff is active, the freeze can last several seconds per timer.

---

### HIGH — File polling makes blocking file I/O + an API call every second

**File:** [page_nukedash.py](page_nukedash.py) — Lines [571–609](page_nukedash.py#L571)  
**Timer interval:** 1000 ms ([line 467](page_nukedash.py#L467))

`_poll_visible_shot_card_file_state_batch()` runs every second on the main thread and calls:

1. `filesIO.scan_shot_file_state(shot_dir)` ([line 594](page_nukedash.py#L594)) — which in turn runs `glob()`, `Path.stat()`, and directory walks (see [filesIO.py:255–287](filesIO.py#L255)). On network-mounted storage or a busy filesystem, each scan can block for hundreds of milliseconds.
2. `self._shot_card_api.update_shot(...)` ([line 607](page_nukedash.py#L607)) — a blocking HTTP PATCH call that fires whenever a preview video path changes.

This is the **most likely cause of constant micro-stuttering** during normal use because it runs continuously, not just on user actions.

---

### HIGH — Filter dropdowns populated with a blocking `get_users()` API call

**File:** [page_nukedash.py](page_nukedash.py) — Line [1815](page_nukedash.py#L1815)  
**Function:** `_populate_filter_dropdowns()`

Called on the main thread after job data loads. Makes a synchronous `api.get_users()` HTTP GET to fill the artist filter dropdown. Freezes the UI until the server responds.

---

### HIGH — "Open Nuke" button freezes UI with two sequential API calls

**File:** [page_nukedash.py](page_nukedash.py) — Lines [1619](page_nukedash.py#L1619), [1665](page_nukedash.py#L1665)  
**Function:** `_handle_nuke_open_request()`

When the user clicks the Open Nuke button, two blocking HTTP calls run back-to-back on the main thread:

1. `api.get_shot(shot_id)` — refreshes lock state (line 1619)
2. `api.update_shot_lock(shot_id, ...)` — claims the lock (line 1665)

The UI is completely frozen between button press and Nuke launching.

---

### HIGH — "Check for Updates" runs multiple blocking `subprocess.run()` git commands

**File:** [settings.py](settings.py) — Lines [1286](settings.py#L1286), [1294](settings.py#L1294)  
**File:** [app_update.py](app_update.py) — Lines [84–101](app_update.py#L84), [233–261](app_update.py#L233)

`_on_check_for_updates()` and `_on_update_and_restart()` call `app_update.check_for_updates()` directly on the main thread. That function runs **4–5 sequential `subprocess.run(["git", ...], timeout=30)` calls**. Each can block up to 30 seconds if git is slow or the network is congested.

A `WaitCursor` is applied, but the UI is still fully frozen — no repaints, no cancel button, no window drag. Worst-case total freeze: **~2 minutes**.

---

### MEDIUM — `time.sleep()` in HTTP retry logic blocks whatever thread calls it

**File:** [http_help.py](http_help.py) — Line [207](http_help.py#L207)  
**Function:** `_sleep_before_retry()`

```python
time.sleep(delay)  # called on the calling thread
```

For any API call currently made from the main thread (lock timers, file polling slot, Open Nuke handler), a failed request with retry backoff will also `sleep()` on the main thread, multiplying the freeze duration on network errors.

---

## What Is Already Done Correctly

These patterns in the codebase are correct and should be used as the template for all fixes:

| File | Pattern | Notes |
|---|---|---|
| [activity_page.py](activity_page.py) | `ActivityApiWorker(QObject)` in `QThread`, signals back results | Ideal template for lock timer workers |
| [duration_updater.py](duration_updater.py) | `DurationUpdaterWorker(QThread)`, all file I/O + API in `run()`, `finished` signal | Ideal template for file polling worker |
| [image_loader.py](image_loader.py) | `QNetworkAccessManager` for async HTTP image loading | Already fully non-blocking |
| [page_nukedash.py](page_nukedash.py) ~line 137 | `ChunkedJobLoader` uses `QTimer(0)` + time budgets | Correct chunking pattern for CPU-heavy work |

---

## Fix Plan

### Fix 1 — Move all three lock timers into a `QThread` worker (CRITICAL)

**Template:** `activity_page.py` → `ActivityApiWorker`

Create a `LockWorker(QObject)` that lives in a `QThread`. Move the logic from `_on_lock_state_sync_tick`, `_on_lock_detector_tick`, and `_on_lock_heartbeat_tick` into worker slots. The worker emits signals with parsed results; the main thread applies lock state via the existing `_apply_shot_lock_value()` / `_apply_shot_lock_payload()` methods (which only touch UI widgets and are safe on the main thread).

Key design points:
- The three timers can stay on the main thread as trigger signals, or move into the worker with their own `QTimer`s — either works, worker-side is cleaner
- Stagger timer starts by a few hundred milliseconds to avoid simultaneous API calls
- `_owned_locks_by_shot_id` must either be owned exclusively by the worker thread, or protected with a `QMutex` if the main thread still reads it

**Files:** [page_nukedash.py](page_nukedash.py) lines 441–483, 1704–1800

---

### Fix 2 — Move file-state polling into a `QThreadPool` worker

**Template:** `QRunnable` + `QThreadPool`

Keep the 1-second `QTimer` on the main thread to *trigger* work. Each tick collects the list of visible `(shot_card_id, shot_dir)` pairs (fast, main-thread safe), submits a `ShotFileStateRunnable` to `QThreadPool`. The runnable does the `scan_shot_file_state()` and emits a queued signal with results back to the main thread. The main thread slot calls `apply_file_state_snapshot()` and, if the preview changed, the `update_shot()` API call (or that can also stay in the runnable).

Use a guard flag to skip enqueueing if the previous batch hasn't finished yet.

**Files:** [page_nukedash.py](page_nukedash.py) lines 571–609, 463–468

---

### Fix 3 — Load filter dropdown users asynchronously

Use a one-shot `QThread` (or the existing `ApiWorker` pattern already in `page_nukedash.py` around line 237). Call `api.get_users()` in the worker, emit a `users_ready` signal, populate the `comboBox_sort_artist` widget in the signal handler on the main thread.

**Files:** [page_nukedash.py](page_nukedash.py) lines 1802–1831

---

### Fix 4 — Make "Open Nuke" non-blocking

Wrap the two API calls in `_handle_nuke_open_request()` in a short-lived `QThread` worker. Show `QApplication.setOverrideCursor(WaitCursor)` when the worker starts, restore it in the `finished` signal. Any `QMessageBox` dialogs (conflict/already-locked warnings) must be shown from the main thread signal handler, not from the worker.

**Files:** [page_nukedash.py](page_nukedash.py) lines 1611–1680

---

### Fix 5 — Run `check_for_updates()` in a background thread

Create an `UpdateCheckerWorker(QThread)` with a `finished(UpdateStatus)` signal. Call `app_update.check_for_updates()` inside `run()`. In `_on_check_for_updates()` and `_on_update_and_restart()`, start the worker, disable the button (prevent double-clicks), keep `WaitCursor` active until the signal fires, then call `_apply_update_status()` from the signal handler.

**Files:** [settings.py](settings.py) lines 1283–1298

---

### Fix 6 — Prevent `time.sleep()` from ever running on the main thread

Once Fixes 1 and 2 are complete, all the blocking API calls will be on worker threads where `time.sleep()` is acceptable. As a safety net: set `retry_backoff=0` on any `DjangoAPI` instance that is still called directly from the main thread, or add an assertion in `_sleep_before_retry()` that it is never called from the Qt main thread (`QThread.currentThread() is not QCoreApplication.instance().thread()`).

**Files:** [http_help.py](http_help.py) line 207, or construction sites of `DjangoAPI` in [page_nukedash.py](page_nukedash.py)

---

## Suggested Fix Order

1. **Fix 2** (file polling, 1-second timer) — biggest day-to-day impact, most noticeable stutter
2. **Fix 1** (lock timers) — 45-second periodic hard freezes, CRITICAL severity
3. **Fix 4** (Open Nuke) — user-visible freeze on a common action
4. **Fix 3** (filter dropdowns) — one-time freeze on load
5. **Fix 5** (check for updates) — infrequent but very long freeze
6. **Fix 6** (sleep guard) — safety net, low effort after the above

---

## Verification Checklist

After each fix:

- [ ] Launch shotbox; watch `htop` — main thread CPU should not spike during idle
- [ ] Keep the app open for 2+ minutes; confirm no freeze spikes every 45 seconds
- [ ] Click "Open Nuke" — window should remain draggable and repaint during the call
- [ ] Open Settings → "Check for Updates" — UI stays responsive; button shows loading state
- [ ] On network-mounted shot storage, open the dashboard and confirm no micro-stutters
- [ ] Confirm lock state indicators still update correctly after workers are moved off-thread
- [ ] Simulate server timeout/offline — confirm UI does not freeze on retry backoff
