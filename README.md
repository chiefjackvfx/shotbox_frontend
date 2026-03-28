# ShotBox Frontend

PyQt6 desktop frontend for ShotBox.

## Entry Point

- Run the app from `main.py`.

## Main Live Modules

- `main.py` wires the application tabs and startup behavior.
- `page_nukedash.py` is the primary task / Nuke dashboard.
- `page_assignment_board.py` is the active assignment board implementation.
- `review_page.py`, `activity_page.py`, and `import_xml_v2.py` are active feature pages.

## Cleanup Notes

- Old backup, copy, and prototype files have been removed from this repo.
- `rockybtw_settings.yaml` is intentionally kept in the repo.
- Python cache files are ignored and can be deleted locally at any time.

## Tests

- Current targeted smoke test:
  `QT_QPA_PLATFORM=offscreen python -m unittest shotbox_frontend/test/test_page_nukedash_status_filter.py`
- Two legacy tests still reference `pyqt_frontend.*` imports and are not yet aligned with the current repo layout.

## Release Direction

- GitHub publishing and packaged auto-update support are planned as a separate follow-up step.
