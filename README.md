# ShotBox Frontend

PyQt6 desktop frontend for ShotBox.

## Entry Point

- Run the app from `main.py`.

## Cache and temporary files

Runtime caches and disposable working files live in `.cache` beside `main.py`,
including on Windows and macOS. EXR preview proxies use `.cache/exr-previews-v1`;
thumbnail uploads, exports, and Nuke/3DE requests use `.cache/temp`.
Proxy JPEGs sit directly inside each job folder, named
`original_frame_name_<unique_id>.jpg`. The stable suffix identifies the source
path, keeping matching names from different shots and sequences separate.
Source-change checks are stored inside the JPEG metadata. Existing hashed or
nested proxies migrate when their sequences reopen.
Precomp EXR previews sample at 10 fps to reduce preparation work while retaining
the shot's normal timing.

Use **Settings → Cache → Clear Cache** to remove cached previews and unused
temporary files. The button clears in the background, stops preview preparation,
and keeps temporary files needed by running exports or external applications.
Previews rebuild when opened again. The `.cache` directory is ignored by Git.
**Open Cache Folder** opens that directory in your system's file browser.

**Development rule for Codex, other coding agents, and human developers:** all
new runtime temporary files and disk caches must use the shared `app_cache`
helpers and the frontend `.cache` folder. Follow the cleanup requirements in
[AGENTS.md](AGENTS.md#runtime-cache-and-temporary-files).

## Main Live Modules

- `main.py` wires the application tabs and startup behavior.
- `page_nukedash.py` is the primary task / Nuke dashboard.
- `page_assignment_board.py` is the active assignment board implementation.
- `review_page.py`, `activity_page.py`, and `import_xml_v2.py` are active feature pages.

## Review renders and colour management

Use **Play Render** to play the latest MOV or EXR sequence in a shot's
`renders/comp` folder. While viewing a render, the button changes to **Play Preview**
to return to the chosen preview at the current playback time. If a shot has only
previews or only renders, the button plays the selected media. The **Media**
selector lists versions for the active mode: previews in preview mode and
renders in render mode. Shots without previews remain available for render review.
Switch versions with the selector or Up/Down to keep the playback time and
paused/playing state, as in Quick View. Shorter versions clamp to their last
frame; moving to another shot starts at the beginning.

For a supervisor and team watching together, choose shots from **Review** or use
**Previous/Next** (Page Up/Page Down). Render mode stays active between shots;
a missing render stops playback and offers an explicit switch to the preview.
**Play Timeline** plays from the current shot through the remaining shots, stops
at missing media, and restores your loop setting when finished. Manual shot or
version selection ends timeline playback. **Loop** repeats a shot or its In/Out
range. Use `[` and `]` to set the range at the current frame and `\` to reset it.
Version comparisons keep that range at the same playback times; changing shots
resets the range to the whole shot.

**Present** (F11) opens the same player full-screen, retaining playback position
and the visible shot, version, and transport. Escape returns to the normal tab.
**Drawing** reveals the annotation tools; selecting a drawing tool pauses
playback. EXR frame counters use the sequence's source numbers; movie counters
start at 1001. Review automatically loads the active range into its frame cache,
including while paused. Pressing Play waits for those frames to load, then plays
the cached images at the selected FPS. EXR reads, movie decoding, and OCIO
processing run during loading; cached loops and frame stepping reuse the images.
The cache row shows loaded frames and RAM usage; **Load Frames** loads the current
range and retries unavailable frames. **Cache RAM** defaults to 1 GB, can be raised
up to 8 GB, and is remembered in workstation settings. Ranges larger than the
budget load in sections, with playback holding its frame while the next section
loads. Increase RAM or shorten In/Out to keep a whole range cached. Changing
sources, resolution, or OCIO settings rebuilds the cache. Pausing during loading
cancels automatic playback while frame loading continues.

Up to three background reads run together within the display-cache budget.
Ordinary RGB(A) EXRs use a decoder that lets the interface stay responsive;
layered EXRs read their RGB channels together without loading unrelated passes.
Loading time depends on storage and CPU speed. Review currently plays
images only; movie audio is not played.

Use **Resolution** beside the playback status to choose **Full**, **Half**,
**Quarter**, or **Eighth**. Full is the default for detailed QC. Lower settings
reduce the displayed pixel dimensions before OCIO processing, which helps with
expensive transforms such as ACES 2. Changing resolution retains the current
frame, playing/paused state, and In/Out range. Return to Full to inspect
fine detail. EXR decompression still reads the source frame at its original size.

Click **OCIO** to expand the colour controls. Choose an `.ocio` file, the `OCIO`
environment configuration, or the built-in ACES studio configuration, then select
Input, Display, View, and Look. EXRs default to the shot colourspace or ACEScg;
movies default to sRGB when the configuration provides that space. Input choices
are remembered separately for EXRs and movies. The OCIO configuration and
selections are saved with the existing application settings.

OCIO starts off unless saved settings or an environment configuration enable it.
With OCIO off, floating point EXRs are clipped for display without a colour
transform. With it enabled, the display transform runs on floating point pixels
before screen conversion. It does not modify render files. EXR reads run in the
background with a bounded display-frame cache. The **FPS** control sets playback speed without
changing the source's frame count. EXR sequences use the current rate, initially
24 fps; movies start at their encoded frame rate.

Review selectors use available row space and open wider menus for long names.
Long selected values use ellipses, retaining render version suffixes; hover for
the full label and source path. Popup menus stay within the current screen and
support arrow-key selection without triggering Review playback shortcuts.

Install the updated `requirements.txt` for the OpenColorIO Python bindings.
The pipeline uses the official [OCIO viewing API](https://opencolorio.readthedocs.io/en/latest/api/apphelpers.html)
and [OpenEXR Python reader](https://openexr.com/en/latest/python.html).


## Shotbox Matte Toolbox and Nuke integration

Open **Toolbox → Open Shotbox Matte** for the owned Matte pop-out. Its source
and backend live in `toolbox/shotbox_matte/`; the Nuke host integration lives in
`plugins/nuke/`. Both ship with the frontend, without model weights or a copied
inference environment.

In **Settings → Plugins → Shotbox Matte / Nuke**, prepare the separate GPU
runtime, choose an external model library, save configuration and install the
Nuke integration. Restart Nuke after installation or integration updates.
The installer preserves unrelated startup code and shares runtime/model settings
with Nuke. Shotbox remains open while Matte or runtime preparation is busy.

Matte's worker files, staged EXRs, overwrite backups and export locks use
`.cache/temp/shotbox-matte/`, including retained partial frames after cancellation
or failure. Exporting to another drive or network mount copies files from this
cache to the final output. Setup downloads and default Nuke jobs also use
`.cache/temp/`; active desktop and Nuke sessions protect their files during
Settings → Cache → Clear Cache. `.cache/.lease.lock` is retained as coordination
metadata. Final mattes are written to the chosen production output locations.
See [Matte setup](toolbox/shotbox_matte/README.md) and
[Nuke installation](plugins/nuke/README.md).

[Linux integration verification](toolbox/shotbox_matte/docs/SHOTBOX_INTEGRATION_VERIFICATION.md) records the relocated runtime checks and platform limits.
