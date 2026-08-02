import json
import pytest

pytestmark = pytest.mark.regression


def test_problem_bound(regression_project, regression_api):
    defect = "题面硬下界 lactation_days=35 被误报为可扩搜索域"
    detect = regression_api(
        "modelharness.opportunities", "detect_boundary_optima", defect
    )
    root = regression_project("boundary_problem_bound")
    path = root / "results/research_diagnostics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert not any(
        x.get("dimension") == "lactation_days" for x in detect(data)
    ), defect
