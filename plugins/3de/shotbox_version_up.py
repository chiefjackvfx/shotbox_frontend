# 3DE4.script.name:    Version Up Safe v1.2
# 3DE4.script.version: v1.2
# 3DE4.script.gui:     Main Window::ShotBox
# 3DE4.script.comment: Save current project, copy it to the next available version, and open it.

import os
import re
import shutil
import tde4


# Only the final, underscore-delimited version belongs to the project.
# A shot identifier such as etv020 must never be treated as its version.
VERSION_PATTERN = re.compile(r'^(.*_)(v)([0-9]+)()\Z', re.IGNORECASE)


def parse_version(name):
    """Extract a trailing _vNNN token; retain the existing four-part result."""
    m = VERSION_PATTERN.match(name)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4)


def highest_version_on_disk(dir_path, prefix, v_char, suffix, ext):
    """
    Return the highest matching file version, or None when none exist.
    Let directory errors propagate so callers cannot use an incomplete scan.
    """
    # Match any digit count so we don't miss e.g. v9 -> v10 boundary
    sibling_re = re.compile(
        r'^{}{}([0-9]+){}{}\Z'.format(
            re.escape(prefix), re.escape(v_char),
            re.escape(suffix), re.escape(ext)
        ),
        re.IGNORECASE
    )
    highest = None
    with os.scandir(dir_path) as entries:
        for entry in entries:
            m = sibling_re.match(entry.name)
            if m and entry.is_file():
                n = int(m.group(1))
                if highest is None or n > highest:
                    highest = n
    return highest


def copy_project_exclusive(source, destination):
    """Copy to a new file only, removing our partial copy on I/O failure."""
    created = False
    try:
        with open(source, 'rb') as source_file:
            # Exclusive creation also protects against a collision after scanning.
            with open(destination, 'xb') as destination_file:
                created = True
                shutil.copyfileobj(source_file, destination_file)
                destination_file.flush()
                os.fsync(destination_file.fileno())
        shutil.copystat(source, destination)
    except OSError as exc:
        if created:
            try:
                os.remove(destination)
            except OSError as cleanup_exc:
                raise OSError(
                    "Copy failed: {}\nCould not remove the incomplete file:\n{}\n{}".format(
                        exc, destination, cleanup_exc
                    )
                ) from exc
        raise


def version_up_project():
    current_path = tde4.getProjectPath()

    if not current_path:
        tde4.postQuestionRequester("Version Up Safe v1.2", "No project is currently open.", "OK")
        return

    dir_path = os.path.dirname(current_path)
    filename = os.path.basename(current_path)
    name, ext = os.path.splitext(filename)

    parsed = parse_version(name)
    if parsed is None or ext.lower() != '.3de':
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "Unsupported project filename:\n{}\n\nExpected a name ending in _v001.3de.\nVersion Up was cancelled before saving.".format(filename),
            "OK"
        )
        return

    prefix, v_char, version_str, suffix = parsed
    padding = len(version_str)

    try:
        highest = highest_version_on_disk(dir_path, prefix, v_char, suffix, ext)
    except OSError as exc:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "Could not scan the project directory:\n{}\n\nVersion Up was cancelled before saving.".format(exc),
            "OK"
        )
        return
    highest = max(int(version_str), highest if highest is not None else 0)

    new_version_str = str(highest + 1).zfill(padding)
    new_name = "{}{}{}{}{}".format(prefix, v_char, new_version_str, suffix, ext)
    new_path = os.path.join(dir_path, new_name)

    if os.path.lexists(new_path):
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "{} already exists.\nVersion Up was cancelled before saving. Please try again.".format(new_name),
            "OK"
        )
        return

    # Preserve the existing workflow: save edits to the current project first.
    try:
        save_ok = tde4.saveProject(current_path)
    except Exception as exc:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "Could not save the current project:\n{}".format(exc),
            "OK"
        )
        return

    if save_ok != 1:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "The current project could not be saved.\nVersion Up was cancelled; no new version was created.",
            "OK"
        )
        return

    try:
        copy_project_exclusive(current_path, new_path)
    except FileExistsError:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "The current project was saved, but {} now exists.\nIt was not overwritten. Please try again.".format(new_name),
            "OK"
        )
        return
    except OSError as exc:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "The current project was saved, but the new version could not be created:\n{}".format(exc),
            "OK"
        )
        return

    try:
        load_ok = tde4.loadProject(new_path)
    except Exception as exc:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "Created the new version, but could not open it:\n{}\n\n{}".format(new_path, exc),
            "OK"
        )
        return

    if load_ok != 1:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "Created the new version, but could not open it:\n{}".format(new_path),
            "OK"
        )
        return

    tde4.postQuestionRequester(
        "Version Up Safe v1.2",
        "Saved current project and opened:\n{}".format(new_name),
        "OK"
    )


if __name__ == '__main__':
    version_up_project()
