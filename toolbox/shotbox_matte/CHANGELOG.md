# Changelog

## Unreleased

### Added

- [2026-10-26T13:23:54+00:00] Added Linux GPU and Nuke integration verification report.
- [2026-10-23T15:37:51+01:00] Added Find Read and alpha Viewer navigation controls.

- [2026-10-23T13:39:31+01:00] Verified direct alpha export, publication, and byte-preserving read-back.

- [2026-10-23T13:10:50+01:00] Verified EXR-only publication, cleanup, recovery, and legacy cache aliases.

- [2026-10-23T12:56:13+01:00] Verified popup typing, paste, validation, previews, and cancellation.
- [2026-10-23T12:41:04+01:00] Verified naming, Read wiring, regeneration, and saved-script compatibility.
- [2026-10-23T12:34:08+01:00] Added precomp naming dialog and standalone main-graph Read outputs.
- [2026-10-23T12:03:59+01:00] Verified GPU precomp pixels, regeneration, and independent cached rendering.
- [2026-10-23T12:01:08+01:00] Added versioned RGBA precomps with automatic Nuke Read activation.
- [2026-10-23T12:01:08+01:00] Added live/precomp switching and immutable full-channel source snapshots.
- [2026-10-23T11:11:27+01:00] Added Nuke Group, external generation, and immutable matte caches.
- [2026-10-23T11:11:27+01:00] Added strict cached rendering, relinking, freshness, and lifecycle guards.
- [2026-10-23T11:11:27+01:00] Added bridge contracts, failure tests, and Nuke verification fixtures.
- [2026-10-23T11:11:27+01:00] Documented Nuke installation, farm deployment, and GPU verification results.
- [2026-10-23T10:00:55+01:00] Added BAT and SH launchers with automatic local setup.
- [2026-10-23T10:00:55+01:00] Reused existing environments and bootstrapped Python when needed.
- [2026-10-22T18:01:48+01:00] Added experimental full-range mode alongside overlapping segment processing.
- [2026-10-22T17:23:49+01:00] Added 250-frame shots using overlapping segments and matte blending.
- [2026-10-22T17:23:49+01:00] Streamed long shots while preserving numbering and EXR metadata.
- [2026-10-22T12:09:15+01:00] Matched generation width to source aspect ratio when changing height.
- [2026-10-22T12:09:15+01:00] Calculated matching generation dimensions separately for each batch source.
- [2026-10-21T16:38:07+01:00] Added MXF input throughout desktop, CLI and batch workflows.
- [2026-10-21T16:34:10+01:00] Added multiple-movie and multiple-folder batch imports.
- [2026-10-21T16:34:10+01:00] Added recursive shot discovery with sequence deduplication.
- [2026-10-21T16:26:23+01:00] Added source ranges and sequential per-shot batch queue.
- [2026-10-21T16:26:23+01:00] Added scrubbable source, matte and overlay playback.
- [2026-10-21T16:26:23+01:00] Added named presets and persisted workflow settings.
- [2026-10-21T16:26:23+01:00] Added stage timing and learned job finish estimates.
- [2026-10-21T16:26:23+01:00] Added build identification and complete categorized diagnostic logs.
- [2026-10-21T16:26:23+01:00] Added small Windows updates preserving models and settings.
- [2026-10-21T15:43:12+01:00] Added editable prompts with saved UI and CLI settings.
- [2026-10-21T15:43:12+01:00] Rebuilt Windows portable application with custom prompt support.
- [2026-10-21T14:45:38+01:00] Bundled Windows EXE, runtime, models, and Alpha Gen LoRA.
- [2026-10-21T14:45:38+01:00] Added reproducible Windows builds and portable packaging checks.
- [2026-10-21T14:32:10+01:00] Copied six verified models into project storage.
- [2026-10-21T13:41:44+01:00] Added MOV, MP4, and common image sequence inputs.
- [2026-10-21T13:41:44+01:00] Restricted source browsing and drops to supported media.
- [2026-10-21T13:41:44+01:00] Added automatic colour defaults and movie FPS detection.
- [2026-10-21T13:41:44+01:00] Verified bed030 GPU export with twenty original-size EXRs.
- [2026-10-21T13:41:44+01:00] Added media decoding and shared export integration tests.
- [2026-10-21T13:31:56+01:00] Added Python UI launcher with automatic project environment selection.
- [2026-10-21T13:11:24+01:00] Added standalone native LTX Alpha Gen inference.
- [2026-10-21T13:11:24+01:00] Added responsive desktop controls and headless CLI.
- [2026-10-21T13:11:24+01:00] Added local model reuse without weight downloads.
- [2026-10-21T13:11:24+01:00] Added transactional EXR export and concurrent version reservation.
- [2026-10-21T13:11:24+01:00] Added EXR, interface, cancellation, and backend tests.
- [2026-10-21T13:11:24+01:00] Added installation instructions and native environment documentation.

