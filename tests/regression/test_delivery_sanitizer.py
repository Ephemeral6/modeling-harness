import json
import pytest

pytestmark = pytest.mark.regression


def test_delivery_sanitizer(regression_project, regression_api):
    defect = "交付净化器未阻止内部证据标记、单图和全自引参考文献"
    render = regression_api("modelharness.sanitize", "render_final", defect)
    audit = regression_api("modelharness.sanitize", "sanitize_report", defect)
    root = regression_project("delivery_sanitizer")
    final = render(root)
    kinds = {item["kind"] for item in audit(root)["violations"]}
    claim_map = json.loads(
        (root / "paper/claim_map.json").read_text(encoding="utf-8")
    )
    assert "[[problem.statement]]" not in final.read_text(encoding="utf-8"), defect
    assert claim_map["claims"], defect
    assert {
        "figure_shortfall", "reference_shortfall", "self_citation_only"
    } <= kinds, defect
    assert "evidence_marker" not in kinds, defect
