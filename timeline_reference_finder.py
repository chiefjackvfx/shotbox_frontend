"""Read-only reference discovery within a project's offline export folders."""
from pathlib import Path
import os
import re

from timeline_import import Cancelled

VIDEO_EXTENSIONS = {'.mov', '.mp4', '.mxf', '.avi', '.mkv', '.m4v'}


def calculate_reference_start(document, reference_frames):
    """Infer an export's timeline origin from its length and the full V1 span.

    When the movie exactly spans the cuts, its first frame maps to the
    earliest cut, regardless of lead-in length or timeline frame rate.
    Otherwise retain the full-timeline origin (zero); callers must validate
    coverage and allow manual alignment for slates or other trimmed exports.
    This infers timing, not whether the lead-in contains black pictures.
    """
    clips = [clip for clip in document.clips if clip.track == 1]
    if not clips or reference_frames <= 0:
        return 0
    first = min(clip.timeline_start for clip in clips)
    end = max(clip.timeline_start + clip.duration for clip in clips)
    return first if first > 0 and reference_frames == end - first else 0


def reference_stem(path):
    # Preserve edit versions and ALT suffixes; they identify different edits.
    return re.sub(r'(?:[_ -]xml)$', '', Path(path).stem, flags=re.I).casefold()


def offline_roots(xml_path, project_root=''):
    roots = []
    for parent in Path(xml_path).parents:
        if parent.name.casefold() == 'xml':
            roots.append(parent.parent / 'Offline')
            break
    if project_root:
        project = Path(project_root)
        roots.extend(parent / 'Exports' / 'Offline' for parent in (project, *project.parents))
    return list(dict.fromkeys(str(p) for p in roots if p.is_dir()))


def find_references(xml_path, roots, cancel=lambda: False):
    """Exact edit identity, preferring the XML's date when available."""
    target = reference_stem(xml_path)
    date = next((p.name for p in Path(xml_path).parents
                 if re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name)), None)
    def scan(folders):
        found = set()
        for folder in folders:
            for current, dirs, files in os.walk(folder, followlinks=False):
                if cancel():
                    raise Cancelled()
                dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
                for name in files:
                    path = Path(current) / name
                    if path.suffix.lower() in VIDEO_EXTENSIONS and reference_stem(path) == target:
                        found.add(str(path))
        return sorted(found)
    if date:
        same_date = scan([str(Path(root) / date) for root in roots])
        if same_date:
            return same_date
    return scan(roots)
