import json

import pytest

pytestmark = pytest.mark.regression


def test_dominated_incumbent_blocks_delivery(regression_project, regression_api):
    defect = "greedy 基线 108.5 严格优于 incumbent 100.0 却未阻断优化交付"
    audit = regression_api(
        "modelharness.search_quality", "audit_incumbent_dominance", defect
    )
    assess = regression_api(
        "modelharness.optimization", "assess_optimization", defect
    )
    root = regression_project("dominated_incumbent")

    errors = audit(root)
    assert any(
        "greedy-2023-repeat" in error and "支配" in error for error in errors
    ), defect

    assessment = assess(root)
    assert assessment["machine_status"] == "BLOCKED", defect
    assert "search_quality" in assessment["checks"], defect
    assert any(
        blocker.startswith("search_quality:") and "支配" in blocker
        for blocker in assessment["machine_blockers"]
    ), defect


def test_non_dominating_baseline_is_clean(regression_project, regression_api):
    defect = "基线劣于 incumbent 时 incumbent 支配审计误报"
    audit = regression_api(
        "modelharness.search_quality", "audit_incumbent_dominance", defect
    )
    root = regression_project("dominated_incumbent")
    path = root / "results/incumbent_dominance_audit.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["baselines"][0]["objective"] = 99.0
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assert audit(root) == [], defect
