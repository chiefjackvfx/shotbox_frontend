# Python 3.13 qualification

Python 3.13 is the Linux production default for the frontend and its shared
Matte worker. The main Linux `venv` was migrated to **3.13.15** on 26 October
2026, following the user’s request to use the normal launcher. Run
`./run_shotbox_frontend.sh` as usual. Windows/macOS remain on **3.12** until
their native acceptance is complete; remaining checks are recorded below. The Django
backend, its environments and deployment are outside this migration.

## Prepare and launch

Run from the frontend directory on Linux/macOS:

```sh
sh setup_runtime.sh --qualify-python313
PYTHON_JIT=0 .runtime/qualification/python313/venv/bin/python main.py
```

On Windows, run from PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_runtime.ps1 --qualify-python313
$env:PYTHON_JIT = "0"
& .\.runtime\qualification\python313\venv\Scripts\python.exe main.py
```

Setup installs standard managed CPython 3.13, without free-threading or JIT,
and the existing frontend requirements into
`.runtime/qualification/python313/venv`. This qualification command leaves the production `venv` in place.
This is a qualification environment for the combined application, not a private
Matte runtime. Launching Shotbox from it also launches desktop Matte workers
with that exact interpreter. Production launchers continue to use `venv`.

The neighbouring `report.json` captures interpreter/OS details, every installed
package version, requirements hash, dependency consistency and GPU/driver
details where available. Failed installation/probe steps record their phase and
error. A bootstrap download failure happens before Python can write this report;
retain the terminal output in that case. Every run records
`promotion_ready: false`: installation evidence does not certify interactive
acceptance. The report is refreshed on each qualification run.

Persistent tools, interpreters, qualification environments and reports live in
`.runtime`. Downloads and disposable acceptance files use `.cache/temp` and
active-task leases. Settings → Clear Cache preserves active setup work.

For Nuke acceptance, use a temporary test-only preferences override pointing at
the qualification interpreter. Do not change the user's saved custom override.
Nuke retains its bundled Python/PySide; only the external worker uses 3.13.

## Recorded Linux results — 26 October 2026

Environment: Linux x86_64, glibc 2.34, managed **CPython 3.13.15**,
PyQt6 6.11.0 / Qt 6.11.2, PyTorch **2.11.0+cu130**, torchvision
0.26.0+cu130, torchaudio 2.11.0+cu130, transformers 5.14.1,
LTX core/pipelines 1.5.0 at revision
`e98f6d927d83c92ef2be6aec76c8440b19306e39`, RTX 5090 and driver 590.48.01.
Existing dependency pins and model weights were retained. The exact installed
package snapshot is in [the Linux environment record](PYTHON313_LINUX_ENVIRONMENT.json).

- Fresh isolated installation, native imports and dependency consistency passed.
- A real disposable 3.12.14 environment migrated to 3.13.15 at its final path,
  retained a backup and was reused on another run. Deliberate dependency
  installation failure restored the old 3.12 environment and marker. Paths
  contained spaces; the production environment configuration was unchanged.
- The full Matte suite passed: **775 tests and 25 subtests** under 3.13.
- All 56 frontend test modules ran in separate processes: **795 passed,
  141 subtests passed, 3 Windows-only tests skipped, 1 existing failure**.
  The XML import test expects artist `Jack` but receives local user `gambit`;
  the same failure was reproduced on the production 3.12 runtime. No unrelated
  application behavior was changed to accommodate this test. The runtime module
  was rerun after adding four further migration failure cases; its final
  43-test result is included in the aggregate above.
- Actual Roto UI generation launched the same 3.13 interpreter as its worker,
  exported three 16×16 RGB grayscale EXRs, kept the UI responsive, preserved
  source hashes and passed cancellation without deleting completed outputs.
  Dimensions and finite pixels were inspected. It used existing local weights.
- The identical tiny fixture took **23.582 seconds on 3.13** and
  **23.599 seconds on 3.12** in one run each. This establishes no meaningful
  timing difference for that fixture, not a production performance claim.
- Commercial Nuke **17.1v2**, retaining its bundled Python **3.11.11**, completed
  real GPU inference through the external 3.13 worker. Three alpha-only EXRs
  were published, checked by checksum, installed as a normal raw Read and
  rendered back. Private job files were cleaned; cancellation preserved the
  completed output. The complete GUI check took **25.212 seconds**.
- Dependency resolution passed for Windows x86_64 (**60 packages**), macOS ARM64
  (**20**) and Intel macOS (**20**). This does not establish native installation,
  DLL/dylib loading, GPU execution or GUI compatibility on those systems.

Detailed transient records are in frontend `.cache/temp`: qualification test
logs under `python313-frontend-tests-ddd40epi`, migration evidence under
`real-python313-migration-7oygwh_s`, Roto records under
`shared-runtime-acceptance-9vr8m2b3` (3.13) and
`shared-runtime-acceptance-zwlxwkx3` (3.12), and Nuke records under
`python313-nuke-lp2yagxo`. They are disposable; the results above and environment
snapshot are retained here. Historical verification documents remain unchanged.

## Remaining acceptance and promotion

| Check | Linux | Windows | macOS |
| --- | --- | --- | --- |
| Dependency resolution | Passed | Passed | Passed, ARM64 and Intel |
| Native install/imports/consistency | Passed | Pending | Pending |
| Real desktop/Matte regression suites | Passed with existing test issue | Pending | Pending |
| Interactive startup, settings, clean exit | Pending | Pending | Pending |
| Read-only backend connectivity | Pending | Pending | Pending |
| MOV playback, EXR previews, OCIO and playback timing | Pending manual acceptance | Pending | Pending |
| Real GPU generation/cancellation | Passed, tiny fixture | Pending | Unsupported; verify existing limitation |
| Nuke alpha publication/readback/recovery | Passed | Pending | Outside current inference support |

For automated testing, install pytest into the isolated environment with the
frontend-owned uv and run the Matte suite with the frontend and
`toolbox/shotbox_matte` on `PYTHONPATH`, `PYTHON_JIT=0` and
`QT_QPA_PLATFORM=offscreen`. Run each frontend `test/test_*.py` module in its own
process; the existing combined Qt suite is not certified. Record failures and
skips instead of treating a zero-crash module run as full product acceptance.

Linux was promoted independently at the user’s request. The existing
3.12.14 environment was preserved under
`.runtime/backups/venv-b87dfceeb8874a9cbc42290155575551`. Migration verified
all application imports and the pinned CUDA dependencies. Linux bootstraps and
Python probes select `runtime_python_linux.txt`; Windows/macOS select
`runtime_python.txt`. The environment snapshot above records the earlier
qualification phase, before Linux promotion.

For the remaining platforms, only after their applicable checks pass:

1. Change frontend `runtime_python.txt` from `3.12` to `3.13` and update the
   platform-default test expectations. The Linux override is already 3.13.
2. Rerun native installer/updater migration and rollback acceptance on all
   platforms, then update current installation documentation to the new default.
3. Use the normal release workflow; this migration does not publish, tag or bump
   a version automatically.

Normal migration backs up `venv`, creates the replacement at its final path
and restores the backup if installation or verification fails. Successful
backups remain under `.runtime/backups`. For manual rollback, close Shotbox and
all its workers, retain the failed candidate separately and restore the chosen
backup to the original `venv` path. Keep the old managed interpreter available;
do not run a renamed backup directly. Revert `runtime_python_linux.txt` to 3.12 for Linux, or
`runtime_python.txt` for other platforms, in the rollback checkout before running another updater, or it will migrate again.

Matte metadata and Nuke bridge compatibility remain Python 3.12–3.13; the Ruff
target stays `py312`. No model format, worker protocol, backend API or user
settings schema changes are required.
