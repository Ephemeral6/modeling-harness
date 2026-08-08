import json

import pytest

pytestmark = pytest.mark.regression


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_comparison_missing_report(regression_project, regression_api):
    defect = "对照实验清单齐备但判卷报告缺失/不完整时仍被当成有效对照"
    audit = regression_api(
        "modelharness.comparison", "audit_comparison", defect
    )
    root = regression_project("comparison_missing_report")
    manifest = root / "manifest.json"

    errors = audit(manifest)
    assert any(
        "comparison report missing" in error and "reports/comparison.json" in error
        for error in errors
    ), defect
    # 唯一缺陷是没人判卷：状态、独立性、judge 冻结三项都不应报错。
    assert all("comparison report" in error for error in errors), defect

    # 报告存在但漏掉一条臂、且另一条臂缺 verdict —— 仍然不算判过卷。
    report = root / "reports" / "comparison.json"
    _write(report, {
        "schema": 1,
        "arms": [{"arm_id": "harness_a", "objective_value": 12.5}],
    })
    errors = audit(manifest)
    assert any(
        "comparison report incomplete" in error and "external_b" in error
        for error in errors
    ), defect
    assert any(
        "comparison report incomplete" in error
        and "harness_a" in error
        and "verdict" in error
        for error in errors
    ), defect

    # 报告覆盖全部臂且每条臂都有 objective_value 与 verdict 后，审计归零。
    _write(report, {
        "schema": 1,
        "arms": [
            {"arm_id": "harness_a", "objective_value": 12.5, "verdict": "PASS"},
            {"arm_id": "external_b", "objective_value": 11.0, "verdict": "PASS"},
        ],
    })
    assert audit(manifest) == [], defect
