import pytest

pytestmark = pytest.mark.regression


def test_repair_verify_catches_newly_introduced_defect(
    regression_project, regression_api
):
    defect = (
        "修复在声明域内手抄了数字 1319.74，verify 的护栏违规差集应报 "
        "repair_introduced_defect；否则每轮修复至少引入一个新缺陷"
    )
    begin = regression_api("modelharness.repairs", "begin_repair", defect)
    verify = regression_api("modelharness.repairs", "verify_repair", defect)
    root = regression_project("repair_introduced_defect")
    record = begin(
        root,
        "reviews/audit_findings.json#F-display-drift",
        ["paper/src/10_results.md"],
    )

    section = root / "paper" / "src" / "10_results.md"
    section.write_text(
        section.read_text(encoding="utf-8")
        + "\n补充：与手工推算 1319.74 只/年一致。\n",
        encoding="utf-8",
    )

    report = verify(root, record["id"])
    assert report["ok"] is False, defect
    assert any(
        violation.startswith("repair_introduced_defect")
        and "bare_number_in_prose" in violation
        and "1319.74" in violation
        for violation in report["violations"]
    ), defect
    # 域内改动本身不构成 out_of_scope_change。
    assert not any(
        violation.startswith("out_of_scope_change")
        for violation in report["violations"]
    ), defect
    # begin 时已存在的基线违规（缺 registered.json 等）不得复报为新增缺陷。
    assert not any(
        "registered_missing" in violation
        for violation in report["violations"]
    ), defect
