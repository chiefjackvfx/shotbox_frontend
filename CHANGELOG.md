# Changelog

All notable frontend publish changes should be documented in this file.

## Unreleased

### Added

### Changed

### Fixed

## 5.2.8 - 2026-10-27

### Added

- Add visible Windows profiling diagnosis without automatic rendering.
- Test diagnostic setup isolation, launch, and profiling lifecycle.

## 5.2.7 - 2026-10-27

### Added

- Test language-bar filtering while preserving genuine plugin warnings.

### Fixed

- Exclude Windows input helpers from denoise modal detection.

## 5.2.6 - 2026-10-27

### Added

- Record window dimensions when denoise capture remains unavailable.
- Test Windows GUI launch and host shutdown lifecycle.

### Changed

- Profile each clip's first frame on Windows and Linux.
- Launch Windows denoise with full NukeX GUI privately.
- Document Windows launch hypothesis and remaining verification.

### Fixed

- Retry temporarily empty Windows profiling windows before failing.
- Preserve plugin warning details when dialog screenshots fail.

## 5.2.5 - 2026-10-27

### Added

- Read separate Qt denoise dialogs through Windows accessibility.
- Test accessibility cleanup, timeouts, cancellation, and transient denoise windows.

### Changed

- Document Windows diagnostic retry and pending native qualification.

### Fixed

- Allow opening Windows denoise dialogs time to settle.

## 5.2.4 - 2026-10-27

### Fixed

- Ignore local EXR fixture and remove oversized unpushed history.

## 5.2.3 - 2026-10-27

### Added

- Denoise original media using versioned template outputs.
- Added native template, media, lifecycle, and Windows acceptance checks.

### Changed

- Recorded real three-shot denoise and versioning verification.
- Store profiled Nuke scripts exclusively in the worker cache.
- Removed saved-comp denoising and legacy acceptance/benchmark tools.
- Migrated denoise regressions to template-only media requests.
- Label denoise output choices Overwrite and Version up.
- Limit denoise width to 4096 without upscaling.
- Documented template workflow and pending Windows qualification.

### Fixed

- Apply shot IDT before validating denoise media readers.
- Recognize Neat Apply label sizes after fresh profiling.
- Retry transient Linux profiling windows without confirming stale profiles.

## 5.2.2 - 2026-10-27

### Added

- Covered Windows termination races and original-error preservation.
- Capture native dialog text when Windows screenshots fail.

### Fixed

- Wait for job termination before closing Windows workers.
- Retry black Windows captures using bounded client painting.

## 5.2.1 - 2026-10-27

### Added

- Added staged NukeX 17.1v2 Windows qualification checks.
- Added native shutdown and batch failure-continuation acceptance.
- Added Nuke 17.0 and verifier failure regression coverage.

### Fixed

- Prevented Windows qualification selecting other Nuke versions.
- Prevented failed Apply clicks leaving profile confirmation receipts.
- Allowed Nuke 17.0 denoise without exact-version restrictions.
- Show actual Nuke startup errors in verification output.

## 5.2.0 - 2026-10-26

### Added

- Added Windows private-desktop denoise backend and process cleanup.
- Added Windows denoise regressions and native verification commands.
- Added Denoise toolbar button for queued job-wide clip processing.
- Added batch denoise cancellation, failure, and native acceptance coverage.
- Added batch preview timing, fidelity, cancellation, and failure coverage.
- Added Qt denoise startup and profile-readiness regression coverage.
- Added isolated denoise timing and thread-count benchmark coverage.
- Covered direct folder hovering and repeated native right-click reopening.
- Added preview lifecycle and Python runtime acceptance coverage.
- Added coverage for queued proxies and late EXR thumbnails.
- Added live denoise console stages, Nuke output, and errors.
- Added background Neat Video denoise from shot-card menus.
- Added denoise cancellation and Nuke acceptance coverage.
- Added deferred-publication, collision, and cross-volume rollback coverage.
- Added Roto processing-mode selection with live plan summaries.
- Added Roto mode switching and worker option regressions.
- Added long-sequence GUI, export, and bridge regressions.

