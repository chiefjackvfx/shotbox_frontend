# Windows Neat Video controls

These three fixtures contain control strips from user-supplied screenshots of
Neat Video 6.1.3 Studio on Windows, in Beginner Mode, captured through a remote
desktop viewer on 27 October 2026. The client region was cropped at x=596,
y=124 (stage 1) or y=126 (stages 2 and 3), width=1374, height=1240.

- Stage 1: Auto Profile enabled; Adjust and Preview and Apply disabled.
- Stage 2: Build Profile, Adjust and Preview, Profile Check and Apply enabled.
- Stage 3: Build Profile remains visible; Profile Check is selected; Assist
  Image is visible; Apply remains enabled.

The desktop and source footage were excluded. Only the top control strip,
bottom-right buttons and the Assist Image label remain. Full captures are
disposable working files in `.cache/temp/denoise-windows-reference`.

Runtime templates are in `ui/denoise/windows`; `source.json` records source
filenames and crop rectangles in original screenshot coordinates. These are
remote-viewer pixels, not a claim of native Windows DPI. Tests replay all three
states at original size and at 80%, 93%, 125% and 150%, including enabled versus
disabled controls. Resampling tests cannot establish live capture/input or
every possible Windows font, display scaling and Neat theme combination.
