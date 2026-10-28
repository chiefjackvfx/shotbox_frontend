# Import Timeline

Enable **Settings → Startup → Enable Import Timeline Tab**, then restart Shotbox.
XML Import V2 keeps its existing tab, settings and import behavior. No backend
changes or migrations are needed.

Drop one media-managed folder anywhere on the Import Timeline panel, including
the table or text fields, or use **Select Folder**. Scanning and parsing start
automatically. Shotbox scans subfolders for video media, Resolve DRT, xmeml XML
and OTIO timelines, without following symlinks. A single DRT is preferred when
all discovered exports share its filename stem. DRTs alongside differently named
edits require a choice. Without DRT, a single XML is preferred over OTIO; a sole
OTIO is selected when there is no XML. Only the selected file supplies timing,
with no automatic comparison or timing merge. Folder drops are ignored while a
scan or import is running. Any folder may be used.

DRT import reads the archive directly without extracting or changing it. It
supports the normal-speed and two-point forward constant-speed timing encodings
validated against a Resolve 21.1 export. Cut positions, gaps, repeated media and
source cut anchors are preserved. Unknown retime encodings, speed ramps, reverse
or freeze timing, unsupported clip/track flags, transitions and nested timeline
items require review or an XML/OTIO export. Resolve grades and Fusion effects
are not recreated. Other Resolve export versions may use unsupported encodings.

Choose the project destination, naming, input transform, handle settings and
existing database/task options. **Timeline name** starts with the loaded timeline
name and can be edited before import. It controls the imported timeline name and
its output folder; the source file stays unchanged. Loading another timeline
resets this field to that timeline’s name. Review the preview before importing:

- **Detect input colour space** is enabled by default. Supported
  explicit colour-space tags take priority over camera-model inference. The
  detected transform and reason are shown; each clip can be overridden in the
  IDT column. Disable detection to apply the editable global transform instead.
