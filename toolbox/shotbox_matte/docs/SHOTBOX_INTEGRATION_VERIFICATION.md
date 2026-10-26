# Shared Shotbox / Matte Python 3.12 integration

Verified on 2026-10-26 (Europe/London). Matte now uses frontend `venv` for both
its host UI and background generation. Standalone launchers, private runtime
preparation and portable EXE packaging have been removed. Existing model
libraries, old private environments and the legacy parent environment are
preserved.

## Setup and migration

- Ran the actual frontend `setup_runtime.sh` from a fresh canonical environment.
  It provisioned local Python **3.12.14** under `.runtime/tools/python`, created
  frontend `venv` and installed the combined **78-package** application stack.
  Import/version checks passed, including OpenImageIO and CUDA 13 PyTorch.
- Dependency resolution passed for Windows x86_64 (60 packages), Linux x86_64
  glibc ≥2.34 (78), and macOS ARM (20, without inference/CUDA packages).
- A disposable real Python 3.11 environment migrated at its final path to
  **3.12.14**, retained an inactive rollback backup, and was reused on another
  setup run. Its activation script contains the final environment path.
- A second real migration deliberately failed during dependency installation.
  The original **Python 3.11.13** environment and its marker were restored at
  the original path. The shared parent environment's configuration was unchanged.
- Controlled migration tests additionally exercise interrupted recovery,
  concurrent setup exclusion, process identity, child termination before
  rollback, symlinks/junctions, download cache protection and paths with spaces.
- Main environment compatibility check passed after installing test tools:
  **82 packages checked; all installed packages are compatible**.

## Generation and application boundaries

- Actual Precomp/Roto button generation used the same executable as the host:
  `shotbox_frontend/venv/bin/python` (**Python 3.12.14**).
- The real NVIDIA GPU worker exported three 16×16 RGB grayscale EXRs in
  **22.409 seconds**, with **924 UI timer callbacks** during processing.
  Dimensions, finite pixels, equal RGB channels and unchanged source hashes
  passed inspection. A second actual button-launched worker was cancelled;
  the completed output remained intact.
- Native model files were read from the existing external library. No model
  weights were downloaded or copied.
- Saved managed Matte interpreter paths migrate to the frontend default.
  Explicit custom Nuke overrides, external model paths and unrelated settings
  are preserved. Nuke's preference/default lookup remains free of frontend Qt
  and inference imports.

## Automated validation

- Full remaining Matte/backend suite: **773 passed, 25 subtests passed**.
- Focused frontend setup, integration, Roto, shutdown, cache, updater and plugin
  group: **109 passed** on the canonical Python 3.12 environment.
- Broader frontend coverage ran all **52 test modules in separate processes**:
  **744 passed, 141 subtests passed**, with no crashes or timeouts. One existing
  test expects the username `Jack` and fails for this machine's `gambit` user;
  the identical failure was reproduced on unchanged HEAD.
- A single-process full frontend run still segfaults during combined Qt tests,
  as previously observed before this integration; that execution mode is not
  certified. The isolated module results above cover the full frontend suite.
- Ruff passed for the new setup code, Matte package and Nuke preferences;
  shell syntax and Git whitespace checks passed. Broader frontend lint still
  reports existing style findings outside those checked modules.

Windows/macOS dependency resolution and controlled platform branches were
checked; actual Windows/macOS installation and native generation were not run.
Tiny synthetic GPU inputs establish integration mechanics, not production matte
quality. Transient acceptance scripts, EXRs, resolver manifests and JSON reports
are under frontend `.cache/temp` and can be cleared when no jobs are active.

---

The following sections are historical records of the earlier split-runtime
implementation. They describe the software as it existed at the time and are
not current setup instructions.

# Earlier Shotbox integration verification

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
