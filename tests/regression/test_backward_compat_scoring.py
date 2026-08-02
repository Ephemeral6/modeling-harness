from pathlib import Path

import pytest

from modelharness.problem_graph import ProblemGraph
from modelharness.scaffold import create

pytestmark = pytest.mark.regression


def test_backward_scoring(tmp_path: Path):
    root = create(tmp_path / "legacy-score", "legacy")
    first = ProblemGraph(root).frontier({}, [], "s0")[0]
    assert first["id"] == "s0.problem_definition"
    assert first["priority"] == 20.0
