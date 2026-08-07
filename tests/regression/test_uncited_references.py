import pytest

pytestmark = pytest.mark.regression


def test_uncited_references(regression_project, regression_api):
    defect = (
        "参考文献 [2][3] 零文内标注且正文引用了不存在的 [4]，"
        "交付审计未逐条察觉"
    )
    audit = regression_api("modelharness.sanitize", "sanitize_report", defect)
    root = regression_project("uncited_references")
    report = audit(root)
    uncited = sorted(
        item["reference"] for item in report["violations"]
        if item["kind"] == "uncited_reference"
    )
    phantom = sorted(
        item["reference"] for item in report["violations"]
        if item["kind"] == "citation_without_entry"
    )
    assert uncited == [2, 3], defect
    assert phantom == [4], defect
