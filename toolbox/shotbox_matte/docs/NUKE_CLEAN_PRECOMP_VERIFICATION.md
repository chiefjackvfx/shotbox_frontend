# Clean precomp output verification

Verified on **23 October 2026**, in commercial **Nuke 17.1v2 on Linux**.
New precomp version folders contain **only the final EXR sequence**. Capture
images, inference outputs, logs, manifests and locks stay in a private cache
job directory. Successful, failed and cancelled jobs remove their bulk working
files; a small validation receipt remains outside the artist's precomp folder.

The actual GUI workflow used the supplied **ACEScg** shot at **4096×2850**,
frames **1001–1003**, with generation height 384. In **47.841 seconds**, it
completed a first version, an intentional worker failure, a cooperative cancel
and a successful regeneration. Both finished folders contained exactly three
timeline-numbered EXRs. Every private job retained only `integration.json`.
Failure and cancellation preserved the previous active Read and every existing
final-file checksum; regeneration published a separate version.

| Evidence | Passed scope |
| --- | --- |
| [Native host](nuke-clean-precomp-host.json) | Two clean publications, retained original RGB/alpha/auxiliary depth, matte-only preview, explicit Reload, same-size EXR corruption rejection and cached farm rendering |
| [GUI workflow](nuke-clean-precomp-gui.json) | Actual source capture, probe, preflight, CLI/export, validation, publication, cleanup, Read wiring, failure, cancellation and regeneration |
| [Fresh-process reopen](nuke-clean-precomp-reopen.json) | Scene opens twice without duplicate Reads; compact receipt validates and Reload works after intermediate deletion; Group and matte-only farm renders work; standalone Read renders after deleting the generator, with external interpreter unavailable |
| [Independent pixel inspection](nuke-clean-precomp-pixels.json) | Every pixel of both versions: RGB equals original ACEScg source, alpha equals the explicit replay fixture, auxiliary channel remains 42; actual GUI and fresh headless renders match the final EXRs |
| [Desktop cleanup audit](nuke-desktop-precomp-cleanup.json) | Seven terminal legacy directories cleaned; all **105 existing EXRs** retain their original SHA-256; two empty failed folders removed |
| [Desktop native verification](nuke-desktop-cleanup-host.json) | First/last frames of all four completed legacy versions load and render as RGBA and matte-only after cleanup through the old manifest aliases |

The synthetic host fixture also saved a legacy scene pointing to the public
`integration.json`, moved its receipt to the private cache and removed the old
working files. The original saved scene reopened, previewed, Reloaded and
rendered through the alias. All original final EXR hashes remained unchanged.

The GUI run explicitly used a **test-only CPU replay engine**, not new GPU
inference. Other applications occupied GPU memory and were left untouched.
Previously verified native GPU matte outputs were fitted to the model raster,
then passed through the normal CLI restoration/export pipeline. The reports
record `native_gpu_inference: false`, provenance and source hashes. Pixel
comparison accounts for that resampling; it does not claim equality to the old
unresampled matte. Prior actual GPU evidence is documented in
[NUKE_PRECOMP_VERIFICATION.md](NUKE_PRECOMP_VERIFICATION.md). These checks verify
storage, workflow and pixel integrity; they do not assess artistic matte quality.
Windows and other Nuke builds remain unverified.

Repeat the isolated test workflow from the repository root:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/clean_precomp_smoke.py \
  --report docs/nuke-clean-precomp-host.json
.venv/bin/python integrations/nuke/tests_nuke/launch_gui_test.py \
  --clean-precomp --source /home/gambit/Desktop/-----------/bam030_DN_v001 \
  --replay-manifest test-runs/nuke-precomp/shot/renders/precomp/bam030_alpha_v05/integration.json \
  --cache test-runs/nuke-clean-precomp --frames 3 --height 384 \
  --report docs/nuke-clean-precomp-gui.json --timeout 600
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/reopen_clean_precomp.py \
  --gui-report docs/nuke-clean-precomp-gui.json \
  --report docs/nuke-clean-precomp-reopen.json
.venv/bin/python integrations/nuke/tests_nuke/verify_clean_precomp_pixels.py \
  --gui-report docs/nuke-clean-precomp-gui.json \
  --reopen-report docs/nuke-clean-precomp-reopen.json \
  --report docs/nuke-clean-precomp-pixels.json
```

These commands write test results under the workspace test directory and do not
perform the separate Desktop legacy cleanup. The replay source is a retained
historical test fixture. Repeated runs allocate further precomp versions.
