"""Large production DAGs must keep the local Read placement search bounded."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'plugins/nuke'))
from shotbox_nuke import readback  # noqa: E402


class DagNode:
    def __init__(self, x, y, width=80, height=18):
        self.x, self.y, self.width, self.height = x, y, width, height

    def xpos(self):
        return self.x

    def ypos(self):
        return self.y

    def screenWidth(self):
        return self.width

    def screenHeight(self):
        return self.height

    def Class(self):
        return 'Read'


def test_dense_graph_search_has_fixed_budget_and_safe_outer_fallback(monkeypatch):
    source = DagNode(0, 0)
    result = DagNode(0, 0, width=100, height=90)
    # Distinct coordinates reproduce the worst case for candidate-edge growth.
    siblings = [source] + [DagNode(i * 13, i * 17) for i in range(1, 1201)]
    checks = []

    def occupied(rect, obstacles):
        checks.append(rect)
        assert len(checks) <= 1024, 'Read placement exceeded its local search budget'
        assert len(obstacles) == len(siblings)
        return False

    monkeypatch.setattr(readback, '_clear_position', occupied)
    x, y = readback._position(source, result, siblings)
    assert checks
    assert x >= max(node.xpos() + node.screenWidth() for node in siblings) + 16
    assert y == source.ypos()


def test_small_clear_graph_keeps_the_preferred_position():
    source = DagNode(-200, 75)
    result = DagNode(0, 0, width=100, height=90)
    assert readback._position(source, result, [source]) == (-50, 75)
