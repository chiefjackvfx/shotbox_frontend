"""Explicit host configuration without process-wide environment changes."""
from dataclasses import dataclass
import json
from pathlib import Path

@dataclass(frozen=True)
class RuntimeConfig:
    application_root: Path
    worker_python: Path
    model_root: str
    state_directory: Path
    temporary_directory: Path
    model_config: str = ""


def configured_model_preferences(values: dict, runtime: RuntimeConfig | None) -> dict:
    """Resolve host overrides identically for both Matte interfaces."""
    values = dict(values)
    if runtime is not None and runtime.model_root:
        if values.get("model_root") != runtime.model_root:
            values["models"] = {}
        values["model_root"] = runtime.model_root
    if runtime is not None and runtime.model_config:
        config_path = Path(runtime.model_config).expanduser()
        configured = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(configured, dict):
            raise ValueError("Model configuration must be an object.")
        configured = configured.get("models", configured)
        if not isinstance(configured, dict):
            raise ValueError("Model configuration must be an object.")
        values["models"] = {key: str((config_path.parent / value).resolve())
                            for key, value in configured.items() if isinstance(value, str) and value}
    return values
