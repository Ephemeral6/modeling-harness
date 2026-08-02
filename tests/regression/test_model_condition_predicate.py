import pytest

pytestmark = pytest.mark.regression


def test_model_condition(regression_project, regression_api):
    defect = "多羔死亡率对胎数非单调却通过 Q3.multi_litter_mortality"
    audit = regression_api(
        "modelharness.requirements", "audit_requirements", defect
    )
    root = regression_project("model_condition_predicate")
    assert any(
        "Q3.multi_litter_mortality" in error and "predicate" in error
        for error in audit(root)
    ), defect
