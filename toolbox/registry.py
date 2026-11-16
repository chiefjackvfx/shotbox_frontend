"""Explicit, lightweight registry of bundled pop-out tools."""
from dataclasses import dataclass
from importlib import import_module

@dataclass(frozen=True)
class Tool:
    id: str
    title: str
    description: str
    entrypoint: str

    def open(self, parent):
        module, name = self.entrypoint.split(":", 1)
        return getattr(import_module(module), name)(parent)

TOOLS = (Tool("shotbox_matte", "Shotbox Matte",
              "Generate mattes from image sequences and movies, with single-shot and batch workflows.",
              "toolbox.shotbox_matte.plugin:open_tool"),
         Tool("time_block_planner", "Time Block Planner",
              "Try daily scheduling with sample tasks. Plans last while ShotBox is open.",
              "time_block_planner:open_tool"))