### Changed

- Enabled single and batch denoise actions on Windows.
- Reused headless Nuke workers for faster batch previews.
- Halved measured denoise duration with lightweight Qt startup.
- Documented measured denoise timings and recommended thread settings.
- Close Denoise popup automatically after successful rendering and cleanup.
- Show denoise steps, activity history, elapsed time, and rendering progress.
- Denoise now ignores shot locks and open editing sessions.
- Documented Linux denoise requirements and Nuke verification.
- Unified Python 3.13 defaults across all desktop platforms.
- Removed 250-frame restrictions from Matte and Nuke workflows.

### Fixed

- Preserved native Windows denoise errors in failure reports.
- Show precomp previews on hover without waiting for submenus.
- Fixed EXR preview cancellation, retries, and cache clearing recovery.
- Restored EXR still previews while playback proxies are preparing.
- Fixed Denoise Close button, Escape, and window dismissal.
- Excluded verified denoise workers from editing-session lock detection.
- Avoided hidden autosave recovery prompts before loading denoise scripts.
- Fixed background NukeX loading and Neat Video Apply detection.
- Deferred Matte output folders until successful generation completes.

## 5.1.0 - 2026-10-26

### Added

- Added isolated Python 3.13 qualification with environment evidence.
- Added Python 3.13 migration rollback and compatibility regressions.

### Changed

- Switched normal Linux launches and updates to Python 3.13.
- Centralized runtime selection with platform-specific production defaults.
- Recorded Python 3.13 GPU, Nuke, and migration qualification.

## 5.0.1 - 2026-10-26

### Added

- Added Resolve import isolation and connection error regressions.
- Added Windows launcher coverage for native and Python failures.
- Added Qt logging defaults and override regression coverage.
- Added eight-colour palette strip beneath terminal startup diagnostics.

### Changed

- Documented deferred Resolve scripting library loading.
- Enabled Windows startup fault diagnostics and documented crash reporting.
- Documented optional FFmpeg metadata logging for troubleshooting.
- Documented palette controls and extended terminal banner rendering coverage.

### Fixed

- Deferred Resolve native imports to prevent Windows startup crashes.
- Fixed Windows launcher hiding negative native crash exit codes.
- Suppressed repeated FFmpeg video metadata dumps by default.

## 5.0.0 - 2026-10-26

### Added

- Added logo-derived terminal banner with runtime and dependency versions.
- Added startup banner layout, colour and failure regression coverage.
- Added automatic Python 3.12 migration with recoverable environment backups.
- Added shared-runtime setup, rollback and interpreter regression coverage.

### Changed

- Documented startup diagnostics and terminal colour behaviour.
- Unified Shotbox and Matte dependencies in the main environment.
- Removed standalone Matte launchers, private setup and Windows packaging.
- Updated Nuke defaults while preserving custom interpreters and models.
- Documented shared setup, migration recovery and platform requirements.

### Fixed

- Fixed Matte workers using a different Python than Shotbox.

## 4.5.0 - 2026-10-26

### Added

- Added compact Precomp Roto popup with seed and generation controls.
- Added Roto planning, export, cancellation and lifecycle regression coverage.
- Added cached Matte export and cross-volume recovery regressions.
- Added Linux GPU and Nuke integration verification report.
- Added owned Matte pop-out with isolated GPU worker configuration.
- Added Shotbox-managed Nuke installation and shared model settings.
- Added cross-process cache protection and integration regression coverage.
- Added Precomp folder menus with open and copy actions.
- Added Assets-style corner menu arrow to Precomp.
- Added Roto placeholder for Precomp folders containing EXR sequences.
- Added cached EXR previews beside open folder submenus.
- Added preview tests covering hovering, caching, refresh, failures, and deletion.
- Added preview detection regressions for mixed versions, gaps, and sidecars.
- Added looping EXR sequence playback in Precomp preview panels.
- Added playback regressions covering looping, cancellation, failures, and stale frames.
- Added proxy cache, timing, cancellation, and decoder fallback regressions.
- Added Settings button clearing caches and unused temporary files.
- Added cache clearing, running task protection, and Settings regression coverage.
- Added Settings button opening the frontend cache folder.
- Added centred loading spinner to Precomp EXR preview panels.
- Added proxy naming, migration, invalidation, and nested cleanup regressions.

