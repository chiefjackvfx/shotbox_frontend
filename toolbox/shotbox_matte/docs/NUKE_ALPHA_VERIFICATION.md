# Alpha-only delivery verification

Verified on **23 October 2026**, in commercial **Nuke 17.1v2 on Linux**.
New nodes default to **ACEScg** input and **Matte only** preview. Generate
captures RGB once for inference. It creates no full-source snapshot and performs
no Nuke RGB/alpha bake after inference. The worker writes a full-resolution EXR
with **exactly one `A` channel**. Publication moves that sequence into the named
precomp version and installs a normal raw Read in the main node network.

The final actual GUI run used the supplied **4096×2850 ACEScg** plate,
frames **1001–1003**, with generation height 384. It completed two versions in
**20.936 seconds** (`bam030_alpha_only_v05` and `v06`). Each generation executed
exactly one foreground `ShotBoxCaptureWrite` with RGB channels. A fatal test
guard rejected any other Nuke execution during processing and any call to the
old RGB/alpha publisher. Every final EXR retained the **exact byte size and
SHA-256 of its original backend output**, proving there was no second encode.

| Evidence | Passed scope |
| --- | --- |
| [Native host](nuke-alpha-host.json) | ACEScg/Matte only defaults, single RGB capture, no source snapshot, direct publication without Nuke execution, raw A-only Read, unclamped negative/above-one float alpha, both Group preview choices, regeneration, cleanup, Reload and cached save/reopen/farm rendering |
| [GUI workflow](nuke-alpha-gui.json) | Actual capture, probe, preflight, CLI/export, validation, alpha promotion, clean version folders, backend-identical hashes and automatic standalone Read; explicitly substituted CPU replay engine |
| [Fresh-process reopen](nuke-alpha-reopen.json) | Scene opens twice without duplicate Reads; Reload and raw alpha Read work; both Group preview choices render after cleanup; standalone Read renders after deleting the generator, with external interpreter unavailable |
| [Independent pixel inspection](nuke-alpha-pixels.json) | All six final EXRs have exactly one A channel at original resolution; every alpha pixel matches the explicit replay fixture; GUI/headless Read outputs match; Group RGB previews equal alpha; legacy RGBA remains exact on every channel/pixel |

Group matte preview creates RGB channels only in its preview graph. Those
channels are absent from the published EXRs and the standalone Read. Both
versions' private job directories retain only their validation receipt, and
each artist-facing version folder contains only its three final EXRs.
Regeneration preserves all earlier EXR hashes. A retained legacy RGBA result
still activates and renders its original RGB, generated alpha and auxiliary
channel correctly; existing outputs were not converted.

This GUI acceptance used a **test-only CPU replay of previously verified native
GPU matte outputs**, with model-raster fitting followed by normal CLI
restoration/export. Reports record `native_gpu_inference: false`, provenance
and source hashes. No fresh GPU inference was attempted. The every-pixel
comparison uses this explicitly resampled fixture; prior native GPU evidence
remains in [NUKE_PRECOMP_VERIFICATION.md](NUKE_PRECOMP_VERIFICATION.md). These
checks establish workflow and data integrity, not artistic matte quality.
Windows and other Nuke versions remain unverified.

Repeat from the repository root:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/alpha_smoke.py \
  --report docs/nuke-alpha-host.json
.venv/bin/python integrations/nuke/tests_nuke/launch_gui_test.py \
  --alpha-only --source /home/gambit/Desktop/-----------/bam030_DN_v001 \
  --replay-manifest test-runs/nuke-precomp/shot/renders/precomp/bam030_alpha_v05/integration.json \
  --cache test-runs/nuke-alpha --frames 3 --height 384 \
  --report docs/nuke-alpha-gui.json --timeout 600
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/reopen_alpha.py \
  --gui-report docs/nuke-alpha-gui.json --report docs/nuke-alpha-reopen.json
.venv/bin/python integrations/nuke/tests_nuke/verify_alpha_pixels.py \
  --gui-report docs/nuke-alpha-gui.json \
  --reopen-report docs/nuke-alpha-reopen.json --report docs/nuke-alpha-pixels.json
```

All test output stays under the workspace test directory. These commands do not
change existing Desktop precomps. The replay and legacy RGBA cases use retained
historical test fixtures. Repeated runs allocate further versions.
