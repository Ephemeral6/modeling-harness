import pytest

pytestmark = pytest.mark.regression


def test_deferred_accounting(regression_project, regression_api):
    defect = "deferred Opportunity 未强制注入成稿局限性小节"
    audit = regression_api(
        "modelharness.opportunities", "audit_discharge", defect
    )
    render = regression_api("modelharness.sanitize", "render_final", defect)
    root = regression_project("deferred_accounting")
    assert any("opp-boundary" in error for error in audit(root)), defect
    text = render(root).read_text(encoding="utf-8")
    assert "opp-boundary" in text and "预算" in text, defect
    assert not audit(root), defect