### Changed

- [2026-10-26T13:12:00+00:00] Integrated Matte frontend and relocated Nuke host plugin.
- [2026-10-23T15:37:51+01:00] Simplified render controls and disabled unavailable node actions.
- [2026-10-23T15:37:51+01:00] Placed output Reads in nearby clear graph space.

- [2026-10-23T13:43:28+01:00] Used three-digit precomp versions in filenames and naming previews.

- [2026-10-23T13:39:31+01:00] Published alpha-only EXRs without baking original RGB again.
- [2026-10-23T13:39:31+01:00] Captured RGB once and removed redundant source snapshots.
- [2026-10-23T13:39:31+01:00] Defaulted new Nuke inputs to ACEScg.

- [2026-10-23T13:10:50+01:00] Kept precomp folders limited to final EXR sequences.
- [2026-10-23T13:10:50+01:00] Removed temporary captures, mattes, logs, and backend job files.
- [2026-10-23T13:10:50+01:00] Cleaned existing precomp folders while preserving all 105 EXRs.

- [2026-10-23T12:56:13+01:00] Restyled precomp naming with live filename and path previews.
- [2026-10-23T12:34:08+01:00] Reconnected downstream nodes and reused Reads after successful regeneration.
- [2026-10-23T12:34:08+01:00] Moved generator preview controls into Advanced.
- [2026-10-23T12:15:39+01:00] Verified installed Nuke startup loads updated precomp and interface.
- [2026-10-23T12:12:35+01:00] Reorganized Nuke controls into source, generation, and result sections.
- [2026-10-23T12:01:08+01:00] Matched PrecompSwitcher output paths and preserved existing render versions.
- [2026-10-23T12:01:08+01:00] Documented precomp stages, storage, relocation, and legacy cache behavior.
- [2026-10-23T11:39:53+01:00] Configured local Nuke startup and verified GPU runtime preferences.
- [2026-10-23T10:00:55+01:00] Removed obsolete distribution folder and documented launcher startup.
- [2026-10-22T18:01:48+01:00] Persisted processing mode across desktop, CLI, presets and batches.
- [2026-10-22T17:23:49+01:00] Updated segment progress, timing estimates and frame-limit documentation.
- [2026-10-21T17:13:59+01:00] Removed prompt controls and enforced empty Alpha Gen conditioning.
- [2026-10-21T16:40:48+01:00] Refreshed Windows distribution while preserving all model files.
- [2026-10-21T16:34:10+01:00] Moved batch processing into a dedicated workspace tab.
- [2026-10-21T15:46:22+01:00] Moved job manifests and previews into separate app storage.
- [2026-10-21T14:45:38+01:00] Kept portable settings beside EXE across folder moves.
- [2026-10-21T14:32:10+01:00] Changed model defaults and migrated saved external paths.

### Fixed

- [2026-10-26T13:12:00+00:00] Preserved external model paths and isolated worker execution.
- [2026-10-23T15:37:51+01:00] Opened latest job diagnostics after failed regeneration.
- [2026-10-23T15:37:51+01:00] Recognised native capture cancellation while preserving prior results.
- [2026-10-23T15:37:51+01:00] Relinked moved EXRs transactionally with validation and Undo/Redo.

- [2026-10-23T14:00:44+01:00] Fixed normal Nuke node placement, insertion, and automatic connections.

- [2026-10-23T13:39:31+01:00] Displayed alpha-only results correctly in the Group matte preview.

- [2026-10-23T13:10:50+01:00] Read matte previews from published alpha after temporary-file cleanup.

- [2026-10-23T12:56:13+01:00] Converted spaces and pasted punctuation to underscores while typing.
- [2026-10-23T12:12:35+01:00] Separated Nuke button rows and wrapped status messages.
- [2026-10-23T12:12:35+01:00] Preserved saved controls and animation during Nuke layout migration.
- [2026-10-23T12:03:59+01:00] Preserved saved live/precomp selection when reopening Nuke scripts.
- [2026-10-21T17:13:59+01:00] Ignored legacy prompts in saved workflows and worker payloads.
- [2026-10-21T16:38:07+01:00] Reported missing video decoders clearly during inspection and decoding.
- [2026-10-21T16:34:10+01:00] Kept valid batch sources when individual inputs fail.
- [2026-10-21T16:34:10+01:00] Enabled batch launch without loading a single shot.
- [2026-10-21T16:26:23+01:00] Fixed cropped EXR alignment and preserved canvas metadata.
- [2026-10-21T16:26:23+01:00] Fixed GUI input forwarding and invalid control limits.
- [2026-10-21T15:46:22+01:00] Kept generated output folders limited to matte EXRs.
- [2026-10-21T13:14:12+01:00] Corrected colour preparation descriptions in job manifests.
