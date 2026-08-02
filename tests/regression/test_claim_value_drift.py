import pytest

pytestmark = pytest.mark.regression


def test_claim_value_drift(regression_project, regression_api):
    defect = "results/q2.json=1319.737991 与 registered.json=1319.475983 字段漂移"
    evaluate = regression_api("modelharness.claims", "evaluate_claims", defect)
    audit = regression_api("modelharness.claims", "audit_claims", defect)
    root = regression_project("claim_drift")
    report = evaluate(root)
    assert report["claims"]["Q2.annual_output"]["status"] == "valid", defect
    assert any(
        "value_drift" in error and "Q2.annual_output" in error
        for error in audit(root)
    ), defect
