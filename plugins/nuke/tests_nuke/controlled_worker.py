"""Explicit CPU fixture for workflow_gui.py. Never used by the plugin itself.

Probe resolves and validates real local model headers but deliberately omits
CUDA checks. Worker uses the actual CLI/export with a deterministic CPU engine.
This is process/host integration evidence, never native inference evidence.
"""
import json
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "toolbox" / "shotbox_matte"))


def main():
    operation, path = sys.argv[1:]
    if operation == "probe":
        from shotbox_matte.models import discover_models, validate_models
        request = json.loads(Path(path).read_text())
        config = discover_models(Path(request["model_root"]))
        errors = validate_models(config)
        if errors:
            print(json.dumps({"type": "error", "message": "; ".join(errors)}), flush=True)
            return 1
        print(json.dumps({"type": "probe", "protocol_version": 1,
                          "report": {"models": config.to_dict(), "controlled_cpu_fixture": True,
                                     "cuda": "explicitly not checked in this test", "model_headers": "valid"}}), flush=True)
        return 0
    if operation != "worker":
        raise ValueError("Expected probe or worker fixture operation.")
    worker_path = Path(path)
    seed = json.loads(worker_path.read_text())["seed"]
    if seed == 45:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    import numpy as np
    import shotbox_matte.native as native
    from shotbox_matte.core import CancelledError
    from shotbox_matte.cli import main as cli_main

    class ControlledEngine:
        def __init__(self, *args, **kwargs):
            pass

        def infer(self, frames, **kwargs):
            if kwargs["seed"] == 43:
                raise RuntimeError("Controlled CPU regression failure (intentional test).")
            if kwargs["seed"] == 44:
                while not kwargs["cancelled"]():
                    time.sleep(0.05)
                raise CancelledError("Controlled CPU cancellation acknowledged.")
            if kwargs["seed"] in (45, 46):
                worker_path.with_name("controlled-worker-ready").write_text(str(kwargs["seed"]))
                if kwargs["seed"] == 45:
                    # Exercise only the integration's owned-process force-stop
                    # path. This explicitly hostile test ignores cancellation.
                    while True:
                        time.sleep(0.1)
                time.sleep(1.0)
            for index, frame in enumerate(frames):
                yield np.full_like(frame, -0.125 + index / 4)

    native.NativeAlphaEngine = ControlledEngine
    return cli_main(["--worker", path])


if __name__ == "__main__":
    raise SystemExit(main())
