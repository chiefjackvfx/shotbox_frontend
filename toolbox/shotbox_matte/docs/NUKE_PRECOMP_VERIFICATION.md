# Nuke precomp workflow verification

This records the original complete-precomp implementation. The subsequent
[named Read verification](NUKE_READBACK_VERIFICATION.md) adds a naming panel and
standalone main-graph Read as the default output. Group preview remains compatible
with the behavior verified below.

The precomp workflow follows the configured PrecompSwitcher EXR location:
`{script_dir}/../renders/precomp/{precomp_name}{ver}/{precomp_name}{ver}.####.exr`.
ShotBox writes the captured original image channels with the generated matte in
alpha, then switches the Group to a native Read of that finished sequence.

Repeatable acceptance commands, run from the repository root:

```bash
.venv/bin/python integrations/nuke/tests_nuke/launch_gui_test.py \
  --precomp --source /home/gambit/Desktop/-----------/bam030_DN_v001 \
  --cache test-runs/nuke-precomp --frames 3 --height 384 \
  --report docs/nuke-precomp-gui.json --timeout 600
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/reopen_precomp.py \
  --gui-report docs/nuke-precomp-gui.json --report docs/nuke-precomp-reopen.json
.venv/bin/python integrations/nuke/tests_nuke/verify_precomp_pixels.py \
  --gui-report docs/nuke-precomp-gui.json \
  --reopen-report docs/nuke-precomp-reopen.json --report docs/nuke-precomp-pixels.json
```

The launcher creates a temporary Nuke startup path and does not edit the user's
startup scripts or preferences. Its review comp lives under
`test-runs/nuke-precomp/shot/nuke/bam030.nk`; all generated EXRs live under that
test shot's `renders/precomp` directory. The original plate directory is read
only. Rerunning allocates further versions and preserves existing output.

Verified on **23 October 2026** in commercial **Nuke 17.1v2 on Linux**. The
actual GUI/native GPU run completed both generation passes in **92.336 seconds**
(104.711 seconds including GUI launch and shutdown). It used the supplied
ACEScg footage, frames **1001–1003**, at original **4096×2850** resolution.
Generation height was 384; source Read and precomp Read used raw values. The
final EXRs use 32-bit float, lossless compression and original timeline numbers.

| Evidence | Passed scope |
| --- | --- |
| [nuke-precomp-gui.json](nuke-precomp-gui.json) | Actual Nuke GUI, native GPU probe/worker, immutable source capture, publication, automatic Read, project paths and two generation versions |
| [nuke-precomp-reopen.json](nuke-precomp-reopen.json) | Fresh headless reopen, saved live bypass survives reopening, full-range cached render with external interpreter unavailable and live input disconnected |
| [nuke-precomp-pixels.json](nuke-precomp-pixels.json) | Independent OpenImageIO comparison of every pixel in both published versions, actual GUI renders, frozen/live tests and final headless renders |
| [nuke-precomp-host.json](nuke-precomp-host.json) | Native synthetic fixtures for source snapshots, auxiliary channels, animated bounds, overscan, timeline mapping, mutation rejection and overwrite protection |
| [nuke-precomp-host-compile.json](nuke-precomp-host-compile.json) | Host Python 3.11 compilation and imports without inference dependencies |

The final GUI run produced `bam030_alpha_v04` and `bam030_alpha_v05`. It checked
that the second pass created a distinct next version and that all previous
published EXR hashes remained unchanged. On completion the Group entered
**Precomped** automatically and read the finished RGBA sequence; no manual Read
creation or reload was required. Before the first result it passed live input.
During generation its state was **Rendering**. Toggling Use precomp off selected
the live input and changed its state to **Bypass**.

Every RGB pixel in both versions exactly equals the original ACEScg plate.
Every alpha pixel exactly equals the corresponding actual native worker matte's
red channel. An added `shotboxAux.value` channel remains exactly 42 everywhere.
Actual GUI Write output matches every published channel and pixel on all three
frames. Changing an upstream Grade to 0.5 left the active precomp's rendered
pixels unchanged; bypass rendered the live graded RGB and retained the source
channels. Saving with the live toggle off and reopening retained that choice.
After restoring precomp mode and disconnecting the live input, fresh headless
renders still exactly matched the final published version on every pixel and
channel. That process imported no inference backend, Torch or process adapter.

Initial GUI checks using `Node.sample` returned black for the internal Read
despite correct written files and correct fresh-process decoding. Acceptance
therefore uses actual Nuke Write output plus independent full-image comparison
as its pixel oracle. Earlier diagnostic versions remain on disk; the final
passing run and exact files are identified in the reports above.

This verifies the precomp workflow and pixel integrity, not artistic matte
quality. Three selected frames do not establish full-motion consistency, edge
quality, segment joins or delivery acceptance. Windows, other Nuke builds and
show-specific colour configurations remain unverified. For the broader
integration evidence and original 25-frame footage review, see
[NUKE_VERIFICATION.md](NUKE_VERIFICATION.md).
