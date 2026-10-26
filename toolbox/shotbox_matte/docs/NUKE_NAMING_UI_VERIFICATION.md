# Precomp naming dialog verification

Verified on **23 October 2026**, in the actual commercial **Nuke 17.1v2 GUI
on Linux**, using its bundled PySide6 6.5.3 and real Qt keyboard events.
No worker, generation or GPU inference was started.

The dialog now presents the name field, version badge, EXR filename and full
output-path preview together. Typing Space inserts `_` immediately; pasted
spaces, punctuation and non-ASCII characters also become underscores. Leading
and trailing underscores remain while editing and are trimmed on acceptance.

| Evidence | Screenshot/report |
| --- | --- |
| Normal width, 620 px | [Naming dialog](nuke-naming-ui-620.png) |
| Narrow width, 480 px | [Naming dialog](nuke-naming-ui-480.png) |
| Invalid name | [Inline validation](nuke-naming-ui-invalid.png) |
| Actual host assertions | [nuke-naming-ui.json](nuke-naming-ui.json) |

The native GUI run passed:

- Actual Generate opens the dialog; Cancel preserves the node's name, pending
  attempt, session and filesystem.
- Space changes `hero` to `hero_` while the field still has focus and does not
  trigger Generate. Typing, insertion in the middle, cursor position,
  Backspace, Delete and actual Ctrl+V paste all work with live sanitization.
- Empty and underscore-only names stay in the dialog with an inline error and
  Generate disabled. No attempt is allocated.
- Filename and full-path previews update while editing. An existing `v001`
  directory changes the preview to `v002`, without creating or reserving it.
- Enter accepts the normalized name through the actual prompt API;
  `__ hero matte __` becomes `hero_matte` on the node.
- Both displayed widths fit their buttons, version badge and wrapped full
  path. Screenshots were captured from the actual dialog and visually checked.

Repeat from the repository root:

```bash
.venv/bin/python integrations/nuke/tests_nuke/naming_dialog_gui.py --output docs
```

The launcher uses a temporary Nuke startup path and an isolated saved-script
fixture under `test-runs/nuke-naming-ui`. It does not edit user preferences.
It creates one empty test version directory to check collision previews;
there are no generated images or integration manifests. This verifies the
dialog only; prior Read/publication/pixel evidence remains in
[NUKE_READBACK_VERIFICATION.md](NUKE_READBACK_VERIFICATION.md). Windows, other
Nuke builds and other display scales remain unverified.
