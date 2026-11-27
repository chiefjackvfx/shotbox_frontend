"""Terminal preference for the user's Linux Shotbox application-menu shortcut."""
from configparser import ConfigParser, Error
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

RUN_SCRIPT = Path(__file__).resolve().with_name("run_shotbox_frontend.sh")


def terminal_setting(path):
    """Return the shortcut's terminal flag only if it launches this installation."""
    entry = ConfigParser(interpolation=None, delimiters=("=",), strict=False)
    try:
        entry.read(path, encoding="utf-8")
        desktop = entry["Desktop Entry"]
        command = shlex.split(desktop["Exec"])
        if command and Path(command[0]) == RUN_SCRIPT:
            return desktop.getboolean("Terminal", fallback=False)
    except (Error, KeyError, OSError, ValueError):
        pass
    return None


def find_launcher():
    if not sys.platform.startswith("linux") or not shutil.which("desktop-file-edit"):
        return None
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    entries = data_home / "applications"
    launched = os.environ.get("GIO_LAUNCHED_DESKTOP_FILE")
    if launched:
        path = Path(launched)
        if path.parent == entries and terminal_setting(path) is not None:
            return path
    return next((path for path in sorted(entries.glob("*.desktop"))
                 if terminal_setting(path) is not None), None)


def set_terminal(path, enabled):
    if terminal_setting(path) is None:
        raise ValueError("The Shotbox shortcut has moved or changed.")
    result = subprocess.run(
        ["desktop-file-edit", "--set-key=Terminal",
         "--set-value=" + ("true" if enabled else "false"), str(path)],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise OSError(result.stderr.strip() or "Could not update the Shotbox shortcut.")


def launch_from_dedicated_terminal():
    shortcut = find_launcher()
    command = [sys.executable, "main.py"]
    if shortcut is not None and terminal_setting(shortcut) is False:
        subprocess.Popen(command, cwd=RUN_SCRIPT.parent, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    else:
        os.execv(sys.executable, command)


if __name__ == "__main__":
    launch_from_dedicated_terminal()
