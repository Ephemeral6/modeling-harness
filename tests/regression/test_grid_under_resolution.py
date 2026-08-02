import json
import pytest

pytestmark = pytest.mark.regression


def test_grid_resolution(regression_project, regression_api):
    defect = "哺乳期整数域 35–45 只测三点却未报网格欠分辨"
    detect = regression_api(
        "modelharness.opportunities", "detect_grid_under_resolution", defect
    )
    root = regression_project("grid_under_resolution")
    path = root / "results/research_diagnostics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    found = [x for x in detect(data) if x["kind"] == "under_resolved_grid"]
    assert any(
        x.get("dimension") == "lactation_days" for x in found
    ), defect