### Changed

- Clarified maximum Roto height and quality versus generation time.
- Changed Roto lifecycle regressions to exercise Generate Now clicks.
- Shared Matte worker transport and documented compact Roto workflow.
- Defaulted Matte exports to Version up on every launch.
- Shared EXR pixel decoding between playback and menu previews.
- Used small local proxies for realtime Precomp preview playback.
- Bounded preview caches and released closed menu playback images.
- Centralized disposable runtime files under frontend .cache; ignored in Git.
- Documented frontend cache location and Settings cleanup behavior.
- Documented mandatory .cache rule for coding agents and human developers.
- Reduced Precomp previews to 10fps with fewer proxy frames.
- Updated preview sampling and timing regressions for 10fps playback.
- Updated preview regressions to verify spinner loading and cleanup.
- Flattened Precomp JPEG caches into job folders with unique suffixes.
- Preserved existing proxy caches through automatic filename migration.

### Fixed

- Fixed Generate Now crashing when Qt forwarded button state.
- Kept Toolbox Matte staging, backups and locks inside .cache.
- Restored Assets left-click opening folders; retained right-click matchmove options.
- Recognized mixed EXR versions and incomplete sequences for previews.

## 4.4.5 - 2026-10-23

### Added

- Added Windows regressions and native Linux process lifecycle coverage.
- Added live Nuke detector debug output and settings coverage.
- Added configurable Nuke detector interval with live settings coverage.
- Added regressions protecting foreign locks during local cleanup.
- Added Settings buttons clearing this machine's stale or active locks.
- Added cleanup tests covering ownership, heartbeats, failures, and UI updates.

### Fixed

- Cleared unmatched local locks despite unidentified Nuke sessions.
- Matched Windows usernames, mapped drives, UNC paths, and Linux mounts.
- Stopped heartbeats for unmatched local locks during release grace.

## 4.4.4 - 2026-10-23

### Added

- Added regressions for stale lock recovery and identity persistence.
- Added configurable Nuke lock clearing and settings regression coverage.

### Fixed

- Persisted Nuke ownership identities across restarts and lookup failures.
- Recovered stale local locks using their original ownership identities.

## 4.4.3 - 2026-10-14

### Added

- Added regressions for Nuke ownership, heartbeats, launches, and release recovery.

### Changed

- Moved Nuke lock maintenance and claims into a dedicated worker.
- Updated detector test imports for current frontend layout.

### Fixed

- Updated stale lock displays when unchanged heartbeats age.
- Cleaned owned stale Nuke locks across all jobs.
- Preserved stale status until an open Nuke script is confirmed.
- Unified Nuke ownership checks and preserved original heartbeat owners.
- Preserved Nuke lock tracking across version changes and restarts.
- Kept stale Nuke locks and their owners visible.
- Filled shot grid width and preserved left-to-right row ordering.
- Improved grid text contrast, control sizing, and responsive task wrapping.
- Restored jobs dropdown sizing after asynchronous loading and refreshes.
- Made dashboard filter labels readable without clipping.
- Restored single-line filters, separating spacer, and uniform box typography.
- Protected Nuke locks when process detection is uncertain.
- Fixed Nuke Dash filters, hidden shots, and removed jobs.
- Moved dashboard requests into workers; cached artist lookups.
- Made Nuke Dash task creation atomic.
- Corrected preview overwrite, plate selection, cancellation, and save retries.
- Restored scroll positions, thumbnail clearing, and independent refresh pauses.
- Cancelled dashboard callbacks and safely stopped workers during shutdown.
- Fixed assignment moves, rollback, and board data synchronisation.
- Created tasks atomically; corrected deletion counts and stale selections.
- Restored thumbnails; removed repeated artist requests and rejected invalid drops.
- Prevented Create Task fields clipping on differently scaled monitors.

