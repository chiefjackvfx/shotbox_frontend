"""Main-thread orchestration; pure ownership lives in state.py."""
from pathlib import Path
import os
import time

from . import cache, freshness, preferences, storage
from .protocol import FrameMap, generation_size
from .state import SessionState

SESSION = SessionState()
RUNS = {}


def capture_cancelled(error):
    """Match Nuke's real render cancellation without classifying other errors."""
    import sys
    error_type = getattr(sys.modules.get("nuke"), "CancelledError", None)
    return isinstance(error_type, type) and isinstance(error, error_type)


def settings(node):
    return {"start": int(node["sb_start"].value()), "end": int(node["sb_end"].value()),
            "source_color": node["sb_color"].value(), "generation_height": int(node["sb_height"].value()),
            "generation_width": int(node["sb_width"].value()), "seed": int(node["sb_seed"].value()),
            "offload": node["sb_offload"].value(), "precision": node["sb_precision"].value(),
            "low_vram": bool(node["sb_low_vram"].value()), "temporal_mode": node["sb_temporal"].value()}


def generate(node, *, prompt_name=True):
    import nuke
    from . import capture
    from .lifecycle import reconcile
    from .node import set_status, upgrade
    upgrade(node)
    reconcile()
    if SESSION.current is not None:
        raise RuntimeError("A ShotBox attempt is already running. Cancel or wait for it to finish.")
    if nuke.NUKE_VERSION_STRING != "17.1v2" or nuke.env.get("nc") or nuke.env.get("indie"):
        raise RuntimeError("This integration requires commercial Nuke/NukeX 17.1v2.")
    if prompt_name and nuke.GUI:
        from .naming import prompt
        if not prompt(node):
            return
    mapping = FrameMap(int(node["sb_start"].value()), int(node["sb_end"].value()))
    if node["sb_color"].value() == "Unset":
        raise ValueError("Select Input colour encoding to describe your connected RGB.")
    project_precomp = node["sb_storage"].value() == "Project precomp"
    root_value = node["sb_cache_root"].value().strip()
    if not project_precomp and (not root_value or not Path(root_value).is_absolute()):
        raise ValueError("Choose an absolute persistent Cache root before Generate.")
    geometry = capture.input_geometry(node, mapping.start, mapping.end)
    width, height = generation_size(geometry["width"], geometry["height"], int(node["sb_height"].value()))
    node["sb_width"].setValue(width)
    node["sb_height"].setValue(height)
    node["sb_capture_area"].setValue("%d × %d; PAR 1; full resolution" % (geometry["width"], geometry["height"]))
    fingerprint = freshness.fingerprint(node)
    if fingerprint["unresolved"]:
        raise RuntimeError("Cannot establish input freshness: " + "; ".join(fingerprint["unresolved"]))
    prefs = preferences.load()
    application = Path(prefs["application_root"] or preferences.application_root()).expanduser().absolute()
    if not (application / "shotbox_matte" / "nuke_bridge.py").is_file():
        raise ValueError("Set the ShotBox application directory in Preferences.")
    python = preferences.resolve_interpreter(application, prefs["interpreter"] or None)
    attempt = SESSION.begin(node["sb_uuid"].value(), fingerprint=fingerprint["digest"])
    run = None
    output_directory = None
    lease = None
    try:
        from .cache_lease import acquire
        lease = acquire()
        if project_precomp:
            from .precomp_paths import allocate
            target = allocate(nuke.root().name(), node["sb_precomp_name"].value(),
                              template=node["sb_precomp_template"].value().strip() or None)
            output_directory = target["directory"]
            pattern = Path(target["pattern"].replace("####", "%04d")).relative_to(output_directory).as_posix()
        else:
            from .precomp_paths import normalize_name
            output_directory = Path(root_value) / attempt.owner.node_id / attempt.owner.attempt_id
            output_directory.mkdir(parents=True, exist_ok=False)
            pattern = normalize_name(node["sb_precomp_name"].value() or "precomp") + ".%04d.exr"
        work_root = Path(root_value or prefs.get("cache_root") or preferences.default_cache_root()).expanduser().absolute()
        directory = cache.allocate_attempt(work_root / ".jobs", attempt.owner.node_id, attempt.owner.attempt_id)
        snapshot = {**geometry, "start": mapping.start, "end": mapping.end, "count": mapping.count,
                    "fps": float(nuke.root()["fps"].value()), "view": nuke.views()[0], "files": [],
                    "mode": "alpha"}
        run = Run(node, attempt.owner, directory, application, python, snapshot, settings(node), fingerprint, prefs)
        run.data["precomp_request"] = {"pattern": pattern, "output_directory": str(output_directory.resolve()), "location": node["sb_storage"].value(),
                                       "script": nuke.root().name(), "name": node["sb_precomp_name"].value()}
        run.write()
        run._cache_lease = lease
        RUNS[attempt.owner.node_id] = run
        node["sb_pending_manifest"].setValue(str(run.path))
        node["sb_precomp_path"].setValue(str(output_directory / pattern))
        set_status(node, "Probing ShotBox environment · " + attempt.owner.attempt_id[:8])
        from .ui import refresh_all
        refresh_all()
        run.probe()
    except Exception as exc:
        if run is not None:
            run.finish("cancelled" if capture_cancelled(exc) else "failed", str(exc))
        else:
            from .cache_lease import release
            release(lease)
            if output_directory is not None:
                try:
                    output_directory.rmdir()
                except OSError:
                    pass
            SESSION.finish(attempt.owner, "failed", error=str(exc))
        RUNS.pop(attempt.owner.node_id, None)
        raise


