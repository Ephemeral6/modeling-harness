import json

import pytest

pytestmark = pytest.mark.regression


def test_portfolio_missing_cold_arm(regression_project, regression_api):
    defect = "声明 warm start 却无 cold_start 对照臂的搜索组合未被拦截"
    audit = regression_api(
        "modelharness.search_quality", "audit_search_portfolio", defect
    )
    root = regression_project("portfolio_missing_cold_arm")
    errors = audit(root)
    assert any("cold_start" in error for error in errors), defect


def test_adding_cold_arm_clears_the_audit(regression_project, regression_api):
    defect = "补齐 cold_start 对照臂后搜索组合审计仍误报"
    audit = regression_api(
        "modelharness.search_quality", "audit_search_portfolio", defect
    )
    root = regression_project("portfolio_missing_cold_arm")
    path = root / "results/search_portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["arms"].append({
        "id": "cold-seed0",
        "kind": "cold_start",
        "seed": 0,
        "method": "mip_highs",
        "incumbent_objective": 99.4,
        "tool_run_ids": [],
    })
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assert audit(root) == [], defect