## 4.4.2 - 2026-10-07

### Added

- better review page

### Fixed

- small ui fixes

## 4.4.1 - 2026-10-05

### Added

- Clicking shot thumbnails opens Quick View.
- Update shot thumbnails from the current Quick View frame.
- Click Quick View videos to toggle play and pause.
- Added Thumbs button to refresh all current job thumbnails.

### Fixed

- Resolve activity users after configuring the saved server.
- Apply activity user changes immediately; clear caches between servers.
- Enter creates tasks, including after preset and artist clicks.

## 4.4.0 - 2026-10-05

### Added

- Added optional staggered grid with responsive shot cards.

### Changed

- Sized Nuke buttons to text, giving renders remaining space.
- Renamed grid and card presentation to v02, preserving preferences.
- Moved Assets and Precomp onto v02 cards' second action line.
- Reduced v02 checklist row height and spacing.

### Fixed

- Preserved original clip filenames with width-aware middle ellipsis.
- Kept Nuke and render filenames on single lines.
- Preserved layout attachment when switching shot views.
- Cancelled completion flashes when task widgets are removed.
- Stopped sizing probes shrinking thumbnails and triggering flicker.
- Applied v02 card presentation during initial job loading.

## 4.3.1 - 2026-09-24

### Added

- Settings save automatically, including when leaving or quitting.

## 4.3.0 - 2026-09-22

### Added

- plugins for 3de updater

### Changed

- better settings layout

## 4.2.2 - 2026-09-21

### Added

- vnum

## 4.2.1 - 2026-09-21

### Fixed

- bump test

## 4.2.0 - 2026-09-21

### Added

- auto updater
- toolbox for extra pipeline tools and stuff

### Changed

- old jobs dont show any more ctrl by django admin page hide tage

## 4.1.1 - 2026-09-03

### Changed

- press space on the export to open in default viewer
- small changes to ui

## 4.1.0 - 2026-09-02

### Changed

- quick view hold frame when paused and version is changed

## 4.0.0 - 2026-09-02

### Added

- add Quick View previews to NukeDash thumbnails
- - open Quick View with Space while hovering a thumbnail
- - support video playback with still-image fallback
- - add responsive scrubbing, seeking, mute and looping
- - add scroll-to-zoom, drag-to-pan and centre-based resizing
- - navigate shots with Left/Right
- - navigate preview versions with Up/Down
- - keep the original thumbnail at the top of the version stack
- - add J/K/L reverse, stop and forward playback
- - add comma/period single-frame stepping
- - preserve playback positions between inline and popup previews

## 3.3.3 - 2026-09-02

### Changed

- added sRGB Encoded Rec.709 (sRGB)
- changed to referance from one list  presets
- default is   Input - ARRI - V4 LogC (EI800) - Wide Gamut4

## 3.3.2 - 2026-08-20

### Fixed

- fixed clour spacce when making nk scripts from xml will now use colour spcae selected, again

## 3.3.1 - 2026-08-11

### Added

- path mapping for projects B

## 3.3.0 - 2026-07-27

### Added

- server path remapping, thanks adobe

## 3.2.0 - 2026-07-27

### Changed

- changed the way you star a 3de project and make it more clear to see when a shot or job has a 3de project

## 3.1.0 - 2026-07-13

### Added

- back to work

## 3.0.0 - 2026-04-30

### Added

- mac support! for paul

## 2.0.5 - 2026-04-30

### Fixed

- patch

## 2.0.4 - 2026-04-24

### Fixed

- push to dvr fix

## 2.0.3 - 2026-04-20

### Changed

- better nuke 17 template
- nuke in use says username not machione name

## 2.0.2 - 2026-04-07

### Fixed

- nk template write pathing fix

