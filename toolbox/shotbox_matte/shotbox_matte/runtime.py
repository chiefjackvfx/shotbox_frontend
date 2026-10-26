"""Explicit host configuration without process-wide environment changes."""
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class RuntimeConfig:
    application_root: Path
    worker_python: Path
    model_root: str
    state_directory: Path
    temporary_directory: Path
    model_config: str = ""
