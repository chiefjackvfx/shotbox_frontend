# Changelog

All notable frontend publish changes should be documented in this file.

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
