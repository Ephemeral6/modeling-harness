import json
import pytest

pytestmark = pytest.mark.regression


def test_assumption_branch(regression_project, regression_api):
    defect = "load-bearing 假设 cross_batch_pooling 未物化替代分支"
    detect = regression_api(
        "modelharness.opportunities",
        "detect_unmaterialized_branches",
        defect,
    )
    root = regression_project("assumption_branch")
    data = json.loads(
        (root / "docs/assumptions.json").read_text(encoding="utf-8")
    )
    found = detect(data)
    assert any(
        item["kind"] == "unmaterialized_assumption_branch"
        and item["severity"] == "high"
        and item.get("assumption_id") == "cross_batch_pooling"
        for item in found
    ), defect
