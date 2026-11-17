# Desktop Timeline Review

Timeline Review is a separate, always-available top-level tab. Restart Shotbox
after updating to load it. Settings → Default Tab can select Timeline Review.
It uses the existing jobs, timelines, shots, users and tasks APIs. There are no
desktop API changes, migrations, hosted sessions, shared playback or custom cursors.
Web reference streaming uses a separate authenticated, read-only endpoint.

## Opening an edit

Select a job and timeline. The initially suggested job comes from Tasks if that
job has loaded before opening the tab. Subsequent selections are independent.
Refresh previews reloads API data and discovers files on the workstation using
the existing cross-platform project path mapping. Files must be accessible to
the current workstation; the new tab does not require browser login cookies.

Versioned MP4 files are discovered under each shot's
`renders/precomp/previews` directory. Numeric versions and legacy `_preview.mp4`
filenames follow the web review's selection rules. The latest version starts
selected. Older, Newer, Latest and the version menu change media without moving
the editorial playhead. Refresh preserves an explicitly selected version when
it still exists. Shot thumbnails provide a fallback when previews are missing.

The edit respects editorial positions and handles. Missing or overlapping edit
positions use the web review's sequential assembly fallback; the status line
identifies this. Actual gaps remain gaps. The common preview rate establishes
timeline FPS, with 25 fps as the metadata-free fallback. The timecode field is
relative to the beginning of the assembled timeline and uses nominal,
non-drop-frame timecode, matching the web view.

## Navigation and playback

- Click/drag the timeline to scrub; zoom and scroll horizontally or use Fit.
- Enter `HH:MM:SS:FF` and press Enter or Go to seek.
- Space or clicking the picture toggles playback.
- L plays forwards; J plays backwards; K stops. Press L or J again while
  playing in that direction for 2× speed. Changing direction starts at 1×.
- Left/Right select the previous/next shot; comma/period step one frame.
- Up/Down select older/newer preview versions.
- Home/End seek to the beginning/end. H hides/shows the current shot.
- Repeat shot supports ordinary looping and ping-pong playback.
- Scroll over the picture to zoom; middle-drag pans; middle-click or Fit resets.
- Picture zoom ranges from 10% to 1600% of fit. Timeline zoom can shrink to
  25% of the available width; its Fit button restores full-width display.
- F11 or Fullscreen retains the transport and timeline. Escape returns.

Playback is silent. Missing or undecodable previews stop playback with feedback;
gaps advance normally. Decode and speculative next-frame/next-cut loading run
outside the UI thread. Full/Half/Quarter/Eighth affect display resolution, and
Cache RAM bounds the image cache, apart from allowing one oversized frame.
Both controls are remembered. Clear frame cache or Settings → Cache → Clear Cache
invalidates pending decoding and releases cached frames and thumbnail copies.
No disk caches or temporary runtime files are created by this feature.

The existing Review tab continues to provide its MOV/EXR, OCIO and In/Out tools.
Timeline Review currently uses the web workflow's versioned MP4 previews.

## Offline reference overlay

The tab automatically reads `timeline.online_reference`, applies the application's
workstation path mappings, and loads that movie when selecting a timeline. The
saved path is never changed by the review controls. Missing or unreadable files
show an error without stopping primary playback; use **Offline ref…** to select a
local replacement. Refreshing an unchanged saved path preserves manual selection,
removal and overlay adjustments. A changed saved path is loaded on the next
preview refresh. Switching timelines loads that timeline's saved reference.

Use **Offline ref…** beneath the version controls to choose a locally accessible
reference movie. Its frame zero aligns with the beginning of the assembled
timeline, independently of embedded source timecode. **Reference** toggles it and
**Opacity** blends it over the picture (100% shows the reference alone). Both
images share picture zoom/pan and fullscreen; annotations remain above them.

Choose **Compare → Wipe** (or press **W** outside text fields) for a full-opacity
split between the reference and the selected render version. Initially the
reference is on the left. Drag the centre circle or dividing line to reposition
it; drag the outer circle to rotate it. **Angle** also sets rotation in degrees.
**Swap sides** reverses the comparison, and **Reset wipe** restores a centred,
vertical split. **W** or **Compare → Overlay** restores blending at the previous
opacity. Drawing tools take priority over wipe dragging; annotations stay visible.
The wipe follows zoom, pan and fullscreen, works during playback, and its settings
are local to this viewer. Wheel scrolling does not edit these controls.