## 2.0.1 - 2026-04-07

### Fixed

- nuke template fix

## 2.0.0 - 2026-04-07

### Added

- todo list ui option to task cards. and progress buttons

### Fixed

- match move bug

## 1.6.0 - 2026-04-02

### Added

- added check list feature ui

### Changed

- filters on at boot

## 1.5.0 - 2026-03-29

### Added

- timeline bnased 3de

### Changed

- better previews inc fromeexrs

## 1.4.3 - 2026-03-29

### Fixed

- fix

## 1.4.2 - 2026-03-29

### Fixed

- windows fix

## 1.4.1 - 2026-03-29

### Fixed

- fix 3de for win

## 1.4.0 - 2026-03-29

### Added

- 3de matchmove project maker and opening throiugh right clicking assets when a exr is detexted in precomp

### Changed

- made xml logic more robust and can now handle time outs with tries

## 1.3.1 - 2026-03-29

### Added

- Added an explicit Input transform / colourspace selector to the single-shot creation dialog in import_xml_v2.py.
- The single-shot flow now uses the dialog-selected colourspace for .nk creation instead of always using the page default.
- Single-shot creation now supports up to 5 clips in one shot, with the first clip used as the primary/V1 plate and additional clips mapped to V2-V5.

## 1.3.0 - 2026-03-29

### Added

- Import payloads now keep original_clip as the primary copied plate and support original_clips for the full copied-plate list.

### Changed

- XML import now builds shots under VFX instead of nuke, using VFX/timeline>/shot>/....
- Imported source clips are copied into each shot’s local plates/ folder, with duplicate filenames resolved safely, so generated .nk files reference local media instead of external paths.
- New import-created scripts and thumbnails now start at v001 with 3-digit version padding.
- Legacy shot .txt / note-file creation was removed from the import flow.
- XML shot naming was relaxed so prefixes are no longer treated as 3 characters only; 3-digit shot numbering remains (010, 020, etc.) and final names are validated against the shot title limit.

## 1.2.0 - 2026-03-28

### Changed

- Improved Assignment Board sync with NukeDash so current job and timeline selection stay aligned when the board opens or refreshes.
- Added a Show hidden checkbox to Assignment Board so hidden tasks can be displayed without changing the Hide done/approved filter.
- Added a Stop auto refresh checkbox to Assignment Board to pause NukeDash auto-refresh directly from the board.
- Updated refresh pause handling so drag-and-drop pauses and the new manual auto-refresh pause work together correctly.
- Added focused test coverage for Assignment Board job sync, hidden-task filtering, and auto-refresh pause behavior.

### Fixed

- Fixed Assignment Board startup sync so it now loads the same active job as NukeDash instead of defaulting to the first job.

## 1.1.0 - 2026-03-28

### Added

- change log in settings

## 1.0.1 - 2026-03-28

### Fixed

- fixed gap in tasks load when filterd away

## 1.0.0 - 2026-03-28

### Changed

- Reduced task-load cost in Nuke Dash by switching task creation from eager all-at-once rendering to queued incremental materialization. Tasks are now built in small batches, and only for the current timeline instead of every timeline in the project.
- Also added a simple task-load status label in the UI so it shows Loading tasks... while the current timeline is filling in, then Tasks loaded when finished.

## 0.1.4 - 2026-03-28

### Changed

- nothing this is just a test

## 0.1.3 - 2026-03-28

### Fixed

- windows updater

## 0.1.2 - 2026-03-28

### Added

- release manager

## 0.1.1 - 2026-03-28


### Fixed

- windows installer.

## 0.1.0 - 2026-03-28

### Added

- Initial frontend publish versioning with a semantic app version and git commit display.
- Settings page update controls for checking GitHub and launching a self-update.

### Changed

- Windows source install, run, and update scripts now target the current frontend repo layout.
- Added a source-based Linux updater script for git-backed frontend installs.

### Fixed

- Update detection now distinguishes up-to-date, behind, ahead, and diverged git states.
