# Template denoise verification

The current workflow uses `template_denoise_v01.nk` and original source media.
Saved-comp denoising and its legacy acceptance/benchmark scripts have been removed.
Shared native startup and lifecycle helpers live in `test/denoise_test_support.py`.

## Native template acceptance

From the frontend folder on Linux:

```bash
venv/bin/python test/denoise_template_acceptance.py --nuke /opt/Nuke17.1v2/Nuke17.1
```

On Windows:

```powershell
.\venv\Scripts\python.exe test\denoise_windows_acceptance.py --nuke "C:\Program Files\Nuke17.1v2\Nuke17.1.exe"
```

Prerequisites: licensed NukeX **17.1v2**, Neat Video **6.1.2** (Windows also accepts
**6.1.3**), supplied template
and `test/noise_test.exr`, frontend dependencies including OpenEXR/Pillow/NumPy,
and ffmpeg on PATH. The Windows command never discovers or substitutes 17.0.
The Windows wrapper checks import, version, NukeX mode and plugin loading first.

The template harness writes only under `.cache/temp`, without database writes:

- Supplied 8192 × 4320 EXR: fresh Auto Profile → Apply in memory, exactly one
  4096 × 2160 EXR at 1001, finite pixels and no generated `.nk` in the outputs.
- Profiled script saved only as the worker cache file `profiled.nk`; a fresh Nuke
  process reopens that file and renders it with the stored profile.
- Template/source hashes unchanged; profiling is freshly confirmed before rendering.
- OCIO preserved and Write1 uses scene-linear EXR output.
- Below-limit/exactly-4096 stills, width-only portrait handling, above-limit
  non-square pixels, explicit and detected PNG sequences.
- H.264 MP4 at 24000/1001 FPS and ProRes MOV at 25 FPS, detected frame counts.
- Two successful queued plates with an unreadable clip between them, overlap
  rejection, cancellation during profiling/rendering, supervisor failure and shutdown.

Failures print Nuke logs and save `verification.json` in the reported cache
folder. Worker logs include media metadata, host events, control captures and
Neat editor version. On Windows, inspect `unexpected-dialog.json` alongside
`unexpected-dialog.png`; a black image is not evidence of a recognized dialog.
Do not automatically dismiss unknown dialogs.

The full-GUI Windows retry exposed `TF_FloatingLangBar_WndTitle`, a Windows
language-bar helper, as a false modal. Windows input-helper classes
`CiceroUIWndFrame`, `MSCTFIME UI`, and `IME` are now excluded from modal
classification. These helpers can share Nuke's disabled owner while Neat is
open. The filter uses exact native classes, not window titles or empty sizes;
Qt, native message boxes, and unknown plugin classes retain their modal checks.
All 75 focused host/Windows tests passed, including continuing to Auto Profile
with a language bar present and never clicking that helper. A native Windows
retry is still required to verify the full profiling workflow.

Windows modal diagnostics allow two seconds for an opening window to settle.
When native control text is empty, an owned helper queries .NET UI Automation
from the same private desktop. It reads only the target window's subtree,
limited to 64 elements and six levels, without focusing or invoking controls.
The supervisor bounds the query to four seconds and terminates the helper on
timeout or cancellation. The helper uses Windows PowerShell in MTA mode with
no user profile; no additional Python dependencies are required.

The reported window class is `Qt5152QWindow`, so Neat owns a Qt 5.15.2 UI separate
from Nuke's Qt 6 instance. The earlier in-process Qt 6 capture helper cannot
inspect that window and has been replaced. The existing native paint fallback
can still return a black PNG; text is read through accessibility independently.
`unexpected-dialog.json` now includes an `accessibility` section with control
names/help text or the query error. `dialog-accessibility.log` records helper
startup failures. Unknown dialogs remain unacknowledged.

Mocked regressions cover the supplied Qt 5 window shape, black captures,
transient windows, private-desktop/job ownership, timeout/cancellation cleanup,
stale-result removal and literal Unicode paths. A native Windows retry reached
the `Information` window (`Qt5152QWindowIcon`) through UI Automation but exposed
only its root title, with no message body. The PNG remained black. This does
not establish the cause of the warning.

Manual denoising was subsequently confirmed working on Windows, while the
Shotbox Linux workflow also works. Windows requests now select full NukeX GUI
startup via `menu.py`, leaving Linux's `--tg` launch and top-down setting intact.
The GUI still runs on the private desktop. The supervisor logs the launch mode.
This tests terminal-mode compatibility as a hypothesis; an inactive desktop
or another Windows-specific difference could still be responsible.

