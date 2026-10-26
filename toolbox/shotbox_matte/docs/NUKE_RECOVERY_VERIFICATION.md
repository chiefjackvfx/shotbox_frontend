# Recovery, navigation and properties verification

Verified on **23 October 2026**, in commercial **Nuke 17.1v2 on Linux**, using
**16×12 synthetic alpha EXRs**, frames **1001–1003**. No inference or GPU test
ran, and no Desktop precomp was modified.

| Evidence | Passed scope |
| --- | --- |
| [Native GUI report](nuke-recovery-gui.json) | Read placement, root/nested Find and View, moved/renamed EXR recovery, corrupt-file refusal, single-step Undo/Redo, latest-attempt logs, native capture cancellation and four properties states |
| [Fresh-process reopen](nuke-recovery-reopen.json) | Saved relinked result opens twice; Reload uses the valid active result despite a newer failed attempt; raw alpha Read keeps its identity; Group and standalone Read pass farm preflight and render all selected frames |
| [Independent pixels](nuke-recovery-pixels.json) | Every relocated EXR retains its original SHA-256; full-image alpha and reopened A-only Read match exactly; Group RGB preview equals the generated alpha |

Read placement cleared occupied nodes and an 80-pixel-font backdrop title and
border. A clear backdrop interior remained usable. Installing again preserved
the artist's Read position. Find selected the Read in its root or nested parent
network and invoked native graph framing. View reused an existing Read
connection or a free Viewer input, selected **alpha** display and preserved
unrelated inputs. A nested View created a Viewer in that parent network without
changing the root Viewer's connections.

Relink recovered a sequence moved into a new folder and renamed
`artist_alpha.1001.exr` through `.1003.exr`. A same-size byte corruption was
rejected before any change to the original receipt or **any serialized Group
or Read knob**. Restoring the file allowed recovery. One native Undo restored
both the Group receipt and Read path; one Redo restored the verified new
references. The original receipt remained unchanged and all relocated EXR
hashes remained exact. The saved scene reopened and rendered successfully.

Open log selected the latest pending attempt's actual `stderr.log`, even with
an older active result. After cleanup, it displayed the latest failure receipt
and message. Opening an external log application was intercepted in this test.
Capture cancellation injected the actual host **`_nuke.CancelledError`** through
the controller's asynchronous probe continuation. It recorded **cancelled**,
released the attempt, removed bulk files and retained the previous active Read.
The test used a controlled probe response and did **not** click Nuke's render
progress Cancel button or start an inference worker.

The real floating native properties panel was captured at both widths. Render
and Cancel enabled states followed the running attempt. Find/View were disabled
before any result and became available after publication. Recovery controls
were disabled during processing. All visible buttons stayed inside the panel.
The main tab contains daily render and viewing controls; diagnostics and recovery
are under Advanced. Alpha results hide the legacy RGB preview selector.

| State/tab | 650 pixels | 480 pixels |
| --- | --- | --- |
| Idle | [Image](nuke-recovery-idle-650-shotbox.png) | [Image](nuke-recovery-idle-480-shotbox.png) |
| Running | [Image](nuke-recovery-running-650-shotbox.png) | [Image](nuke-recovery-running-480-shotbox.png) |
| Success | [Image](nuke-recovery-success-650-shotbox.png) | [Image](nuke-recovery-success-480-shotbox.png) |
| Latest attempt failed | [Image](nuke-recovery-failed-650-shotbox.png) | [Image](nuke-recovery-failed-480-shotbox.png) |
| Advanced | [Image](nuke-recovery-success-650-advanced.png) | [Image](nuke-recovery-success-480-advanced.png) |

Repeat from the repository root:

```bash
.venv/bin/python integrations/nuke/tests_nuke/recovery_gui.py \
  --output docs --directory test-runs/nuke-recovery
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/reopen_recovery.py \
  --gui-report docs/nuke-recovery-gui.json --report docs/nuke-recovery-reopen.json
.venv/bin/python integrations/nuke/tests_nuke/verify_recovery_pixels.py \
  --gui-report docs/nuke-recovery-gui.json \
  --reopen-report docs/nuke-recovery-reopen.json --report docs/nuke-recovery-pixels.json
```

Each GUI run creates an isolated case under the selected workspace test
directory. These checks cover native workflow and data integrity, not artistic
matte quality. Windows and other Nuke builds remain unverified. Prior
[alpha-only delivery verification](NUKE_ALPHA_VERIFICATION.md) covers the
full-resolution footage workflow and explicitly documented CPU replay.
