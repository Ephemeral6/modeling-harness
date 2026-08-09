import json

import pytest

pytestmark = pytest.mark.regression


DEFECT = (
    "segment s0003 用一句自由文本冒充独立评审，就把命中建模标记的售价波动条件"
    "降级为 background"
)


def _ledger(root):
    path = root / "problem/source_segmentation.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _segment(ledger, segment_id="s0003"):
    return next(
        item
        for item in ledger["sources"][0]["segments"]
        if item["id"] == segment_id
    )


def test_free_text_segment_override_is_rejected(
    regression_project, regression_api
):
    audit = regression_api(
        "modelharness.requirements", "audit_segmentation", DEFECT
    )
    root = regression_project("weak_segment_override")
    errors = audit(root)
    assert any(
        "允许有波动" in error and "override_review" in error
        for error in errors
    ), DEFECT
    # The message has to name the accepted shape, or the agent's only route to
    # the field's contract is reading modelharness/requirements.py.
    hit = next(error for error in errors if "允许有波动" in error)
    assert "APPROVE" in hit and "reviewer" in hit, DEFECT


def test_segment_override_passes_once_it_points_at_a_real_review(
    regression_project, regression_api
):
    audit = regression_api(
        "modelharness.requirements", "audit_segmentation", DEFECT
    )
    root = regression_project("weak_segment_override")
    path, ledger = _ledger(root)
    # The APPROVE record already sits in the fixture; the override just never
    # pointed at it.
    _segment(ledger)["override_review"] = "reviews/s0_segment_override.json"
    _write(path, ledger)

    assert audit(root) == []


def test_segment_override_accepts_inline_signed_approval(
    regression_project, regression_api
):
    audit = regression_api(
        "modelharness.requirements", "audit_segmentation", DEFECT
    )
    root = regression_project("weak_segment_override")
    path, ledger = _ledger(root)
    _segment(ledger)["override_review"] = {
        "verdict": "APPROVE",
        "reviewer": "independent-s0-source-auditor",
    }
    _write(path, ledger)

    assert audit(root) == []
