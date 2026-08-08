import pytest

pytestmark = pytest.mark.regression


def test_self_imposed_disclosure(regression_project, regression_api):
    defect = (
        "冻结合同与约束账本里的自设口径（20% 最小面积、分散度上限）在 final.md 中"
        "没有任何披露节，论文内容审计仍判通过"
    )
    initialize = regression_api(
        "modelharness.paper_content", "initialize_content_coverage", defect
    )
    audit = regression_api(
        "modelharness.paper_content", "audit_paper_content", defect
    )
    root = regression_project("self_imposed_disclosure")
    initialize(root)

    errors = audit(root)
    assert any(
        "calib.min_planting_area_fraction" in error
        and "自设口径与管理假设" in error
        for error in errors
    ), defect
    assert any(
        "constraint.management.dispersion_cap" in error
        and "分散度上限的管理解释" in error
        for error in errors
    ), defect

    final = root / "paper" / "final.md"
    # 只补标题、正文留空：披露节存在但为空，仍不算披露。
    final.write_text(
        final.read_text(encoding="utf-8")
        + "\n## 自设口径与管理假设\n\n## 分散度上限的管理解释\n",
        encoding="utf-8",
    )
    errors = audit(root)
    assert any("自设口径与管理假设" in error for error in errors), defect
    assert any("分散度上限的管理解释" in error for error in errors), defect

    final.write_text(
        final.read_text(encoding="utf-8").replace(
            "## 自设口径与管理假设\n",
            "## 自设口径与管理假设\n\n题面只要求“便于田间管理”，未给数值；本文自定"
            "单作物最小种植面积为地块面积的 20%，该口径跨 run 冻结，改动需写明理由。\n",
        ).replace(
            "## 分散度上限的管理解释\n",
            "## 分散度上限的管理解释\n\n同一年—季—作物在每个管理分组内最多启用固定"
            "数量地块，用于抑制过度分散；该上限是自设管理口径而非题面数据。\n",
        ),
        encoding="utf-8",
    )
    assert audit(root) == [], defect