- ARRI camera-model inference distinguishes LogC3 cameras such as ALEXA Mini LF
  from LogC4 cameras such as ALEXA 35/265, following
  [ARRI's camera guidance](https://www.arri.com/en/learn/camera-systems/image-science/look-files).
  Camera inference assumes the export retained native encoding; LogC3 uses the
  existing EI800 preset and labels that assumption. Explicit conflicting tags,
  unsupported spaces or non-800 LogC3 EI values require manual selection.
  Mixed-camera layers retain their own input transforms in the Nuke Read nodes.
- Exact reference/file names are matched first, then unique case-insensitive
  names. Duplicate filenames require review unless timecode coverage uniquely
  identifies a compatible candidate. Stem/reel suggestions require confirmation.
- Browse can explicitly select replacement media outside the folder. Shotbox
  never automatically reads the timeline's original media locations.
- Source and local start timecodes determine local cut positions. The displayed
  source offset is zero-based. Missing timecodes can be corrected by entering
  a local cut start; incompatible frame rates are blocked.
- Select DRT, XML or OTIO in **Timeline to import**. That file supplies both
  shot structure and timing, including retimed source starts and speeds. Switching
  files rebuilds the preview and clears previous matching/range overrides. Other
  timelines do not contribute timing, participate in comparison, or block import.
- **Detect handles from media** is on by default. Head/tail availability is
  measured from local source offsets and media lengths in timeline frames,
  accounting for speed. The common availability across every layer is used
  automatically; uneven counts are supported and editable per shot. No 10/25
  assumption or global cap is applied in detection mode. Shared clips can have
  additional usable frames beyond the original exported handle allocation;
  review/reduce those counts if needed. Missing or unsupported timing still
  blocks import. **Reset to detected handles** restores measured counts.
- Disable detection to request a fixed number of handles instead. Shortages in
  this manual mode require explicitly accepting smaller counts or longer media.
- An unresolved shot can be explicitly excluded and later restored without
  renumbering the other shots.

Import validates reviewed files again before creating anything and checks for
changes while copying. Existing Nuke scripts block import rather than silently
reusing scripts with potentially different media/timing. Choose a different
destination or shot prefix if scripts already exist.

The shared XML pipeline copies local clips to each shot's `plates`, generates a
cut thumbnail, creates the usual folder scaffold and Nuke script, and optionally
uses the existing Shotbox API for database entries/tasks. **Make Single Shot**
retains its existing standalone clip-selection workflow.

Detected camera make/model, lens name and focal length are added to each
imported shot's notes and the Nuke script's info backdrop. Camera make/model
use a single line, for example `Camera: ARRI ALEXA Mini LF`. Artist notes
are preserved and repeated imports do not duplicate metadata lines. Different
cameras or lenses on secondary layers are labelled by video track; missing
values are omitted.
Clips with a non-100% speed also include a `Retime: 50%` line (or, for example,
`V2 Retime: 50%` on a secondary layer) in both notes and the info backdrop.

Resolve-managed MXFs can preserve a binary ARRI V3 camera header that ffprobe
does not expose. The frontend reads validated header snapshots from bounded MXF
metadata blocks, following the [ARRI Metadata White Paper, pages 55–59](https://www.arri.com/resource/blob/31912/e30dd1a324dbecbced88fa146d3f88a9/2020-11-arri-metadata-white-paper-en-data.pdf).
This needs no additional library and does not scan image essence or modify media.
Lens names are retained as recorded, and focal lengths are converted to mm.
Exposure index and white balance, when present, are retained in the import sidecar;
known exposure index also informs existing input-transform validation. The header
is a snapshot, not per-frame lens animation or a claim about a retimed edit frame.
Unsupported, truncated, conflicting or absent headers simply omit these optional
fields. Other cameras can supply supported lens-name/focal-length tags normally.

MXF thumbnails use PyAV decoding, including camera clips whose invalid colour
tags otherwise produce black OpenCV images. Camera-plate thumbnails use the
reviewed per-clip IDT (including manual overrides), falling back to metadata
detection when no reviewed transform exists. The existing OpenColorIO dependency
applies a [display/view transform](https://opencolorio.readthedocs.io/en/latest/guides/developing/developing.html)
using built-in ACES 2.0 Studio configuration, **sRGB - Display → ACES 2.0 - SDR
100 nits (Rec.709)**. RGB is decoded at 16-bit precision and transformed in float
before quantizing to the saved display thumbnail, reusing Review's conversion
code. This requires no Nuke process or extra library installation. Unsupported
requested transforms fail rather than saving an uncorrected thumbnail.

The dashboard's **thumbs** tool
prefers a rendered preview and can use the identified primary plate when no
preview exists. With import metadata, it captures the middle of that occurrence's
cut and uses that plate's reviewed input transform from either asset-folder
spelling. Already rendered previews receive no additional camera IDT/display
transform. It does not substitute a secondary plate for a missing primary clip.
Run **thumbs** again to replace previously generated flat or black thumbnails.

## Timing and metadata

The working media range always begins at Nuke frame **1001**. The edit cut starts
at `1001 + head_handles` and ends inclusively at `cut_start + cut_duration - 1`.
For a 159-frame cut with 10 handles on each end, working range is 1001–1179 and
edit in/out is **1011–1169**. The Viewer in/out playback range is locked to that
cut; the project range retains the handles.

Complete local media is copied. At 100% speed the template's existing Retime
setup is retained for frame numbering; no replacement TimeWarp is created.
Changed-speed plates use a linear TimeWarp preserving fractional source anchors.
Other template connections are preserved. No source DRT/XML/OTIO files are rewritten.

New asset directories use `job_assets`, `timeline_assets` and `shot_assets`.
Existing `Job_Assets`, `Timeline_Assets` and `Shot_Assets` directories are reused
without renaming or duplicating them. Imports, single-shot creation, asset
buttons, matchmove and thumbnail sidecar lookup share this convention. When both
spellings exist, lowercase is preferred, with fallback to an existing requested
file or subfolder in the title-case directory.

Each timeline shot contains `shot_assets/timeline_import.json`, schema version 3
(or the existing legacy `Shot_Assets` directory):

- Cut/working duration, timeline FPS, detected and used head/tail handles.
- Working start/end, inclusive Nuke cut in/out, and editorial timeline in/out
  (timeline out remains exclusive). `requested_handles` is null
  in detection mode and a number for a manual request.
- Original reference, selected local/copy paths, track, source position, speed,
  timing source and reviewed media fingerprint per plate, plus detected camera,
  chosen input transform and colour-space detection/override provenance.

Existing database fields are used. `duration` records working duration.
`edit_inpoint`/`edit_outpoint` record timeline-relative editorial cut positions,
matching XML Import V2, with an exclusive out point. Changing handles does not
change these values. The preview and info backdrop's `edit` line show these
editorial positions. Nuke's inclusive `cut_first`/`cut_last` are separate and
control the Viewer cut range inside the working range starting at 1001.
Schema 3 corrects schema 2's meaning of the `edit_inpoint`/`edit_outpoint` keys;
the `cut_first`/`cut_last` and `timeline_inpoint`/`timeline_outpoint` keys retain
their meanings. In automatic mode the legacy scalar `handles` records the
smaller used head/tail count; in manual mode it retains the requested count.
Exact detected/used head/tail counts and original timeline positions live in the
frontend JSON sidecar and generated script. No new server fields are required.

## Requirements and limits

- FFmpeg's `ffprobe` must be available on PATH; probes are cancellable and have
  a 30-second timeout. OTIO requires `opentimelineio>=0.17,<0.19`, included in
  frontend requirements. XML remains usable without the optional OTIO module.
  Setup verifies OTIO in the frontend's own `venv`. If an existing installation
  reports a missing module, run `setup_runtime.sh` (Linux/macOS) or
  `setup_runtime.ps1` (Windows), then restart Shotbox. Import errors identify the
  interpreter and original loading failure to distinguish another Python environment.
- Video clips are grouped using V1 as primary, with aligned upper tracks.
  V1–V16 are supported through media preparation and Nuke plate creation.
  Occupied tracks above V16 block import with an error; empty higher tracks
  are ignored. Upper clips must match a
  V1 cut’s start and duration; empty tracks preserve their track numbers.
  Unaligned layers, transitions, nested timelines, mixed source/timeline rates,
  reverse/freeze effects and variable-speed retimes are reported as unsupported.
- Forward constant-speed retimes are supported. Audio, grading, transforms and
  optical-flow rendering are not reproduced. Compare against editorial reference
  renders when exact Resolve retime appearance matters.
- Disposable test/working artifacts use the frontend `.cache/temp` category.

## Verification

Automated tests cover generic synthetic folders, filename collisions, missing
media, local range offsets, selected-timeline timing, shortage acceptance, unchanged API
payloads, sidecar output and legacy XML/single-shot behavior. UI tests exercise
folder/timeline selection, review and validation gating.

An opt-in native check accepts an example folder with one XML/OTIO pair:

```sh
python test/timeline_native_acceptance.py --folder /path/to/example --nuke /path/to/Nuke
```

It generates disposable scripts referencing source media, checks XML and OTIO
independently, tests media decoding at cut/handle boundaries, and measures fractional
frame mapping using frame-coded pixels. It does not copy footage or create
database entries. Comparing final renders to Resolve requires an editorial
reference export and is a separate visual check.
