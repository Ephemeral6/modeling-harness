import pytest

pytestmark = pytest.mark.regression


def test_holdout_separation(regression_project, regression_api):
    defect = "同一组场景既选冠军又报告冠军值，仍被标为无偏终评"
    audit = regression_api("modelharness.claims", "audit_holdout", defect)
    errors = audit(regression_project("holdout_separation"))
    assert any("scenario_sha256" in error for error in errors), defect
    assert any(
        "report" in error and "policy_ranking" in error for error in errors
    ), defect