All 78 focused template/host/Windows tests passed locally after the change.
Checks include platform-specific request selection, GUI startup without a
terminal bootstrap, profile/render events, normal GUI exit, and cleanup.
Native Windows denoising with this launch change remains unverified.

Empty Windows profile windows now retry for up to two seconds without clicking.
If the client area stays empty, `window-capture-error.json` records its handle,
dimensions and enumerated window titles. Invalid oversized geometry still fails
before allocating a bitmap. An unexpected dialog's screenshot failure now
preserves its original title and recorded text instead of replacing them with
the capture error. All 73 focused host/Windows tests passed after these changes;
the new native Windows behaviour still needs verification.

## Visible Windows diagnostic

The visible Windows diagnostic (`test/denoise_windows_diagnostic.py`) reproduces
the latest cached job on the user's desktop instead of the private desktop.
It copies the worker template, preserves media/colourspace/frame preparation,
redirects outputs to its own leased cache directory, and opens Prepare Profile
without rendering or closing Nuke. `--request` selects a particular cached job.
The original job, source media and production output paths are not modified.
It writes `prepared.nk`, `media.json`, `diagnostic-status.json`, `launch.json`
and `nuke.log` beneath the printed diagnostic directory.

Four mocked regressions passed for setup isolation, latest-job selection,
visible GUI launch/environment, and first-frame profiling without rendering.
The user's Windows screenshots show Nuke preparing input frames, followed by
the full Neat Video **6.1.3** editor with Auto Profile enabled and Apply disabled.
That confirms the visible diagnostic opens the editor; it does not verify
automated input or capture on the private desktop.

## Windows loading and Profile Check sequence

Before the versioned editor appears, the generic `NeatVideo6 OFX 64` modal now
gets up to 120 seconds to finish loading. The timer survives handle changes;
cancellation remains active. Other dialogs still receive two seconds before
diagnostics and failure. No loading or warning dialog is dismissed.

Windows accepts versioned 6.1.2 and 6.1.3 editors and performs Auto Profile,
waits for enabled profile controls, selects Profile Check, then waits for its
active button, Assist Image preview label and enabled Apply control. The
preview must remain ready across polls before Apply. Missing controls, failed
clicks and interrupted readiness cannot create a profile receipt. Profile Check
is a preview mode, not an automatic assessment of profile quality. Linux keeps
its verified Auto Profile → Apply sequence.

The Profile Check button images and masked Assist Image label were captured
from the local Neat 6.1.2 editor using a disposable Xvfb session. A native
targeted click activated Profile Check and displayed its preview. Evidence:
`.cache/temp/denoise-profile-check-kftr4ry9/{before-check,after-check}.png`.
These are Linux reference images; matching on Windows 6.1.3 remains unverified.
All 84 focused Windows/host/diagnostic tests passed, including mocked loading,
version recognition, ordered clicks, preview readiness and failure handling.

## Visible Windows automation

The next private-desktop run timed out after five minutes without Auto Profile.
The user confirmed `profile-window.png` was entirely black, so the loaded editor
could not be matched. At the user's request, normal Windows Denoise now uses
the current desktop, opening visible NukeX/Neat windows. It retains suspended
launch into an owned process job, cancellation, crash cleanup and the existing
Auto Profile → Profile Check → Apply sequence.

Window enumeration is restricted to the live Nuke child's PID before dialog
inspection, capture or input. Visible capture uses BitBlt on the client DC;
it does not depend on Qt handling WM_PRINT. Capture and targeted input require
the owned window to be in front and not minimized, and ownership/focus is checked
again after capture. Losing focus resets readiness and shows a progress message;
no desktop switching or global mouse input is used. A continuously black capture
fails after ten seconds. Keep Neat unobscured during the Windows retry.

Mocked regressions verify current-desktop launch without desktop creation or
closure, PID filtering, foreground loss, cancellation, black-capture failure,
ordered profiling clicks and BitBlt resource cleanup. Native Windows capture
and control-image matching remain unverified. The older private adapter is
retained for isolated command-line test fixtures; normal Windows jobs select
the visible adapter. Linux behaviour is unchanged.

## Windows screenshot recognition

The user supplied three Windows Neat 6.1.3 screenshots showing the loaded
editor, a completed Auto Profile, and the active Profile Check preview.
These were copied from Pictures into `.cache/temp/denoise-windows-reference`.
Control crops, including disabled Apply and Adjust and Preview, now live in
`ui/denoise/windows` with source/crop provenance. Redacted control-only fixtures
are retained in `test/fixtures/denoise_windows`; source footage and the remote
desktop are excluded from those fixtures.

