# Precomp EXR preview verification

Verified on 26 October 2026 on Linux using the same source EXRs and current
preview code in two installed environments:

| Component | Available Python 3.11 reference | Frontend Python 3.13 |
| --- | --- | --- |
| Python | 3.11.13 | 3.13.15 |
| PyQt | 6.11.0 | 6.11.0 |
| Qt runtime | 6.11.0 | 6.11.2 |
| PyAV | 17.0.0 | 19.0.2 |
| OpenEXR | 3.4.9 | 3.5.2 |
| NumPy | 2.4.4 | 2.5.4 |

The reference environment is the local `../.venv`. Several dependencies changed
together, so this comparison covers the installed stacks rather than isolating
the Python interpreter as the original trigger.

## Reproduced and repaired behaviour

Five controlled cases failed before the changes: a temporary read failure that
never retried, a menu hovered during cache clearing, abandoned still requests
blocking the latest folder, cancelled proxy cleanup blocking its successor,
and a slow first frame preventing another ready frame from appearing.

The repaired lifecycle:

- Removes abandoned queued work when its preview closes.
- Keeps running task identities distinct across cache clearing and reopening.
- Restarts a hovered preview after cache clearing finishes.
- Retries failed still reads on another hover instead of caching failure.
- Keeps a valid still available even if proxy preparation fails.
- Shows the first available proxy image while preserving playback frame order.
- Stops timers and ignores late results when menus close or are destroyed.
- Shows the panel on folder hover before Qt opens its submenu.
- Repositions the visible panel without restarting still or proxy preparation.
- Passes native pointer input through the preview window.

## Regression checks

All **76 tests** passed in the frontend environment: 38 EXR preview tests,
19 Settings/cache tests, and 19 shot-card tests. The same **38 preview tests**
passed under Python 3.11. Modified Python files passed Ruff and
`git diff --check` passed.

From the frontend directory:

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest \
  test.test_exr_thumbnails test.test_settings_autosave test.test_shotcard_matchmove
```

## Actual sequence and native popup checks

The opt-in acceptance harness reads real source EXRs and stores reports,
screenshots, and temporary proxy files under `.cache/temp`:

```bash
QT_QPA_PLATFORM=offscreen venv/bin/python test/exr_preview_acceptance.py \
  --folder /path/to/an/exr/sequence
```

It checked the 59-frame sequence in
`/home/gambit/Desktop/-----------/shotboxtest/sho010/renders/precomp/sho010_DN_v001`.
The source EXRs were left untouched.

Both runtimes passed native X11 checks on an authenticated private Xvfb display:
12 folder visits, submenu reopening, 30 rapid hover movements, recovery after
cache clearing, animated playback, and stopping playback when menus close.
The first still appeared after about 0.37 seconds in each environment, including
Qt's submenu opening delay. These are local measurements, not timing guarantees
for other storage or workloads.

The native harness uses actual X11 mouse motion to exercise popup input grabs;
run this mode only on an isolated display. Offscreen acceptance opens submenus
programmatically. Both modes now recreate the entire context menu every four
visits; native reopening uses actual right-click button events.

The folder-hover change additionally passed 40 native folder visits under
Python 3.13, including ten context-menu recreations, cache clearing, rapid
hovering, and playback. The report is
`.cache/temp/exr-preview-acceptance-cjh5u4za/verification.json`. A separate
12-visit offscreen check also passed. The unit checks cover showing the panel
before the submenu opens, moving between folders, retaining prepared images
when the submenu opens, and closing before the deferred show runs.

The final native comparison reports are under
`.cache/temp/exr-runtime-final-z3md941h/runtime-comparison.json`. Cache clearing
can remove these artifacts; the permanent harness produces fresh reports.
Windows and macOS native popup acceptance remains manual.
