# Changelog

All notable frontend publish changes should be documented in this file.

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

- XML import now builds shots under VFX instead of nuke, using VFX/<timeline>/<shot>/....
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