**Offset** is measured in timeline frames: positive values delay reference frame
zero; negative values skip a reference leader. Reference FPS is converted against
the timeline FPS. Scrubbing, frame stepping, reverse playback and 2× playback
retain this alignment. Frames outside the reference range are hidden. The
reference status shows its filename, source frame and any load/decode errors.

Reference decoding runs separately from primary media loading. The last complete
picture/reference pair remains visible while either next frame is decoding, then
both images are replaced together. Playback waits
for an enabled reference frame so the overlay cannot show a different timeline
position from the picture. Reference errors do not stall primary playback.
Its decoder uses a separate 64 MB memory cache; Clear frame cache and Settings
→ Cache → Clear Cache invalidate it too. No temporary or cache files are created.

**Remove ref** releases the reference. Changing timelines or servers releases it before loading the new saved reference;
refreshing the same timeline retains it. Selection and alignment are local to
the current tab session and are not saved to the backend or reference file.

## Tasks and drawings

The right panel filters tasks to the current shot or all shots. Task markers
appear below their shots in the timeline; click a marker or a task's frame button
to seek to its context. Double-click a shot tile to start a task. Existing
imported replies are displayed; this view does not create reply threads.

Create/edit tasks with title, notes, artist, status, budget, priority and frame
scope. Title, notes, budget and priority presets are available. Task card status
and artist menus save immediately. The editor requires Create Task/Save changes.
Deletion asks for confirmation. Referenced versions can be loaded from the card.

The right sidebar follows the web view: compact cards with avatars, local dates,
small frame/version buttons, edit/delete icons and side-by-side status/artist
menus. Its composer stays beneath the comments with scope, title, notes and
compact drawing controls. Open **Task / shot details** for presets, budget,
priority and explicit frame-range fields; these live in the left **Task details**
tab. Editing a task, finishing a drawing or choosing Custom frame range opens
those details. Clear and Create Task remain at the bottom of the composer.

Whole-shot references save as null, single frames as integers, and inclusive
ranges as `[start, end]`. Current-frame-to-end and explicit ranges are supported.
Imported custom working-frame origins are retained when editing imported tasks.
Frame references outside a cut seek to the nearest playable frame with feedback.

A draft pins its original shot, version and frame context. Moving the playhead
does not retarget it. Return to draft shot restores its context; across timelines,
select the original timeline first. Editing a saved task temporarily preserves
the new-task draft, which returns after saving or cancelling the editor.
Drafts are held in memory, not persisted across app shutdown or server changes.

Drawing supports pen, arrow, line, rectangle, preset/custom colours, three stroke
widths, undo, redo and clear. Selecting a tool pauses playback. Done drawing
returns to task entry; only saving the task persists the drawing. Active or
unsaved drawings block playback until saved or cleared. Undo/redo operate on
the draft drawing; restoring drawings pauses playback again. Drawings use the
existing version-1 normalized annotation schema and remain aligned under picture
zoom, pan and resizing. Saved drawings display within their frame/version scope.

The left Shot details tab shows metadata, editable notes/colour and hide/show controls.
Both side panels can collapse. Inputs follow the application's wheel guard:
scrolling an input scrolls the page without changing its value.

While visible, the tab periodically refreshes task/shot data through the existing
timeline API. This preserves playback, version selection and local drafts.
Unchanged task data retains card widgets; stale refreshes cannot overwrite newer
local saves. Untouched editor status/artist fields receive remote updates, while
local selections remain intact. Failed saves retain the draft for retry.

## Validation

From `shotbox_frontend`:

```sh
QT_QPA_PLATFORM=offscreen venv/bin/python -m unittest test.test_timeline_review -q
```

The tests cover timing, handles, mixed rates, gaps, fallback assembly, frame
references, local MP4 discovery/decoding, cache eviction, playback across cuts,
draft preservation, drawing and API failures. Generated media lives in a protected
`app_cache` temporary task and is removed after testing. No live backend records
are created by these tests. Main-window integration and settings tests should
also pass before release. Live project-path, storage performance and Windows/macOS
interactive qualification remain necessary before treating those platforms as
verified.

Future review feature changes apply to both this tab and the web timeline review.
The web offline reference overlay uses browser-supported movie codecs; the desktop
uses its local decoder. Local references remain private to each viewer in both.
Desktop hosted/sync and custom cursor exclusions remain in place.
