import json

import pytest

pytestmark = pytest.mark.regression


def test_repair_verify_catches_out_of_scope_change(
    regression_project, regression_api
):
    defect = (
        "修复声明 scope 只含 paper/src/20_model.md，却同时改了 "
        "results/q2.json；verify 未按快照差集报 out_of_scope_change，"
        "域外改动溜进主线"
    )
    begin = regression_api("modelharness.repairs", "begin_repair", defect)
    verify = regression_api("modelharness.repairs", "verify_repair", defect)
    root = regression_project("repair_scope_breach")
    record = begin(
        root, "reviews/audit_findings.json#F-2023D-01", ["paper/src/20_*.md"]
    )

    model = root / "paper" / "src" / "20_model.md"
    model.write_text(
        model.read_text(encoding="utf-8")
        + "\n补充说明：利用率口径以题面栏舍约束为准。\n",
        encoding="utf-8",
    )
    (root / "results" / "q2.json").write_text(
        json.dumps(
            {"schema": 1, "annual_lambs": 1400.0, "ewes": 414},
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    report = verify(root, record["id"])
    assert report["ok"] is False, defect
    breaches = [
        violation for violation in report["violations"]
        if violation.startswith("out_of_scope_change")
    ]
    # 域外改动必须被抓，且声明域内的改动不得被误报。
    assert breaches == ["out_of_scope_change: results/q2.json"], defect
