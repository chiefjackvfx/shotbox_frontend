# Naming and standalone Read verification

The subsequent [naming UI verification](NUKE_NAMING_UI_VERIFICATION.md) covers
the styled dialog, live underscore entry and filename/location previews. The
original native panel screenshots below record the earlier presentation; Read
placement and rendering behavior remain as verified here.

Verified on **23 October 2026**, in commercial **Nuke 17.1v2 on Linux**.
Generate opens the native **Name precomp** panel. Cancel leaves no attempt or
version directory. An accepted name determines the precomp's folder and EXR
prefix. Completion creates a normal **Read** beside the generator in its parent
network and reconnects existing downstream inputs, including Viewers. The Read
contains captured original RGB, generated alpha and preserved auxiliary channels.

Regeneration updates the owned Read while retaining earlier EXR versions. Artist
edits to its name and position survive. A manually changed downstream connection
is respected. A deleted Read can be recreated on the next result installation.
The generating Group remains available to create another version; the finished
Read works independently of that Group and the external inference interpreter.

| Evidence | Passed scope |
| --- | --- |
| [Native naming panel](nuke-readback-name-dialog.png) | Actual name entry and Cancel/OK controls |
| [Main network](nuke-readback-network.png), [capture report](nuke-readback-network.json) | Actual saved graph with standalone `Read_bam030_alpha` outside the generating Group, connected downstream |
| [nuke-readback-host.json](nuke-readback-host.json) | Native synthetic fixtures: root/nested placement, multiple input slots, rename/position preservation, regeneration, prior-file hashes, artist connections, deletion recovery and save/reopen without duplicate Reads |
| [nuke-readback-gui.json](nuke-readback-gui.json) | Actual naming dialog, source capture, probe, preflight, CLI export, validation, publication and automatic Read; explicitly substituted CPU replay inference engine |
| [nuke-readback-reopen.json](nuke-readback-reopen.json) | Actual review comp opens twice without duplicate Reads; all selected frames render after deleting the generator Group, with external interpreter unavailable |
| [nuke-readback-pixels.json](nuke-readback-pixels.json) | Every pixel/channel checked independently in both versions, actual GUI Read renders and fresh headless standalone renders |
| [Read properties](nuke-readback-properties.png), [information tab](nuke-readback-properties-info.png), [report](nuke-readback-properties.json) | Native Read interface and small ShotBox information tab; ownership metadata remains hidden |

The final GUI workflow used the supplied **ACEScg** shot, frames **1001–1003**,
at **4096×2850**, with generation height 384. It completed in **42.255 seconds**
and published `bam030_alpha_v03` and `bam030_alpha_v04`. The same external Read
identity served both versions. Every RGB pixel equals the original plate; every
alpha pixel equals the corresponding CLI-exported matte red channel. The
auxiliary channel remains 42 everywhere. GUI and headless rendered outputs
exactly match the published precomp on every channel and pixel.

A new native inference attempt ran out of GPU memory while other Nuke/Resolve
processes occupied about 24 GB of the 32 GB GPU. Those applications were left
untouched. The final naming/Read acceptance therefore used a test-only CPU
engine replaying previously verified native GPU mattes through the actual CLI
and export pipeline. Replay includes model-raster resampling before normal
full-size restoration, so it is not a bit-exact replay of the old full-size
matte. The report records `native_gpu_inference: false` and the original source
manifest and checksums. Actual prior native GPU execution remains documented in
[NUKE_PRECOMP_VERIFICATION.md](NUKE_PRECOMP_VERIFICATION.md); it is separate
evidence from this workflow run.

Repeat from the repository root:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/readback_smoke.py \
  --report docs/nuke-readback-host.json
.venv/bin/python integrations/nuke/tests_nuke/launch_gui_test.py \
  --readback --source /home/gambit/Desktop/-----------/bam030_DN_v001 \
  --replay-manifest test-runs/nuke-precomp/shot/renders/precomp/bam030_alpha_v05/integration.json \
  --cache test-runs/nuke-readback --frames 3 --height 384 \
  --report docs/nuke-readback-gui.json --timeout 600
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/reopen_readback.py \
  --gui-report docs/nuke-readback-gui.json --report docs/nuke-readback-reopen.json
.venv/bin/python integrations/nuke/tests_nuke/verify_precomp_pixels.py \
  --gui-report docs/nuke-readback-gui.json \
  --reopen-report docs/nuke-readback-reopen.json --report docs/nuke-readback-pixels.json
```

Omit `--replay-manifest` to request a new native inference run when GPU resources
permit. Repeated runs allocate further versions. All test writes stay under the
selected workspace test directory; the plate directory and user preferences
remain untouched. The repository regression run passed **575 tests and 13
subtests**, with Ruff clean. These checks establish workflow and pixel
integrity, not artistic matte quality. Windows and other Nuke builds remain
unverified.
