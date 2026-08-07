from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modelharness import cli_v31
from modelharness.claims import evaluate_claims
from modelharness.narrative_core import CLAIM_RE
from modelharness.paper_ir import compile_paper
from modelharness.scaffold import create
from modelharness.util import sha256


MAIN_SECTION = """# 问题二结果

基础母羊规模为 {num:Q2.ewes} 只，对应年化出栏量 {num:Q2.out} 只/年，
详见证据 {ev:EV-q2-plan}。

$$Y = 2E \\cdot 365 / 229$$

```text
for day in horizon:
    schedule batches
```
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


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    _write(root / "modeling-project.json", {"schema": 4, "title": "paper ir"})
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
        "schema": 1,
        "sections": [{"file": "10_main.md"}],
    })
    _write(root / "paper/src/10_main.md", MAIN_SECTION)
    return root


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def test_compile_twice_is_byte_identical(tmp_path: Path):
    root = _project(tmp_path)
    first = compile_paper(root)
    assert first["ok"] is True and first["errors"] == []
    draft_hash = sha256(root / "paper/draft.md")
    ir_hash = sha256(root / "paper/paper_ir.json")
    assert first["outputs"]["draft_sha256"] == draft_hash
    second = compile_paper(root)
    assert second["ok"] is True
    assert sha256(root / "paper/draft.md") == draft_hash
    assert sha256(root / "paper/paper_ir.json") == ir_hash


def test_num_placeholder_matches_claim_evaluation(tmp_path: Path):
    root = _project(tmp_path)
    compile_paper(root)
    draft = (root / "paper/draft.md").read_text(encoding="utf-8")
    report = evaluate_claims(root)
    assert report["claims"]["Q2.out"]["displayed"] == "1319.74"
    assert report["claims"]["Q2.ewes"]["displayed"] == "414"
    assert "1319.74" in draft
    assert "414" in draft
    assert "1319.737991" not in draft
    assert "{num:" not in draft


def test_ev_placeholder_renders_narrative_marker(tmp_path: Path):
    root = _project(tmp_path)
    compile_paper(root)
    draft = (root / "paper/draft.md").read_text(encoding="utf-8")
    assert "[[EV-q2-plan]]" in draft
    assert "EV-q2-plan" in CLAIM_RE.findall(draft)


def test_check_lints_without_writing(tmp_path: Path):
    root = _project(tmp_path)
    source = root / "paper/src/10_main.md"
    source.write_text(
        source.read_text(encoding="utf-8")
        + "\n手抄补充：年化出栏量 1319.74 只/年。\n",
        encoding="utf-8",
    )
    report = compile_paper(root, check=True)
    assert report["ok"] is False
    assert any(
        "bare_number_in_prose" in error
        and "paper/src/10_main.md:" in error
        and "1319.74" in error
        for error in report["errors"]
    )
    assert not (root / "paper/draft.md").exists()
    assert not (root / "paper/paper_ir.json").exists()
    assert not (root / "results/claim_values.json").exists()
    # lint 未通过时全量 compile 也不得写盘。
    report = compile_paper(root)
    assert report["ok"] is False
    assert not (root / "paper/draft.md").exists()


def test_bare_number_whitelist_and_year_exemption(tmp_path: Path):
    root = _project(tmp_path)
    _write(
        root / "paper/src/10_main.md",
        "# 背景\n\n本题源自 2023 年竞赛，全场共有 112 栏。\n",
    )
    report = compile_paper(root, check=True)
    assert any(
        "bare_number_in_prose" in error and "112" in error
        for error in report["errors"]
    )
    assert not any("2023" in error for error in report["errors"])
    _write(root / "config/paper_content_contract.json", {
        "schema": 1,
        "number_whitelist": [
            {"value": 112, "reason": "题面给定的总栏位数"},
        ],
    })
    report = compile_paper(root, check=True)
    assert report["ok"] is True


def test_whitelist_entry_without_reason_is_invalid(tmp_path: Path):
    root = _project(tmp_path)
    _write(
        root / "paper/src/10_main.md",
        "# 背景\n\n全场共有 112 栏。\n",
    )
    _write(root / "config/paper_content_contract.json", {
        "schema": 1,
        "number_whitelist": [{"value": 112}],
    })
    report = compile_paper(root, check=True)
    assert any(
        "invalid_number_whitelist" in error and "reason" in error
        for error in report["errors"]
    )
    # 非法白名单项不产生豁免。
    assert any(
        "bare_number_in_prose" in error and "112" in error
        for error in report["errors"]
    )


def test_unbound_and_stale_claim(tmp_path: Path):
    root = _project(tmp_path)
    _write(
        root / "paper/src/10_main.md",
        "# 结果\n\n未知引用 {num:Q9.missing}，漂移引用 {num:Q2.out}。\n",
    )
    bindings = json.loads(
        (root / "config/claim_bindings.json").read_text(encoding="utf-8")
    )
    bindings["claims"]["Q2.out"]["derivation"] = {
        "expr": "2 * ewes",
        "inputs": {
            "ewes": {"artifact": "results/q2.json", "pointer": "/ewes"},
        },
    }
    _write(root / "config/claim_bindings.json", bindings)
    report = compile_paper(root, check=True)
    assert any(
        "unbound_claim" in error and "Q9.missing" in error
        for error in report["errors"]
    )
    assert any(
        "stale_claim_value" in error and "Q2.out" in error
        for error in report["errors"]
    )


def test_cross_caliber_arithmetic_positive_and_negative(tmp_path: Path):
    root = _project(tmp_path)
    _write(
        root / "paper/src/10_main.md",
        "# 差距\n\n上界与规模之比：{num:Q2.out} / {num:Q2.ewes} 的口径混算。\n",
    )
    report = compile_paper(root, check=True)
    assert any(
        "cross_caliber_arithmetic" in error
        and "Q2.out" in error and "Q2.ewes" in error
        for error in report["errors"]
    )
    bindings = json.loads(
        (root / "config/claim_bindings.json").read_text(encoding="utf-8")
    )
    bindings["claims"]["Q2.out"]["derived_from"] = ["Q2.ewes"]
    _write(root / "config/claim_bindings.json", bindings)
    report = compile_paper(root, check=True)
    assert not any(
        "cross_caliber_arithmetic" in error for error in report["errors"]
    )
    assert report["ok"] is True


def test_decision_placeholder_renders_manifest_statement(tmp_path: Path):
    root = _project(tmp_path)
    _write(
        root / "paper/src/10_main.md",
        "# 决策口径\n\n本文回答的决策变量：{decision:D1}。\n",
    )
    report = compile_paper(root, check=True)
    assert any(
        "unbound_decision_reference" in error and "D1" in error
        for error in report["errors"]
    )
    _write(root / "results/decision_variable_manifest.json", {
        "schema": 1,
        "variables": [
            {
                "id": "D1",
                "role": "primary",
                "statement": "以基础母羊存栏规模作为唯一决策变量",
                "source": {"path": "results/nominal.json", "sha256": "0" * 64},
            }
        ],
    })
    report = compile_paper(root)
    assert report["ok"] is True
    draft = (root / "paper/draft.md").read_text(encoding="utf-8")
    assert "以基础母羊存栏规模作为唯一决策变量" in draft
    assert "{decision:" not in draft


def test_check_without_manifest_is_noop_for_legacy(tmp_path: Path):
    root = tmp_path / "legacy"
    _write(root / "modeling-project.json", {"schema": 4, "title": "legacy"})
    report = compile_paper(root, check=True)
    assert report["ok"] is True
    assert report["adopted"] is False
    with pytest.raises(ValueError):
        compile_paper(root)


def test_scaffolded_template_source_lints_clean(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    report = compile_paper(root, check=True)
    assert report["adopted"] is True
    assert report["errors"] == []


def test_cli_compile_check_exit_codes(tmp_path: Path, monkeypatch, capsys):
    root = _project(tmp_path)
    code = run_cli(
        monkeypatch, "paper", "compile", "--check", "--project", str(root)
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert not (root / "paper/draft.md").exists()
    source = root / "paper/src/10_main.md"
    original = source.read_text(encoding="utf-8")
    source.write_text(
        original + "\n手抄补充：年化出栏量 1319.74 只/年。\n",
        encoding="utf-8",
    )
    code = run_cli(
        monkeypatch, "paper", "compile", "--check", "--project", str(root)
    )
    assert code == 1
    capsys.readouterr()
    source.write_text(original, encoding="utf-8")
    code = run_cli(monkeypatch, "paper", "compile", "--project", str(root))
    assert code == 0
    capsys.readouterr()
    assert (root / "paper/draft.md").is_file()
    assert (root / "paper/paper_ir.json").is_file()
