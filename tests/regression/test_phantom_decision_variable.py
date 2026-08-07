import json

import pytest

pytestmark = pytest.mark.regression


def test_phantom_decision_variable(regression_project, regression_api):
    defect = (
        "成稿宣称联合优化配种批次与栏位分配，但清单中栏位分配 role=parameter，"
        "且头条数字 Q2.annual_output 缺少复现脚本"
    )
    audit_manifest = regression_api(
        "modelharness.optimization", "audit_decision_manifest", defect
    )
    audit_content = regression_api(
        "modelharness.paper_content", "audit_paper_content", defect
    )
    root = regression_project("phantom_decision_variable")

    assert audit_manifest(root) == [], defect
    errors = audit_content(root)
    assert any(
        "phantom_decision_variable" in error
        and "联合优化" in error
        and "x.pen_assignment" in error
        for error in errors
    ), defect
    assert any(
        "undeclared_decision_claim" in error and "同时决策" in error
        for error in errors
    ), defect
    assert any(
        "broken_reproduction_chain" in error and "Q2.annual_output" in error
        for error in errors
    ), defect
    assert not any("Q2.ewes" in error for error in errors), defect

    manifest_path = root / "results/decision_variable_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["variables"][0]["source"]["sha256"] = "0" * 64
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert any(
        "x.mating_batch" in error for error in audit_manifest(root)
    ), defect
