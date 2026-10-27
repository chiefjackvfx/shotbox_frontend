"""Nuke Qt bootstrap for Shotbox's isolated denoise job (not a plugin)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import traceback


NEAT_CLASS = "OFXcom.absoft.NeatVideo6_v6"


def find_nodes(nuke):
    # Nuke converts spaces to underscores when assigning names from Python.
    matches = [node for node in nuke.allNodes() if node.name() in ("Reduce Noise v6_1", "Reduce_Noise_v6_1")]
    if len(matches) != 1 or matches[0].Class() != NEAT_CLASS:
        raise RuntimeError("Expected one Neat Video v6 node named Reduce Noise v6_1.")
    noise = matches[0]
    write = nuke.toNode("WriteDN1")
    if write is None or write.Class() != "Write":
        raise RuntimeError("WriteDN1 is missing or is not a Write node.")
    seen, pending = set(), [write]
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        pending.extend(node.dependencies(nuke.INPUTS))
    if noise not in seen:
        raise RuntimeError("WriteDN1 is not connected downstream of Reduce Noise v6_1.")
    if any(node["disable"].value() for node in (noise, write)):
        raise RuntimeError("The noise node and WriteDN1 must both be enabled.")
    if not write["file"].value():
        raise RuntimeError("WriteDN1 has no output path.")
    if not noise.knob("Prepare_Profile..."):
        raise RuntimeError("Neat Video's Prepare Noise Profile control is unavailable.")
    return noise, write


def start(request_path):
    import nuke
    from PySide6 import QtCore

    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    directory = Path(request_path).parent
    script = Path(request["script"])
    qt_application = None
    if request.get("qt_terminal"):
        from PySide6 import QtWidgets
        qt_application = QtWidgets.QApplication.instance()

    def event(stage, **values):
        with (directory / "host-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"stage": stage, **values}) + "\n")

    def unchanged():
        if hashlib.sha256(script.read_bytes()).hexdigest() != request["sha256"]:
            raise RuntimeError("The .nk changed while denoise was running. The profile was not saved.")
        if (directory / "cancel").exists():
            raise RuntimeError("Denoise cancelled before saving.")

    def run():
        callback = None
        autosave = None
        autosave_value = None
        try:
            if not str(nuke.NUKE_VERSION_STRING).startswith("17.") or (not nuke.GUI and not request.get("qt_terminal")):
                raise RuntimeError(f"Hidden denoise requires Nuke 17 with Qt; found {nuke.NUKE_VERSION_STRING}.")
            if request.get("qt_terminal") and not isinstance(qt_application, QtWidgets.QApplication):
                raise RuntimeError("Qt terminal denoise requires a QApplication.")
            if not nuke.env.get("nukex"):
                raise RuntimeError("Hidden denoise requires NukeX mode to load and preserve the full comp. Check the NukeX license.")
            unchanged()
            os.chdir(script.parent)
            # Redirect before scriptOpen: an artist's newer autosave would
            # otherwise trigger a recovery dialog on the hidden display.
            preferences = nuke.toNode("preferences")
            autosave = preferences.knob("AutoSaveName") if preferences is not None else None
            if autosave is not None:
                autosave_value = autosave.toScript()
                autosave.setValue(str(directory / "script.autosave"))
            # User startup menus may modify the initial Viewer. Clear that empty
            # session so scriptOpen replaces it instead of launching another Nuke.
            nuke.scriptClear(ignoreUnsavedChanges=True)
            event("loading", script=str(script))
            nuke.scriptOpen(str(script))
            noise, write = find_nodes(nuke)
            root = nuke.root()
            first, last = int(root["first_frame"].value()), int(root["last_frame"].value())
            if last < first:
                raise RuntimeError("The script frame range is invalid.")
            # nuke.frame() is context-dependent inside callbacks and terminal
            # scripts. The Root knob holds the frame actually saved in the .nk.
            saved_frame = int(root["frame"].value())
            saved_proxy = root["proxy"].value()
            root["proxy"].setValue(False)
            root.setFrame((first + last) // 2)
            event("preparing", first=first, last=last)
            # Neat Video requires an evaluated full-resolution input frame.
            # A full-image sample evaluates it without writing any render output.
            noise.sample("red", noise.width() / 2, noise.height() / 2, noise.width(), noise.height())
            event("profiling", first=first, last=last, frame=(first + last) // 2,
                  width=noise.width(), height=noise.height())
            noise["Prepare_Profile..."].execute()
            receipt = directory / "profile-confirmed"
            # The modal editor can return before the supervisor records its
            # successful Apply message. Allow that receipt to arrive, bounded.
            deadline = time.monotonic() + 1
            while not receipt.is_file() and time.monotonic() < deadline:
                if (directory / "cancel").exists():
                    break
                time.sleep(.01)
            if not receipt.is_file():
                raise RuntimeError("Neat Video closed without a verified fresh Auto Profile result.")
            unchanged()
            root.setFrame(saved_frame)
            root["proxy"].setValue(saved_proxy)
            event("saving")
            shutil.copy2(script, directory / "original.nk")
            # Keep the original script name/directory so relative paths retain meaning.
            nuke.scriptSaveAs(str(script), overwrite=True)
            event("saved", saved_frame=saved_frame)
            root["proxy"].setValue(False)
            if write.knob("use_limit"):
                write["use_limit"].setValue(False)
            total = last - first + 1
            completed = 0

            def after_frame():
                nonlocal completed
                if nuke.thisNode() is not write:
                    return
                output = Path(nuke.filename(write, nuke.REPLACE))
                if not output.is_absolute():
                    project = root["project_directory"].evaluate() if root.knob("project_directory") else ""
                    output = Path(project or script.parent) / output
                if not output.is_file() or output.stat().st_size == 0:
                    raise RuntimeError(f"Rendered output is missing or empty: {output}")
                completed += 1
                event("rendering", completed=completed, total=total, frame=nuke.frame(), output=str(output))

            callback = after_frame
            nuke.addAfterFrameRender(callback, nodeClass="Write")
            event("rendering", completed=0, total=total)
            nuke.execute(write, first, last, 1)
            if completed != total:
                raise RuntimeError(f"Expected {total} rendered frames, received {completed}.")
            event("complete", completed=completed, total=total)
        except Exception as exc:
            event("failed", error=str(exc), traceback=traceback.format_exc())
        finally:
            if callback:
                nuke.removeAfterFrameRender(callback, nodeClass="Write")
            if autosave is not None:
                autosave.fromScript(autosave_value)
            nuke.scriptClear(ignoreUnsavedChanges=True)
            if request.get("qt_terminal"):
                if qt_application is not None:
                    qt_application.quit()
            else:
                nuke.scriptExit()

    QtCore.QTimer.singleShot(0 if request.get("qt_terminal") else 1500, run)
