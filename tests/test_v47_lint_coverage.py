"""4.7 交付链盲区：正文表格里的结论数字必须和 prose 一样受绑定约束。

端到端演练实测：把 ±1% 区间表放进正文表格后，那三行结果数字
「不受任何绑定约束地进入交付成稿」——`bare_number_in_prose` 只看 prose 与
front_matter，`render_document` 也不替换表格里的占位符。本用例把 table 块的
正负例常驻化，并钉死 equation / code fence 的豁免边界。
"""
from __future__ import annotations

import json
from pathlib import Path

from modelharness.paper_ir import compile_paper


TABLE_SECTION = """# 问题二结果

基础母羊规模为 {num:Q2.ewes} 只。

| 情景 | 年化出栏量 | 相对变化 |
|---|---|---|
| 基准 | 1319.74 | 0.0 |
| 上浮 | 1332.94 | 1.0 |
| 下探 | 1306.54 | -1.0 |
"""


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _project(tmp_path: Path, section: str) -> Path:
    root = tmp_path / "project"
    _write(root / "modeling-project.json", {"schema": 4, "title": "table lint"})
    _write(root / "results/q2.json", {"annual_lambs": 1319.737991, "ewes": 414})
    _write(root / "config/claim_bindings.json", {
        "schema": 1,
        "claims": {
            "Q2.out": {
                "requirement_ids": ["Q2"],
                "source": {
                    "artifact": "results/q2.json", "pointer": "/annual_lambs",
                },
                "value_type": "number",
                "unit": "只/年",
                "display": {"decimals": 2, "rounding": "half_up"},
                "tolerance": {"abs": 0.0, "rel": 1e-9},
            },
            "Q2.ewes": {
                "requirement_ids": ["Q2"],
                "source": {"artifact": "results/q2.json", "pointer": "/ewes"},
                "value_type": "number",
                "unit": "只",
                "display": {"decimals": 0, "rounding": "half_up"},
                "tolerance": {"abs": 0.0, "rel": 1e-9},
            },
        },
    })
    _write(root / "paper/src/manifest.json", {
        "schema": 1, "sections": [{"file": "10_main.md"}],
    })
    _write(root / "paper/src/10_main.md", section)
    return root


def test_bare_number_in_table_blocks_the_draft(tmp_path: Path):
    """±1% 区间表的结果数字必须被 lint 拦住，且不得写出成稿。"""
    root = _project(tmp_path, TABLE_SECTION)
    report = compile_paper(root, check=True)
    assert report["ok"] is False
    for value, line in (("1319.74", 7), ("1332.94", 8), ("1306.54", 9)):
        assert any(
            "bare_number_in_prose" in error
            and f"paper/src/10_main.md:{line}" in error
            and value in error
            for error in report["errors"]
        ), f"表格第 {line} 行的 {value} 未被 lint 覆盖: {report['errors']}"
    # 全量 compile 同样拒绝落盘：裸数字不能进入交付成稿。
    report = compile_paper(root)
    assert report["ok"] is False
    assert not (root / "paper/draft.md").exists()
    assert not (root / "paper/paper_ir.json").exists()


def test_table_placeholders_are_rendered_into_the_draft(tmp_path: Path):
    """表格里的 {num:}/{ev:} 必须像 prose 一样被替换，而不是原样印出。"""
    root = _project(tmp_path, (
        "# 问题二结果\n\n"
        "| 指标 | 取值 | 证据 |\n"
        "|---|---|---|\n"
        "| 年化出栏量 | {num:Q2.out} 只/年 | {ev:EV-q2-plan} |\n"
        "| 基础母羊 | {num:Q2.ewes} 只 | {ev:EV-q2-plan} |\n"
    ))
    report = compile_paper(root)
    assert report["ok"] is True and report["errors"] == []
    draft = (root / "paper/draft.md").read_text(encoding="utf-8")
    assert "| 年化出栏量 | 1319.74 只/年 | [[EV-q2-plan]] |" in draft
    assert "| 基础母羊 | 414 只 | [[EV-q2-plan]] |" in draft
    assert "{num:" not in draft and "{ev:" not in draft
    # IR 也要带上表格的 inline 节点，交付链下游才能顺着占位符回溯。
    ir = json.loads((root / "paper/paper_ir.json").read_text(encoding="utf-8"))
    table = next(
        block for block in ir["sections"][0]["blocks"]
        if block["type"] == "table"
    )
    assert [
        node["claim_id"] for node in table["inlines"]
        if node["type"] == "claim_number"
    ] == ["Q2.out", "Q2.ewes"]


def test_row_index_year_and_whitelist_survive_table_lint(tmp_path: Path):
    """行号、年份、白名单常量不得被误伤。"""
    root = _project(tmp_path, (
        "# 背景\n\n"
        "| 序号 | 年份 | 栏位 |\n"
        "|---|---|---|\n"
        "| 1 | 2023 | 112 |\n"
        "| 2 | 2024 | 112 |\n"
        "| 3 | 2025 | 112 |\n"
    ))
    _write(root / "config/paper_content_contract.json", {
        "schema": 1,
        "number_whitelist": [{"value": 112, "reason": "题面给定的总栏位数"}],
    })
    report = compile_paper(root, check=True)
    assert report["ok"] is True, report["errors"]


def test_first_column_exemption_is_only_for_real_ordinals(tmp_path: Path):
    """首列不是 1..N 序号时不豁免：结果数字不能靠挪到第一列藏起来。"""
    root = _project(tmp_path, (
        "# 结果\n\n"
        "| 年化出栏量 | 情景 |\n"
        "|---|---|\n"
        "| 1319.74 | 基准 |\n"
        "| 1332.94 | 上浮 |\n"
    ))
    report = compile_paper(root, check=True)
    assert report["ok"] is False
    assert any(
        "bare_number_in_prose" in error and "1319.74" in error
        for error in report["errors"]
    )
    assert any(
        "bare_number_in_prose" in error and "1332.94" in error
        for error in report["errors"]
    )


def test_equation_and_fence_numbers_stay_exempt(tmp_path: Path):
    """数学式与代码里的数字是系数/字面量，不是结论数字，按 docs 豁免。"""
    root = _project(tmp_path, (
        "# 推导\n\n"
        "年化出栏量按下式计算：\n\n"
        "$$Y = 2E \\cdot 365 / 229$$\n\n"
        "```python\n"
        "HORIZON = 229\n"
        "peak = 1319.74\n"
        "```\n"
    ))
    report = compile_paper(root, check=True)
    assert report["ok"] is True, report["errors"]
    assert not any("365" in error for error in report["errors"])
    assert not any("1319.74" in error for error in report["errors"])
