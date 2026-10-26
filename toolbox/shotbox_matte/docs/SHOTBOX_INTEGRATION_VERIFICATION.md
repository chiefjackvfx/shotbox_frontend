# Shotbox integration verification

Verified on 2026-10-26T13:23:54+00:00 (Europe/London).

The authoritative application is `toolbox/shotbox_matte/`; the Nuke host
integration is `plugins/nuke/`. Model weights remain external. This workstation's
shared configuration points to the newly prepared Toolbox `.venv` and the
existing `Shotbox_alpha/models` library. No model weights were copied.

## Automated checks

- Relocated Matte suite: **746 passed, 13 subtests passed**.
- Frontend integration, shutdown, installer, cache and autosave group:
  **54 passed, 4 subtests passed** on Python 3.11.
- Main-window group: **37 passed**; settings/startup group: **16 passed**.
- Ruff checks pass for the application/backend, Nuke package and new adapters.
- An initial combined frontend Qt test process segfaulted. The recorded frontend
  groups pass in isolated processes; a single-process full-suite run is not certified.

## Real Linux acceptance

Frontend Python 3.11.13; worker Python 3.12.14; PyTorch CUDA 13.0;
NVIDIA GeForce RTX 5090; commercial Nuke 17.1v2.

- The relocated UI launched its configured worker and generated three 16×16 RGB
  grayscale EXRs from synthetic EXRs in **33.939 seconds**. Output channels,
  dimensions and finite pixel values passed inspection; source hashes matched.
- The UI processed **684 timer callbacks** while generation ran. Cancelling a
  second real worker retained the completed export.
- Fresh headless Nuke startup loaded the managed integration from `plugins/nuke`
  without importing PyQt6 or the Matte backend.
- Nuke's native synthetic alpha capture/publication/cached-render/reopen checks passed.
- A separate offscreen Nuke GUI used the relocated controller and real GPU worker
  to publish three alpha-only EXRs and install a standalone Read. A subsequent
  cancellation retained that publication. Both phases passed in **25.175 seconds**.
- The user startup file was backed up, its recognized old Matte registration was
  migrated, and external model configuration was preserved.

Acceptance uses tiny synthetic inputs to check integration mechanics, not matte
quality. Windows was not exercised. Restart existing Shotbox and Nuke sessions
to load the changed sources. No versions were bumped and no release was made.

Transient test media, raw logs and machine-readable acceptance records live under
frontend `.cache/temp/` and may be removed through Settings → Cache when idle.

## Compact Precomp Roto workflow

Verified on 2026-10-26 (Europe/London), including the editable Seed control
defaulting to 42. Right-click Precomp → folder → Roto selects the same sequence
as the menu preview and opens the non-modal generation dialog.

- Matte suite on its supported Python 3.12 runtime: **797 passed, 25 subtests passed**.
- Frontend Roto, Matte integration/shutdown, Toolbox, EXR menu and cache tests:
  **68 passed** on frontend Python 3.11.
- Ruff and whitespace checks pass for the changed Matte modules and tests.
- Real EXR integration with controlled inference verifies 77 selected frames
  become 81 model frames and exactly 77 exported mattes, preserving source
  dimensions and numbering. An intervening output reservation advances the
  export version without altering the occupied version.
- Normal 640×660 and compact 460×420 layouts were inspected offscreen with the
  frontend theme. Long paths wrap, content scrolls and footer actions remain
  accessible. The compact layout also has automated regression coverage.
- A real GPU worker launched by clicking **Generate Now** exported three 16×16
  RGB grayscale EXRs in **22.675 seconds**, with **906 UI timer callbacks** during processing.
  Output dimensions, finite pixels, equal RGB channels and unchanged source
  hashes passed inspection. It used the existing external model library.

Cancellation, forced termination, failed startup, worker errors/crashes,
stale plans, setup errors, popup reuse and cache protection have controlled-worker
coverage. This tiny synthetic GPU check establishes integration mechanics;
production matte quality and Windows execution were not evaluated here.

The 16 compact Roto tests now exercise Generate Now's button signal for success,
failure, cancellation and forced termination. The success regression captures
unhandled Qt slot exceptions and verifies the selected seed reaches the worker.
It reproduced the `clicked(bool)` argument reaching the parameterless action
through the cache decorator before the button connection was corrected.
