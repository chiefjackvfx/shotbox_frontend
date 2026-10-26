# Native Nuke properties layout verification

These screenshots record the initial UI cleanup. The subsequent
[named Read workflow](NUKE_READBACK_VERIFICATION.md) adds the naming dialog and
standalone output, and moves the Group-only preview controls into Advanced.

Verified on **23 October 2026**, using commercial **Nuke 17.1v2 on Linux**
with its actual native knob controls and PySide6 6.5.3. No inference was run for
this UI change.

The main ShotBox tab now separates source, generation and result controls.
Generate/Cancel share their own row; output-folder/log buttons use the next row.
Precomp contains storage, paths and saved-result recovery. Advanced contains
processing, capture information, configuration and worker recovery. Status and
notes wrap within the panel.

| Native screenshot | 650 px wide | 480 px wide |
| --- | --- | --- |
| ShotBox | [Normal](nuke-ui-650-shotbox.png) | [Narrow](nuke-ui-480-shotbox.png) |
| Precomp | [Normal](nuke-ui-650-precomp.png) | [Narrow](nuke-ui-480-precomp.png) |
| Advanced | [Normal](nuke-ui-650-advanced.png) | [Narrow](nuke-ui-480-advanced.png) |

Screenshots use a native floating properties window created with
`node.showControlPanel(True)`, captured with `QWidget.grab()`. Window height
remains fixed across tabs because Nuke can retain a properties-card height
when its outer floating window shrinks. Both widths were checked visually and
all visible button rectangles fit within the window. Tabs are exactly
ShotBox, Precomp, Advanced and Nuke's built-in Node tab, with no duplicate or
blank user tab. See [nuke-ui-gui.json](nuke-ui-gui.json).

[nuke-ui-host.json](nuke-ui-host.json) records actual host migration tests. A
previous-layout native Group retains generation/storage values, hidden cache
data, UUID, seed animation, a root-knob expression, all action commands and
internal graph expressions after rearrangement, repeated application, and
save/reopen. Opening an actual never-generated previous-layout script applies
the new layout automatically. Disabled controls, custom-cache visibility and
the saved live-input toggle also remain correct.

[nuke-ui-cached-render.json](nuke-ui-cached-render.json) records the existing
ACEScg review comp reopening after UI migration and rendering cached frames
1001–1003 with the external interpreter unavailable and live input disconnected.
The separate repository regression run passed 562 tests and 13 subtests, with
Ruff clean. Earlier full-pixel/precomp evidence is documented in
[NUKE_PRECOMP_VERIFICATION.md](NUKE_PRECOMP_VERIFICATION.md).

Repeat from the repository root:

```bash
/opt/Nuke17.1v2/Nuke17.1 -t integrations/nuke/tests_nuke/ui_smoke.py \
  --report docs/nuke-ui-host.json
.venv/bin/python integrations/nuke/tests_nuke/inspect_ui.py --output docs
```

The GUI harness creates an isolated fixture and temporary startup path; it
does not edit preferences. Windows, other Nuke releases and other display
scales remain unverified. These checks verify layout and saved data integrity;
they do not add an artistic matte-quality claim.
