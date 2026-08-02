import pytest

pytestmark = pytest.mark.regression


def test_claim_rounding(regression_project, regression_api):
    defect = "1319.737991 应显示 1319.74，错误显示 1319.48 未被字段级差分"
    evaluate = regression_api("modelharness.claims", "evaluate_claims", defect)
    audit = regression_api("modelharness.claims", "audit_claims", defect)
    root = regression_project("claim_drift")
    evaluate(root)
    assert any("display_drift" in error for error in audit(root)), defect
    paper = root / "paper/final.md"
    paper.write_text(
        paper.read_text(encoding="utf-8").replace("1319.48", "1319.74"),
        encoding="utf-8",
    )
    assert not any("display_drift" in error for error in audit(root)), defect
