"""Launch isolated GUI acceptance through a temporary NUKE_PATH/menu.py.

Run using ordinary Python, e.g.:
  python launch_gui_test.py --native --report /project/docs/nuke-gui-native.json
  python launch_gui_test.py --source /plates/shot --cache /project/cache --height 384
The controlled matrix takes at least 30 seconds because it verifies force stop.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nuke", default="/opt/Nuke17.1v2/Nuke17.1")
    parser.add_argument("--native", action="store_true")
    parser.add_argument("--precomp", action="store_true", help="Exercise real GPU project precomp, automatic Read and version regeneration")
    parser.add_argument("--readback", action="store_true", help="Exercise real naming dialog and external Read installation")
    parser.add_argument("--clean-precomp", action="store_true", help="Exercise EXR-only delivery, private jobs and compact receipts with CPU fixtures")
    parser.add_argument("--alpha-only", action="store_true", help="Exercise single-capture alpha-only EXR promotion with CPU replay")
    parser.add_argument("--replay-manifest", type=Path, help="--readback only: explicit CPU replay of a previously verified native matte cache")
    parser.add_argument("--frames", type=int, default=3, help="Frame count for --precomp (default: 3)")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--height", type=int, default=384)
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if args.replay_manifest and not (args.readback or args.clean_precomp or args.alpha_only):
        parser.error("--replay-manifest requires --readback, --clean-precomp or --alpha-only")
    if (args.clean_precomp or args.alpha_only) and args.replay_manifest is None:
        parser.error("--clean-precomp/--alpha-only require an explicit --replay-manifest; they never start new GPU inference")
    if (args.precomp or args.readback or args.clean_precomp or args.alpha_only) and (args.source is None or args.cache is None):
        parser.error("--precomp/--readback require --source and an explicit persistent --cache test directory")
    if args.frames < 1:
        parser.error("--frames must be positive")
    here = Path(__file__).resolve().parent
    application = here.parents[2] / "toolbox" / "shotbox_matte"
    native = args.native or args.source is not None
    default_name = "nuke-clean-precomp-gui.json" if args.clean_precomp else ("nuke-readback-gui.json" if args.readback else ("nuke-precomp-gui.json" if args.precomp else ("nuke-bam030-gui.json" if args.source else ("nuke-gui-native.json" if native else "nuke-gui-workflow.json"))))
    if args.alpha_only:
        default_name = "nuke-alpha-gui.json"
    report_path = (args.report or application / "docs" / default_name).resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    previous_failure = None
    if report_path.exists():
        previous = json.loads(report_path.read_text())
        if previous.get("status") == "failed":
            # Keep diagnostic evidence when a corrected test repeats a run.
            with tempfile.NamedTemporaryFile(prefix="shotbox-gui-failed-", suffix=".json", mode="w",
                                             encoding="utf-8", delete=False) as stream:
                json.dump(previous, stream, indent=2)
                previous_failure = stream.name
    script = here / ("clean_precomp_workflow_gui.py" if args.clean_precomp else ("readback_workflow_gui.py" if args.readback else ("precomp_workflow_gui.py" if args.precomp else ("workflow_footage_gui.py" if args.source else "workflow_gui.py"))))
    if args.alpha_only:
        script = here / "alpha_workflow_gui.py"
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="shotbox-nuke-gui-startup-") as temporary:
        startup = Path(temporary)
        (startup / "menu.py").write_text("import runpy\nfrom PySide6 import QtCore\n"
            "QtCore.QTimer.singleShot(1500, lambda: runpy.run_path(" + repr(str(script)) + ")[\"start\"]())\n")
        environment = dict(os.environ)
        environment.update(NUKE_PATH=str(startup) + os.pathsep + str(here.parent),
                           SHOTBOX_WORKFLOW_REPORT=str(report_path),
                           SHOTBOX_WORKFLOW_TIMEOUT=str(args.timeout),
                           SHOTBOX_PRECOMP_FRAMES=str(args.frames))
        if native:
            environment["SHOTBOX_GUI_NATIVE"] = "1"
        else:
            environment.pop("SHOTBOX_GUI_NATIVE", None)
        if args.cache:
            args.cache.resolve().mkdir(parents=True, exist_ok=True)
            environment["SHOTBOX_WORKFLOW_CACHE"] = str(args.cache.resolve())
        if args.source:
            if args.cache is None:
                parser.error("--source requires an explicit persistent --cache directory")
            environment.update(SHOTBOX_GUI_SOURCE=str(args.source.resolve()), SHOTBOX_GUI_HEIGHT=str(args.height))
        if args.replay_manifest:
            environment["SHOTBOX_READBACK_REPLAY_MANIFEST"] = str(args.replay_manifest.resolve())
        report_path.write_text(json.dumps({"status": "running", "nuke": args.nuke, "script": str(script)}))
        log = startup / "nuke.log"
        with log.open("w") as stream:
            child = subprocess.Popen([args.nuke, "-q", "-m", "4"], env=environment,
                                     stdout=stream, stderr=subprocess.STDOUT)
            try:
                code = child.wait(timeout=args.timeout + 60)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                code = -1
        report = json.loads(report_path.read_text())
        if report.get("status") == "awaiting_host_exit" and code == 0:
            try:
                os.kill(report["closing_worker_pid"], 0)
            except ProcessLookupError:
                report["status"] = "passed"
                report["passed"].append("File New/normal GUI exit reaps exact resistant owned worker PID, verified by parent process")
            else:
                report.update(status="failed", error="Owned worker survived normal GUI exit")
        if code != 0 or report.get("status") not in ("passed", "failed"):
            report.update(status="failed", launcher_error="GUI exited without a final passing report", exit_code=code)
        report["launcher"] = {"executable": args.nuke, "exit_code": code,
                              "elapsed_seconds": round(time.monotonic() - started, 3),
                              "startup": "temporary NUKE_PATH/menu.py with PySide6 QTimer"}
        if previous_failure:
            report["launcher"]["previous_failed_report"] = previous_failure
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(log.read_text(errors="replace")[-10000:])
        print(report["status"] + ": " + str(report_path))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
