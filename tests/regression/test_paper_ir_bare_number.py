import pytest

pytestmark = pytest.mark.regression


def test_paper_ir_rejects_hand_copied_number(regression_project, regression_api):
    defect = (
        "paper/src/10_results.md:5 手抄数字 1319.74 绕过 {num:} 占位符，"
        "binding 改动后正文不再同步"
    )
    compile_paper = regression_api(
        "modelharness.paper_ir", "compile_paper", defect
    )
    root = regression_project("paper_ir_bare_number")
    report = compile_paper(root, check=True)
    assert report["ok"] is False, defect
    assert any(
        "bare_number_in_prose" in error
        and "paper/src/10_results.md:5" in error
        and "1319.74" in error
        for error in report["errors"]
    ), defect
    # 经 {num:} 占位符的同一数字不应报错：全部错误都只指向手抄行。
    assert all(
        "bare_number_in_prose" in error for error in report["errors"]
    ), defect
