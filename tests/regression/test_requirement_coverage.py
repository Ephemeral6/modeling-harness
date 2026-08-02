import pytest

pytestmark = pytest.mark.regression


def test_requirement_coverage(regression_project, regression_api):
    defect = "mandatory requirement Q1.output_range 无 claim 绑定却能宣告完整交付"
    audit = regression_api(
        "modelharness.requirements", "audit_requirements", defect
    )
    root = regression_project("requirement_coverage")
    assert any(
        "Q1.output_range" in error and "未闭合" in error
        for error in audit(root)
    ), defect