Normal visible Windows jobs use these references instead of Linux-derived
controls. Matching calibrates scale from Auto Profile, searches small scale
variations for subsequent controls, and compares enabled/disabled alternatives
at the same candidate location. Assist Image matching masks the footage behind
the lettering. The captured third state retains Build Profile, which the
sequence now explicitly exercises. Linux and private test-fixture matching
retain their existing references.

Screenshot replay tests distinguish all expected positive and negative states
at 100%, 80%, 93%, 125% and 150%. Additional checks reject disabled Apply even
with the Check preview visible, tolerate changed footage behind Assist Image,
and reject blank captures or Apply outside the footer. `control-scale.json`
records the chosen scale and capture size. These checks validate recognition
from the supplied screenshots, not live Windows capture or delivered clicks.

## Recorded Linux results

Earlier Linux template runs on 27 October 2026 verified dimensions, pixel
aspect, linear colour samples, ARRI LogC3 (EI800) input handling, multiple media
formats, output versioning, failure continuation and lifecycle cleanup. Generated
scripts are now retained exclusively in the worker cache; fresh-process reopen
checks verify the cached profile while shot output folders contain EXRs only.

The current cache-script workflow passed native acceptance on NukeX **17.1v2**
and Neat Video **6.1.2**. The profiled `.nk` was saved only in the worker cache,
reopened in a fresh Nuke process and rendered with its stored profile. Shot
output folders contained EXRs only, including the supplied still's single
4096 × 2160 frame at 1001. Profiling/render cancellation, supervisor failure,
shutdown and batch failure continuation passed. **120 focused tests passed.**
Evidence: `.cache/temp/denoise-template-verification-1r1_svnb/verification.json`.

## Real test-job verification

The supplied `/home/gambit/Desktop/-----------/shotboxtest/` job passed native
Linux testing on NukeX 17.1v2 / Neat Video 6.1.2. Source clips were read directly;
all test outputs were redirected to `.cache/temp/denoise-shotboxtest-u2vdwwli`.
The IDTs came from each saved shot's Read1 settings.

| Shot | Frames | Output dimensions | Input IDT |
|---|---:|---|---|
| sho010 | 59 | 4096 × 2851 | ARRI LogC3 (EI800) |
| sho020 | 101 | 2880 × 2160 | ARRI LogC3 (EI800) |
| sho030 | 250 | 4096 × 2160 | S-Log3 Venice S-Gamut3.Cine |

The real job-wide handler processed all three shots, including the test's hidden
shot and duplicate-shot deduplication. The real shot-card handler then passed
Version up on sho020 and Overwrite on sho010, selected through the actual popup.
There were **570 rendered frames** across these runs. Checks covered first,
middle and last EXR dimensions/finite pixels, cached `profiled.nk` files, no `.nk`
in render folders, preserving v001 when creating v002, and deleting only a stale
matching frame after overwrite. Whole-source-folder and template hashes matched.

This run exposed Neat's compact Advanced-mode Apply label on sho020. An exact
control-image variant fixes recognition with the existing matching threshold,
fresh Auto Profile receipt and enabled-control readiness checks intact. All
**61 profiling/Windows backend regressions passed** with the new variant.

Report: `.cache/temp/denoise-shotboxtest-u2vdwwli/verification.json`.
This is Linux evidence; native Windows qualification remains pending.

## UI and regression checks

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest \
  test.test_denoise_template test.test_denoise_ui test.test_nuke_denoise \
  test.test_denoise_windows test.test_nukedash_regressions \
  test.test_page_nukedash_thumbnails test.test_nuke_headless_tasks \
  test.test_page_nukedash_preview_tasks test.test_app_cache
```

UI regressions cover both action handlers without existing .nk files, hidden
shots, deduplication, missing sources, default overwrite/new-version/cancel,
queue failure continuation and busy/shutdown state. Output naming tests cover
separate plate versions, latest overwrite, next unused version and removing
only stale matching EXRs after a successful shorter render.

For native UI qualification, use a disposable job with source clips and shot
folders under `.cache/temp`, including spaces, multiple plates and a hidden
shot. Set Nuke's executable to 17.1v2. Run the shot-card action without an
existing comp, choose Overwrite, then Version up. Run job-wide Denoise
and confirm all unique clips including the hidden shot. Check each output version
contains EXRs with no generated `.nk`. Verify cancel and application shutdown
leave no worker processes, do not switch desktops or move the foreground
pointer. Record both entry-point results with the automated report.

## Qualification boundary

Native Windows qualification remains **pending**. The visible diagnostic opens
Neat 6.1.3 successfully, but the visible automation's captures and
Auto Profile → Profile Check → Apply sequence still need testing there.
Automated native PASS plus both UI entry points on Windows 17.1v2 with an
accepted Neat version are required; Linux results do not qualify
Windows. Codec availability is installation-dependent, not universal.
