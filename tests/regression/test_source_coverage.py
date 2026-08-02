import pytest

pytestmark = pytest.mark.regression


def test_source_coverage(regression_project, regression_api):
    defect = "题面条件多羔死亡率高于正常未进入 requirement 且无独立 override"
    segment = regression_api(
        "modelharness.requirements", "segment_source", defect
    )
    audit = regression_api(
        "modelharness.requirements", "audit_segmentation", defect
    )
    root = regression_project("source_coverage")
    path = root / "problem/data_raw/001__statement.md"
    text = path.read_text(encoding="utf-8")
    first = segment(text)
    target = next(item for item in first if "高于" in item["text"])
    assert first == segment(text), "源文本分句不确定"
    assert "高于" in target["markers"], defect
    assert any(
        "多羔死亡率高于正常" in error for error in audit(root)
    ), defect