class Run:
    def __init__(self, node, owner, directory, application, python, snapshot, options, fingerprint, prefs):
        from .capture import host_identity
        self.node, self.owner, self.directory = node, owner, directory
        self.application, self.python = application, python
        self.path = directory / "integration.json"
        self.worker = directory / "worker.json"
        self.child = None
        self.started = time.monotonic()
        self.cancel_time = None
        self.prefs = prefs
        self._cache_lease = None
        self.data = {"schema_version": 1, "integration_version": "0.1.0", "node_id": owner.node_id,
                     "attempt_id": owner.attempt_id, "created_at": cache.utc_now(), "state": "probing",
                     "original_attempt_root": str(directory.resolve()), "validation": {},
                     "work_owner_pid": os.getpid(),
                     "worker_path": "worker.json", "worker_sha256": "0" * 64,
                     "capture": snapshot, "frame_map": FrameMap(snapshot["start"], snapshot["end"]).as_dict(),
                     "settings": options, "fingerprint": fingerprint, "host": host_identity(),
                     "source_alpha_policy": "ignored; no unpremultiplication", "outputs": []}
        self.write()

    def write(self):
        self.data["updated_at"] = cache.utc_now()
        cache.atomic_write_json(self.path, self.data)

    def status(self, message):
        from .node import set_status
        if SESSION.owns(self.owner):
            set_status(self.node, "%s · %s · %.0fs" % (message, self.owner.attempt_id[:8], time.monotonic() - self.started))

    def transition(self, stage):
        SESSION.transition(self.owner, stage)
        self.data["state"] = stage
        self.write()
        self.status(stage.capitalize())

    def launch(self, arguments, terminal, continuation):
        from .process import ChildProcess

        def done(event, error, code):
            if not SESSION.owns(self.owner):
                return
            self.child = None
            if self.data["state"] == "generating":
                self.data["worker_exit"] = code
            if error:
                self.finish("cancelled" if self.cancel_time is not None or code == 130 else "failed", error)
                return
            try:
                continuation(event, code)
            except Exception as exc:
                self.finish("cancelled" if capture_cancelled(exc) or self.cancel_time is not None else "failed",
                            (str(exc) or "Cancelled during RGB capture.") if capture_cancelled(exc) else str(exc))

        self.child = ChildProcess(self.python, self.application, self.directory, arguments,
                                  terminal, self.progress, done)
        self.child.start()

    def progress(self, event):
        if event.get("type") == "progress":
            self.status("%s %s/%s: %s" % (event.get("stage"), event.get("current", 0),
                                         event.get("total", 0), event.get("message", "")))

    def bridge(self, operation, flag, path, terminal, continuation):
        self.launch(["-m", "shotbox_matte.nuke_bridge", operation, flag, str(path)], terminal, continuation)

    def probe(self):
        request = {"schema_version": 1, "model_root": self.prefs["model_root"] or str(self.application / "models"),
                   "cache_root": str(self.directory),
                   "capture": {key: self.data["capture"][key] for key in ("width", "height", "count")}}
        request["capture"]["precision"] = self.data["settings"]["precision"]
        if self.prefs["model_config"]:
            request["model_config"] = self.prefs["model_config"]
        cache.atomic_write_json(self.directory / "runtime.json", request)
        self.bridge("probe", "--request", self.directory / "runtime.json", "probe", self.probed)

    def probed(self, event, code):
        from .capture import capture
        if self.cancel_time is not None:
            self.finish("cancelled", "Cancelled before capture.")
            return
        self.data["probe"] = event["report"]
        options, snap = self.data["settings"], self.data["capture"]
        job = {key: options[key] for key in ("source_color", "generation_width", "generation_height", "seed",
                                            "offload", "low_vram", "precision", "temporal_mode")}
        job.update(input_path=str(self.directory / "capture_v001"), sequence=None, fps=snap["fps"],
                   frame_skip=0, frame_cap=0, source_start=1, source_end=snap["count"], suffix="_matte",
                   first_frame=1, padding=4, existing="version-up", output_channels="alpha", models=event["report"]["models"])
        cache.atomic_write_json(self.worker, job)
        self.data["worker_sha256"] = cache.sha256_file(self.worker)
        self.ensure_fresh()
        self.transition("capturing")
        capture(self.node, self.directory, snap)
        self.ensure_fresh()
        self.transition("preflight")
        self.bridge("preflight", "--job", self.worker, "plan", self.planned)

    def ensure_fresh(self):
        if not SESSION.owns(self.owner) or self.node["sb_uuid"].value() != self.owner.node_id:
            raise RuntimeError("Attempt owner changed; the result cannot activate on this node.")
        fingerprint = freshness.fingerprint(self.node)
        if fingerprint["unresolved"] or fingerprint["digest"] != self.data["fingerprint"]["digest"]:
            raise RuntimeError("Input/settings changed or freshness is unverifiable. Generate again from the intended input.")

    def planned(self, event, code):
        if self.cancel_time is not None:
            self.finish("cancelled", "Cancelled before inference.")
            return
        self.data["capture"].update(event["capture"])
        self.data["plan"] = event["plan"]
        spatial = event["plan"]["spatial"]
        self.node["sb_plan"].setValue("Fitted %d × %d; padded %d × %d; %d output frames" %
                                      (spatial["resized_width"], spatial["resized_height"],
                                       spatial["width"], spatial["height"], self.data["capture"]["count"]))
        self.transition("generating")
        self.launch(["-m", "shotbox_matte", "--worker", str(self.worker)], "result", self.generated)

    def generated(self, event, code):
        self.data["result"], self.data["worker_exit"] = event["result"], code
        manifest = Path(event["result"]["manifest"]).resolve()
        self.data["backend_manifest"] = manifest.relative_to(self.directory.resolve()).as_posix()
        self.transition("validating")
        self.bridge("validate", "--attempt", self.path, "validation", self.validated)

    def validated(self, event, code):
        from .node import activate
        report = event["report"]
        self.data["validation"] = report
        self.data["outputs"] = report["outputs"]
        if self.data.get("precomp_request"):
            self.status("Publishing generated alpha")
            self.data["precomp"] = storage.promote_alpha(self.directory, self.data)
            storage.commit(self.directory, self.data)
        self.data["state"] = "complete"
        self.write()
        try:
            self.ensure_fresh()
        except RuntimeError:
            self.finish("complete", "Completed cache retained; input changed. Reload Result after reviewing freshness.")
            return
        if SESSION.may_activate(self.owner, self.data["fingerprint"]["digest"]):
            try:
                activate(self.node, self.path)
                if self.data.get("precomp"):
                    from .readback import install
                    install(self.node, self.path)
                SESSION.activate(self.owner, str(self.path), self.data["fingerprint"]["digest"])
            except Exception as exc:
                self.finish("complete", "Completed cache retained; activation failed: %s. Use Reload Result." % exc)
                return
        message = "Precomped; main-graph Read ready" if self.data.get("precomp") else "Completed; valid active cache"
        if self.cancel_time is not None:
            message += "; completion preceded cancellation"
        self.finish("complete", message)

    def finish(self, outcome, message=""):
        if not SESSION.owns(self.owner):
            return
        if self.data.get("storage"):
            outcome = "complete"  # Published files remain a valid result after UI errors.
        self.data["state"], self.data["message"] = outcome, message
        display = message or outcome.capitalize()
        try:
            try:
                self.write()
            except (OSError, ValueError) as exc:
                display += "; could not save attempt status: " + str(exc)
            try:
                if self.data.get("precomp_request", {}).get("output_directory"):
                    storage.cleanup(self.directory, self.data)
            except (OSError, ValueError, RuntimeError) as exc:
                display += "; temporary-file cleanup incomplete: " + str(exc)
            try:
                if outcome != "complete" and self.node["sb_active_manifest"].value():
                    display += "; previous active cache retained (Validate Cache to check freshness)"
                self.status(display)
                self.node["sb_force"].setEnabled(False)
            except (ValueError, RuntimeError):
                pass  # Node deletion must never strand process ownership.
        finally:
            SESSION.finish(self.owner, outcome, error=display if outcome != "complete" else "", result=str(self.path))
            RUNS.pop(self.owner.node_id, None)
            from .cache_lease import release
            release(getattr(self, "_cache_lease", None))
            self._cache_lease = None
            from .ui import refresh_all
            refresh_all()

    def cancel(self):
        from .qt_compat import qt_core
        self.cancel_time = time.monotonic()
        SESSION.request_cancel(self.owner)
        if self.data["state"] == "generating":
            Path(str(self.worker) + ".cancel").touch()
        elif self.child is not None:
            self.child.force_stop()
        self.status("Cancellation requested; foreground capture uses Nuke's render Cancel")
        qt_core().QTimer.singleShot(30000, self.offer_force)

    def offer_force(self):
        if SESSION.owns(self.owner) and self.child is not None:
            self.node["sb_force"].setEnabled(True)
            self.status("Worker has not exited; Force stop worker is available")


def cancel(node, force=False):
    run = RUNS.get(node["sb_uuid"].value())
    if run is not None:
        if force and run.child is not None and run.cancel_time is not None and time.monotonic() - run.cancel_time >= 30:
            run.child.force_stop()
        elif not force:
            run.cancel()


def shutdown(node_id=None):
    global SESSION
    runs = list(RUNS.values()) if node_id is None else [RUNS[node_id]] if node_id in RUNS else []
    # Invalidate first: draining/reaping children cannot activate a late result.
    for run in runs:
        SESSION.invalidate_node(run.owner.node_id)
        try:
            Path(str(run.worker) + ".cancel").touch()
            if run.child is not None:
                run.child.reap()
            run.data.update(state="complete" if run.data.get("storage") else "cancelled",
                            message="Owner closed or deleted; attempt interrupted.")
            run.write()
            if run.data.get("precomp_request", {}).get("output_directory"):
                storage.cleanup(run.directory, run.data)
        finally:
            from .cache_lease import release
            release(getattr(run, "_cache_lease", None))
            RUNS.pop(run.owner.node_id, None)
    if node_id is None:
        SESSION.close()
        SESSION = SessionState()
