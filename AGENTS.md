# Development changelog

After each development task, use `$update-app-changelog` to record meaningful completed changes in `CHANGELOG.md` under `## Unreleased`, grouped as Added, Changed, and Fixed. If the personal skill is unavailable, follow these instructions directly: use single-line bullets prefixed with the actual Europe/London timestamp and UTC offset (`[YYYY-MM-DDTHH:MM:SS+HH:MM]`), avoid duplicates and unrelated pre-existing work, and preserve released history. Limit each note to 10 words, excluding its timestamp and bullet marker. Include internal maintenance, tests, and tooling as well as user-facing changes. Keep timestamps in pending notes; omit them from release-manager fields and new published notes.

Development tasks only update pending notes. Do not bump versions, commit, tag, launch `release_manager.py`, or publish automatically. The user runs the release manager, reviews notes, chooses a version, and uses Commit Release and Push Release explicitly.

# Runtime cache and temporary files

This development rule applies to Codex, other coding agents, and all human developers working on the frontend.

All new frontend features must store disposable runtime temporary files and disk caches under `shotbox_frontend/.cache`, resolved relative to the frontend installation on every platform.

Use `app_cache.cache_directory("temp")` for working files and pass it explicitly as `dir=` to `TemporaryDirectory`, `NamedTemporaryFile`, or `mkstemp`. Use a named category such as `app_cache.cache_directory("exr-previews-v1")` for reusable disk caches. Avoid system temporary-directory defaults and hardcoded machine paths.

Decorate operations that need temporary files while running with `@app_cache.temporary_cache_task`, so Settings → Cache → Clear Cache preserves those files. New background cache writers must support cancellation and finish outstanding writes before cache clearing deletes their files; clear associated memory caches as well. Keep `.cache/` ignored by Git and document any new cache category when adding it.
