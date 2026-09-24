# Development changelog

After each development task, use `$update-app-changelog` to record meaningful completed changes in `CHANGELOG.md` under `## Unreleased`, grouped as Added, Changed, and Fixed. If the personal skill is unavailable, follow these instructions directly: use single-line bullets prefixed with the actual Europe/London timestamp and UTC offset (`[YYYY-MM-DDTHH:MM:SS+HH:MM]`), avoid duplicates and unrelated pre-existing work, and preserve released history. Include internal maintenance, tests, and tooling as well as user-facing changes.

Development tasks only update pending notes. Do not bump versions, commit, tag, launch `release_manager.py`, or publish automatically. The user runs the release manager, reviews notes, chooses a version, and uses Commit Release and Push Release explicitly.
