from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_cache import cache_directory
import nuke_denoise as supervisor
import nuke_denoise_host as host
from nuke_denoise_display import PrivateDisplay


class Knob:
    def __init__(self, value=None, execute=None):
        self._value = value
        self.execute = execute

    def value(self):
        return self._value

    def evaluate(self):
        return self._value

    def setValue(self, value):
        self._value = value


class Node(dict):
    __hash__ = object.__hash__

    def __init__(self, name, class_name, inputs=(), **knobs):
        super().__init__({name: Knob(value) for name, value in knobs.items()})
        self._name, self._class, self._inputs = name, class_name, list(inputs)

    def name(self):
        return self._name

    def Class(self):
        return self._class

    def knob(self, key):
        return self.get(key)

    def dependencies(self, _):
        return self._inputs

    def width(self):
        return 512

    def height(self):
        return 256


class QtApplication:
    current = None

    def __init__(self):
        self.quit = mock.Mock()

    @classmethod
    def instance(cls):
        return cls.current


class DenoiseHostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=cache_directory("temp"))
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.script = self.directory / "source.nk"
        self.script.write_text("original script")
        self.request = supervisor.create_request(self.directory, self.script, "/nuke")
        self.frame = 1001
        self.noise = Node("Reduce_Noise_v6_1", host.NEAT_CLASS, disable=False)
        self.noise["Prepare_Profile..."] = Knob(execute=self.apply_profile)
        self.noise.sample = mock.Mock()
        self.write = Node("WriteDN1", "Write", inputs=[self.noise], disable=False,
                          file="renders/out.%04d.exr", use_limit=True)
        self.root = Node("root", "Root", first_frame=1001, last_frame=1004, frame=1001, proxy=True,
                         project_directory=str(self.directory))
        self.root.setFrame = self.set_frame
        self.callback = None
        self.saved_state = None
        self.qt_application = QtApplication()
        QtApplication.current = self.qt_application
        self.addCleanup(setattr, QtApplication, "current", None)
        self.nuke = SimpleNamespace(
            INPUTS=1, REPLACE=2, NUKE_VERSION_STRING="17.1v2", GUI=True, env={"nukex": True},
            allNodes=lambda: [self.noise, self.write],
            toNode=lambda name: self.write if name == "WriteDN1" else None,
            root=lambda: self.root, frame=self.set_frame, scriptOpen=mock.Mock(),
            scriptSaveAs=mock.Mock(side_effect=lambda *_, **__: self.save()), scriptClear=mock.Mock(), scriptExit=mock.Mock(),
            addAfterFrameRender=self.add_callback, removeAfterFrameRender=mock.Mock(),
            filename=self.filename, thisNode=lambda: self.write, execute=self.render,
        )
        self.addCleanup(os.chdir, Path.cwd())

    def set_frame(self, value=None):
        if value is not None:
            self.frame = value
            self.root["frame"].setValue(value)
        return self.frame

    def apply_profile(self):
        self.assertEqual(self.frame, 1002)
        self.assertFalse(self.root["proxy"].value())
        (self.directory / "profile-confirmed").touch()

    def save(self):
        self.saved_state = (self.frame, self.root["proxy"].value(), self.write["use_limit"].value())
        self.script.write_text(f"Root {{\n frame {self.frame}\n}}\n# saved profile\n")

    def add_callback(self, callback, **_):
        self.callback = callback

    def filename(self, node, replace):
        self.assertIs(node, self.write)
        self.assertEqual(replace, self.nuke.REPLACE)
        return f"renders/out.{self.frame:04d}.exr"

    def render(self, node, first, last, step):
        self.assertFalse(self.root["proxy"].value())
        self.assertFalse(node["use_limit"].value())
        self.assertEqual((first, last, step), (1001, 1004, 1))
        for frame in range(first, last + 1):
            self.frame = frame
            output = self.directory / f"renders/out.{frame:04d}.exr"
            output.parent.mkdir(exist_ok=True)
            output.write_bytes(b"rendered image")
            self.callback()

    def run_host(self):
        fake_pyside = SimpleNamespace(
            QtCore=SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda _, run: run())),
            QtWidgets=SimpleNamespace(QApplication=QtApplication),
        )
        with mock.patch.dict(sys.modules, {"nuke": self.nuke, "PySide6": fake_pyside}):
            host.start(str(self.request))
        return [json.loads(line) for line in (self.directory / "host-events.jsonl").read_text().splitlines()]

    def test_profiles_middle_frame_preserves_saved_state_and_renders_full_range(self):
        events = self.run_host()
        self.assertEqual(events[-1], {"stage": "complete", "completed": 4, "total": 4})
        self.assertEqual(self.saved_state, (1001, True, True))
        self.assertEqual(self.root["frame"].value(), 1001)
        self.assertEqual((self.directory / "original.nk").read_text(), "original script")
        self.assertIn("# saved profile", self.script.read_text())
        self.noise.sample.assert_called_once_with("red", 256, 128, 512, 256)
        self.assertEqual([event["frame"] for event in events
                          if event["stage"] == "rendering" and "frame" in event], [1001, 1002, 1003, 1004])
        self.nuke.removeAfterFrameRender.assert_called_once()

    def test_nuke_17_0_profiles_saves_and_renders_through_the_same_host(self):
        self.nuke.NUKE_VERSION_STRING = "17.0v1"
        self.nuke.GUI = False
        events = self.run_host()
        self.assertEqual(events[-1]["stage"], "complete")
        self.assertEqual(self.saved_state, (1001, True, True))
        self.nuke.scriptSaveAs.assert_called_once()

    def test_other_nuke_major_versions_fail_before_loading_or_saving(self):
        for version in ("16.0v1", "18.0v1"):
            with self.subTest(version=version):
                self.nuke.NUKE_VERSION_STRING = version
                events = self.run_host()
                self.assertEqual(events[-1]["stage"], "failed")
                self.assertIn(f"found {version}", events[-1]["error"])
                self.nuke.scriptOpen.assert_not_called()
                self.nuke.scriptSaveAs.assert_not_called()

    def test_unverified_profile_never_saves_or_renders(self):
        self.noise["Prepare_Profile..."].execute = lambda: None
        events = self.run_host()
        self.assertIn("without a verified", events[-1]["error"])
        self.nuke.scriptSaveAs.assert_not_called()
        self.assertEqual(self.script.read_text(), "original script")
        self.assertFalse((self.directory / "renders").exists())

    def test_profile_receipt_can_arrive_just_after_modal_editor_returns(self):
        self.noise["Prepare_Profile..."].execute = lambda: None
        with mock.patch.object(host.time, "sleep", side_effect=lambda _: self.apply_profile()) as sleep:
            events = self.run_host()
        sleep.assert_called_once_with(.01)
        self.assertEqual(events[-1]["stage"], "complete")
        self.nuke.scriptSaveAs.assert_called_once()

    def test_basic_nuke_mode_is_rejected_before_loading_or_saving_the_comp(self):
        self.nuke.env["nukex"] = False
        events = self.run_host()
        self.assertEqual(events[-1]["stage"], "failed")
        self.assertIn("requires NukeX mode", events[-1]["error"])
        self.nuke.scriptOpen.assert_not_called()
        self.nuke.scriptSaveAs.assert_not_called()

    def test_autosave_is_redirected_before_opening_and_restored_afterward(self):
        preferences = Node("preferences", "Preferences", AutoSaveName="artist-autosave")
        autosave = preferences["AutoSaveName"]
        autosave.toScript = lambda: "artist-autosave"
        autosave.fromScript = autosave.setValue
        original_to_node = self.nuke.toNode
        self.nuke.toNode = lambda name: preferences if name == "preferences" else original_to_node(name)
        self.nuke.scriptOpen.side_effect = lambda _: self.assertEqual(
            autosave.value(), str(self.directory / "script.autosave"))
        events = self.run_host()
        self.assertEqual(events[-1]["stage"], "complete")
        self.assertEqual(autosave.value(), "artist-autosave")

    def test_background_launch_uses_nukex_and_keeps_executable_path_intact(self):
        executable = "/opt/Nuke with spaces/Nuke17.1"
        with mock.patch.object(supervisor.os, "cpu_count", return_value=128):
            self.assertEqual(supervisor.build_nuke_command(executable), [executable, "--nukex", "-q", "-m", "16"])

    def test_thread_limit_leaves_cpu_capacity_and_handles_small_or_unknown_hosts(self):
        for cpus, expected in ((1, 1), (2, 1), (4, 2), (8, 4), (128, 16), (None, 4)):
            with self.subTest(cpus=cpus), mock.patch.object(supervisor.os, "cpu_count", return_value=cpus):
                self.assertEqual(supervisor.build_nuke_command("/nuke")[-1], str(expected))

    def test_qt_launch_keeps_interactive_nukex_license_and_topdown_scoped_to_process(self):
        bootstrap = self.directory / "qt bootstrap.py"
        with mock.patch.object(supervisor.os, "cpu_count", return_value=128):
            command = supervisor.build_launch_command("/nuke", qt_bootstrap=bootstrap, topdown=True)
        self.assertEqual(command, ["/nuke", "--nukex", "-q", "-m", "16", "--topdown", "-i", "--tg", str(bootstrap)])

    def test_qt_terminal_host_profiles_saves_and_renders_without_desktop_GUI(self):
        self.nuke.GUI = False
        events = self.run_host()
        self.assertEqual(events[-1]["stage"], "complete")
        self.qt_application.quit.assert_called_once_with()
        self.nuke.scriptExit.assert_not_called()
        self.assertEqual(self.saved_state, (1001, True, True))

    def test_qt_terminal_without_QApplication_fails_before_loading_or_saving(self):
        self.nuke.GUI = False
        QtApplication.current = None
        events = self.run_host()
        self.assertEqual(events[-1]["stage"], "failed")
        self.assertIn("requires a QApplication", events[-1]["error"])
        self.nuke.scriptOpen.assert_not_called()
        self.nuke.scriptSaveAs.assert_not_called()

    def test_legacy_desktop_requests_still_complete_and_exit(self):
        request = json.loads(self.request.read_text())
        request.pop("qt_terminal")
        request.pop("topdown")
        self.request.write_text(json.dumps(request))
        events = self.run_host()
        self.assertEqual(events[-1]["stage"], "complete")
        self.nuke.scriptExit.assert_called_once_with()
        self.qt_application.quit.assert_not_called()

    def test_apply_requires_continuously_ready_controls_including_enabled_button(self):
        import numpy as np

        display = PrivateDisplay(self.directory, lambda: False, tools={"Xvfb": "unused", "openbox": "unused"})
        window = mock.Mock()
        display.windows = lambda: [(window, "Neat Video 6.1.2 Studio")]
        display.screenshot = lambda _: np.zeros((10, 10, 3), dtype=np.uint8)
        display.clicked_auto = True
        display._click = mock.Mock()
        enabled = True
        display._match = lambda pixels, name: None if name == "apply" and not enabled else (1, 1)
        with mock.patch("nuke_denoise_display.time.monotonic", return_value=10) as clock:
            self.assertFalse(display.poll_profile())
            enabled = False
            clock.return_value = 10.4
            self.assertFalse(display.poll_profile())
            self.assertIsNone(display.ready_since)
            enabled = True
            clock.return_value = 10.5
            self.assertFalse(display.poll_profile())
            clock.return_value = 10.7
            self.assertFalse(display.poll_profile())
            clock.return_value = 10.9
            self.assertTrue(display.poll_profile())
        display._click.assert_called_once_with(window, (1, 1))
        self.assertTrue((self.directory / "profile-confirmed").is_file())

    def test_floating_viewers_in_each_nuke_edition_are_not_plugin_dialogs(self):
        for edition in ("Nuke", "NukeX", "NukeStudio"):
            with self.subTest(edition=edition):
                display = PrivateDisplay(self.directory, lambda: False, tools={"Xvfb": "unused", "openbox": "unused"})
                window = mock.Mock()
                window.get_wm_class.return_value = ("Nuke17.1", edition)
                window.get_wm_transient_for.return_value = object()
                display.windows = lambda: [(window, "(Viewer1)")]
                self.assertFalse(display.poll_profile())

    def test_apply_button_recognizes_normal_and_hover_variants_only_in_footer(self):
        import cv2
        import numpy as np
        display = PrivateDisplay(self.directory, lambda: False, tools={"Xvfb": "unused", "openbox": "unused"})
        assets = Path(host.__file__).parent / "ui" / "denoise"
        display.templates = {path.stem: cv2.imread(str(path)) for path in assets.glob("*.png")}
        for name in ("apply", "apply_hover"):
            with self.subTest(name=name):
                button = display.templates[name]
                height, width = button.shape[:2]
                pixels = np.zeros((900, 1200, 3), dtype=np.uint8)
                pixels[850:850 + height, 1000:1000 + width] = button
                self.assertEqual(display._match(pixels, "apply"), (1000 + width // 2, 850 + height // 2))
                pixels[:] = 0
                pixels[50:50 + height, 1000:1000 + width] = button
                self.assertIsNone(display._match(pixels, "apply"))

    def test_unexpected_plugin_dialog_still_stops_profiling(self):
        display = PrivateDisplay(self.directory, lambda: False, tools={"Xvfb": "unused", "openbox": "unused"})
        window = mock.Mock()
        window.get_wm_class.return_value = None
        window.get_wm_transient_for.return_value = object()
        display.windows = lambda: [(window, "Warning")]
        display.connection = mock.Mock()
        display.screenshot = mock.Mock()
        with mock.patch("cv2.imwrite"):
            with self.assertRaisesRegex(RuntimeError, "Unexpected dialog.*Warning"):
                display.poll_profile()

    def test_external_edit_is_preserved(self):
        def edit():
            self.apply_profile()
            self.script.write_text("another artist's edit")
        self.noise["Prepare_Profile..."].execute = edit
        events = self.run_host()
        self.assertIn("changed while denoise", events[-1]["error"])
        self.nuke.scriptSaveAs.assert_not_called()
        self.assertEqual(self.script.read_text(), "another artist's edit")

    def test_cancel_before_save_preserves_original(self):
        def cancel():
            self.apply_profile()
            (self.directory / "cancel").touch()
        self.noise["Prepare_Profile..."].execute = cancel
        events = self.run_host()
        self.assertIn("cancelled before saving", events[-1]["error"])
        self.nuke.scriptSaveAs.assert_not_called()

    def test_validation_rejects_missing_ambiguous_disabled_and_disconnected_nodes(self):
        for fault in ("missing", "ambiguous", "disabled", "disconnected", "wrong_class", "empty_path"):
            with self.subTest(fault=fault):
                nodes = [self.noise, self.write]
                if fault == "missing":
                    nodes.remove(self.noise)
                elif fault == "ambiguous":
                    nodes.append(Node("Reduce Noise v6_1", host.NEAT_CLASS))
                elif fault == "disabled":
                    self.write["disable"].setValue(True)
                elif fault == "disconnected":
                    self.write._inputs = []
                elif fault == "wrong_class":
                    self.noise._class = "NoOp"
                else:
                    self.write["file"].setValue("")
                self.nuke.allNodes = lambda: nodes
                with self.assertRaises(RuntimeError):
                    host.find_nodes(self.nuke)
                self.write["disable"].setValue(False)
                self.write["file"].setValue("output.%04d.exr")
                self.write._inputs = [self.noise]
                self.noise._class = host.NEAT_CLASS

    def test_original_template_name_with_spaces_is_supported(self):
        self.noise._name = "Reduce Noise v6_1"
        self.assertIs(host.find_nodes(self.nuke)[0], self.noise)

    def test_saved_frame_uses_root_knob_instead_of_callback_context(self):
        self.frame = 1  # A new GUI Viewer starts here before loading the script.
        events = self.run_host()
        self.assertEqual(events[-1]["stage"], "complete")
        self.assertEqual(self.saved_state[0], 1001)

    def test_request_captures_script_digest_and_rejects_non_nuke_files(self):
        request = json.loads(self.request.read_text())
        self.assertEqual(request["sha256"], hashlib.sha256(self.script.read_bytes()).hexdigest())
        self.assertEqual(request["script"], str(self.script.resolve()))
        self.assertTrue(request["qt_terminal"])
        self.assertTrue(request["topdown"])
        with self.assertRaises(RuntimeError):
            supervisor.create_request(self.directory, self.directory / "missing.nk", "/nuke")

    def test_events_ignore_unrelated_or_malformed_stdout(self):
        for text in ("Nuke startup", supervisor.EVENT_PREFIX + "[]", supervisor.EVENT_PREFIX + "{bad}"):
            self.assertIsNone(supervisor.parse_event(text))
        event = {"stage": "rendering", "completed": 1, "total": 3}
        self.assertEqual(supervisor.parse_event(supervisor.EVENT_PREFIX + json.dumps(event)), event)


if __name__ == "__main__":
    unittest.main()
