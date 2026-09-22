# 3DE4.script.name:    Version Up Safe v1.2
# 3DE4.script.version: v1.2
# 3DE4.script.gui:     Main Window::ShotBox
# 3DE4.script.comment: Save current project, copy it to the next available version, and open it.

import os
import re
import shutil
import tde4


VERSION_PATTERN = re.compile(r'^(.*?)(v)(\d+)(.*?)$', re.IGNORECASE)


def parse_version(name):
    """Extract (prefix, v_char, version_str, suffix) from a filename stem, or None."""
    m = VERSION_PATTERN.match(name)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4)


def highest_version_on_disk(dir_path, prefix, v_char, suffix, ext):
    """
    Scan dir_path for files matching the same naming structure and return
    the highest version number found as an int. Falls back to current_ver
    if the scan fails or finds nothing higher.
    """
    # Match any digit count so we don't miss e.g. v9 -> v10 boundary
    sibling_re = re.compile(
        r'^{}{}(\d+){}{}$'.format(
            re.escape(prefix), re.escape(v_char),
            re.escape(suffix), re.escape(ext)
        ),
        re.IGNORECASE
    )
    highest = None
    try:
        for entry in os.scandir(dir_path):
            m = sibling_re.match(entry.name)
            if m:
                n = int(m.group(1))
                if highest is None or n > highest:
                    highest = n
    except OSError:
        pass
    return highest


def version_up_project():
    current_path = tde4.getProjectPath()

    if not current_path:
        tde4.postQuestionRequester("Version Up Safe v1.2", "No project is currently open.", "OK")
        return

    # Save the currently open project before making the versioned copy.
    # In this 3DE4 API, saveProject requires the destination path.
    # Saving back to current_path preserves the current project before copying it.
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
            "The current project could not be saved.\n\nVersion Up was cancelled so no work is lost.",
            "OK"
        )
        return

    dir_path = os.path.dirname(current_path)
    filename = os.path.basename(current_path)
    name, ext = os.path.splitext(filename)

    parsed = parse_version(name)
    if parsed is None:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "Could not find a version number in:\n{}\n\nExpected a pattern like _v001 or v01.".format(filename),
            "OK"
        )
        return

    prefix, v_char, version_str, suffix = parsed
    padding = len(version_str)

    # Use the highest version on disk rather than just current + 1,
    # so we never clobber a file saved by someone else in the meantime.
    highest = highest_version_on_disk(dir_path, prefix, v_char, suffix, ext)
    if highest is None:
        highest = int(version_str)

    new_version_str = str(highest + 1).zfill(padding)
    new_name = "{}{}{}{}{}".format(prefix, v_char, new_version_str, suffix, ext)
    new_path = os.path.join(dir_path, new_name)

    # Shouldn't happen given the scan above, but guard anyway
    if os.path.exists(new_path):
        confirm = tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "{} already exists.\nOverwrite?".format(new_name),
            "Yes", "No"
        )
        if confirm != 1:
            return

    try:
        shutil.copy2(current_path, new_path)
    except OSError as exc:
        tde4.postQuestionRequester(
            "Version Up Safe v1.2",
            "The current project was saved, but the new version could not be created:\n{}".format(exc),
            "OK"
        )
        return

    if tde4.loadProject(new_path) != 1:
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
