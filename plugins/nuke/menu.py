"""GUI menu only; generation adapters remain lazy."""
import nuke

if nuke.GUI:
    menu = nuke.menu("Nodes")
    if menu.findItem("ShotBox/ShotBoxMatte") is None:
        menu.addCommand("ShotBox/ShotBoxMatte", "import shotbox_nuke; shotbox_nuke.create()")
