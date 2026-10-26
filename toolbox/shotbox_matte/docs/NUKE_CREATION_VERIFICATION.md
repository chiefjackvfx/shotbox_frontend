# ShotBoxMatte creation verification

Verified in the commercial Nuke 17.1v2 GUI on Linux, 23 October 2026.
[Native evidence](nuke-creation-gui.json) records all three passing cases.

With a selected source, ShotBoxMatte matches native NoOp placement, connects
its input and inserts into the existing downstream pipe. Both native and
ShotBox nodes appeared at (1200, 824) beneath the source at (1200, 800).
The same comparison passed inside a Group. With nothing selected, the node
uses native free-node placement and leaves existing connections alone. Nuke
cascades successive unselected drops, so those coordinates need not repeat.

The completed ShotBoxMatte is selected with its properties open. Creation
leaves no temporary node, preserves ACEScg and matte-only defaults, and one
Undo/Redo removes/restores the complete node and its connections. No worker,
render or GPU inference was started. Headless programmatic creation retains
its existing behavior.

Repeat from the repository root:

```bash
.venv/bin/python integrations/nuke/tests_nuke/creation_gui.py
```
