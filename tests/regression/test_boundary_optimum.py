import json
import pytest

pytestmark = pytest.mark.regression


def test_boundary_optimum(regression_project, regression_api):
    defect = "interval_days 在人工上界 30 最优却无 Opportunity"
    detect = regression_api(
        "modelharness.opportunities", "detect_boundary_optima", defect
    )
    root = regression_project("boundary_optimum")
    path = root / "results/research_diagnostics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    found = [
        item for item in detect(data)
        if item["kind"] == "artificial_boundary_optimum"
    ]
    assert any(
        item.get("dimension") == "interval_days" for item in found
    ), defect
