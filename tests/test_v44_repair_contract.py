from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modelharness import cli_v31
from modelharness.paper_ir import compile_paper
from modelharness.repair_core import (
    DERIVED_ARTIFACTS,
    repair_pass_k,
    snapshot_excluded,
)
from modelharness.repairs import begin_repair, verify_repair
from modelharness.util import sha256


SECTION = """# 结果

年化出栏量为 {num:Q2.out} 只/年。
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
    _write(root / "modeling-project.json", {"schema": 4, "title": "repair"})
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
        },
    })
    _write(root / "paper/src/manifest.json", {
        "schema": 1,
        "sections": [{"file": "10_main.md"}],
    })
    _write(root / "paper/src/10_main.md", SECTION)
    _write(root / "reviews/s1_referee.json", {
        "schema": 1,
        "findings": [{"id": "F1", "summary": "结果节口径表述含混"}],
    })
    return root


def _scoped_edit(root: Path) -> None:
    """声明域内的一次干净修复：措辞变化，数字仍走占位符。"""
    _write(
        root / "paper/src/10_main.md",
        "# 结果\n\n修复后表述：年化出栏量为 {num:Q2.out} 只/年（产羔口径）。\n",
    )


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def test_derived_artifacts_and_snapshot_exclusion():
    for relative in (
        "paper/draft.md", "paper/final.md", "paper/paper_ir.json",
        "paper/render_report.json", "results/claim_values.json",
        "results/paper_coverage.json", "results/delivery_check.json",
    ):
        assert relative in DERIVED_ARTIFACTS
    assert snapshot_excluded(".harness/repairs/R001.json")
    assert snapshot_excluded("src/__pycache__/model.cpython-313.pyc")
    assert snapshot_excluded(".harness/evidence.json.lock")
    assert not snapshot_excluded("src/model.py")
    assert not snapshot_excluded(".harness/evidence.json")


def test_begin_records_append_only_contract(tmp_path: Path):
    root = _project(tmp_path)
    record = begin_repair(
        root, "reviews/s1_referee.json#F1", ["paper\\src\\*.md"]
    )
    assert record["id"] == "R001"
    path = root / ".harness" / "repairs" / "R001.json"
    assert path.is_file()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["finding"] == {
        "path": "reviews/s1_referee.json",
        "sha256": sha256(root / "reviews/s1_referee.json"),
        "entry_id": "F1",
    }
    # Windows 风格 glob 归一化为 POSIX。
    assert stored["scope"] == ["paper/src/*.md"]
    snapshot = stored["snapshot"]
    assert "results/q2.json" in snapshot
    assert "paper/src/10_main.md" in snapshot
    # 派生物与修复记录自身不进快照（基线护栏已生成 claim_values）。
    assert (root / "results/claim_values.json").is_file()
    assert "results/claim_values.json" not in snapshot
    assert ".harness/repairs/R001.json" not in snapshot
    assert stored["guardrail_baseline"] == sorted(
        set(stored["guardrail_baseline"])
    )
    assert stored["verifies"] == []
    # 同一项目再 begin 分配新 id，不覆盖旧记录。
    second = begin_repair(root, "reviews/s1_referee.json#F1", ["paper/*"])
    assert second["id"] == "R002"


def test_begin_rejects_bad_finding_or_scope(tmp_path: Path):
    root = _project(tmp_path)
    with pytest.raises(ValueError):
        begin_repair(root, "reviews/s1_referee.json", ["paper/*"])
    with pytest.raises(ValueError):
        begin_repair(root, "reviews/missing.json#F1", ["paper/*"])
    with pytest.raises(ValueError):
        begin_repair(root, "reviews/s1_referee.json#F999", ["paper/*"])
    with pytest.raises(ValueError):
        begin_repair(root, "reviews/s1_referee.json#F1", [])


def test_verify_green_path_tolerates_derived_regeneration(tmp_path: Path):
    root = _project(tmp_path)
    assert compile_paper(root)["ok"] is True
    record = begin_repair(root, "reviews/s1_referee.json#F1", ["paper/src/*.md"])
    _scoped_edit(root)
    # 修复后重编译：draft/paper_ir 属派生物，不构成域外改动。
    assert compile_paper(root)["ok"] is True
    report = verify_repair(root, record["id"])
    assert report["violations"] == []
    assert report["ok"] is True
    # (c) 情形一：项目没有回归语料文件时跳过并注明。
    assert any("regression_corpus" in note for note in report["notes"])
    stored = json.loads(
        (root / ".harness/repairs/R001.json").read_text(encoding="utf-8")
    )
    assert [item["ok"] for item in stored["verifies"]] == [True]


def test_verify_requires_new_regression_entry(tmp_path: Path):
    root = _project(tmp_path)
    compile_paper(root)
    corpus = root / "docs" / "regression_corpus.json"
    _write(corpus, {"schema": 1, "entries": []})
    record = begin_repair(root, "reviews/s1_referee.json#F1", ["paper/src/*.md"])
    _scoped_edit(root)
    compile_paper(root)
    # (c) 情形二：语料存在但没有新增引用该 finding 的条目。
    report = verify_repair(root, record["id"])
    assert report["ok"] is False
    assert any(
        violation.startswith("repair_without_regression_entry")
        and "F1" in violation
        for violation in report["violations"]
    )
    # 引用了其他 finding 也不算数。
    _write(corpus, {"schema": 1, "entries": [
        {"id": "OTHER-regress", "finding": "F999", "kind": "forbidden_text"},
    ]})
    report = verify_repair(root, record["id"])
    assert any(
        violation.startswith("repair_without_regression_entry")
        for violation in report["violations"]
    )
    # (c) 情形三：新增引用该 finding 的条目后放行；语料改动也不算域外。
    _write(corpus, {"schema": 1, "entries": [
        {
            "id": "F1-regress",
            "finding": "F1",
            "kind": "required_text",
            "targets": ["paper/src/10_main.md"],
            "pattern": "产羔口径",
        },
    ]})
    report = verify_repair(root, record["id"])
    assert report["ok"] is True
    assert report["violations"] == []


def test_verify_rejects_stale_unchanged_corpus_citation(tmp_path: Path):
    root = _project(tmp_path)
    compile_paper(root)
    corpus = root / "docs" / "regression_corpus.json"
    # begin 前语料已引用 F1；修复期间语料一字未动，不算"新增"。
    _write(corpus, {"schema": 1, "entries": [
        {"id": "F1-regress", "finding": "F1", "kind": "forbidden_text"},
    ]})
    record = begin_repair(root, "reviews/s1_referee.json#F1", ["paper/src/*.md"])
    _scoped_edit(root)
    compile_paper(root)
    report = verify_repair(root, record["id"])
    assert any(
        violation.startswith("repair_without_regression_entry")
        for violation in report["violations"]
    )


def test_verify_unknown_repair_id(tmp_path: Path):
    root = _project(tmp_path)
    with pytest.raises(ValueError):
        verify_repair(root, "R999")
    with pytest.raises(ValueError):
        verify_repair(root, "../evil")


def test_repair_pass_k_over_recent_verifies(tmp_path: Path):
    root = _project(tmp_path)
    compile_paper(root)
    assert repair_pass_k(root, 1) is None
    for _ in range(4):
        record = begin_repair(
            root, "reviews/s1_referee.json#F1", ["paper/src/*.md"]
        )
        assert verify_repair(root, record["id"])["ok"] is True
    record = begin_repair(root, "reviews/s1_referee.json#F1", ["paper/src/*.md"])
    _write(root / "results/extra.json", {"schema": 1, "value": 1})
    assert verify_repair(root, record["id"])["ok"] is False
    assert repair_pass_k(root, 5) == pytest.approx(0.8)
    assert repair_pass_k(root, 1) == 0.0
    assert repair_pass_k(root, 4) == pytest.approx(0.75)
    assert repair_pass_k(root, 6) is None
    assert repair_pass_k(root, 0) is None


def test_cli_repair_begin_verify_exit_codes(tmp_path: Path, monkeypatch, capsys):
    root = _project(tmp_path)
    compile_paper(root)
    code = run_cli(
        monkeypatch, "repair", "begin",
        "--finding", "reviews/s1_referee.json#F1",
        "--scope", "paper/src/*.md",
        "--project", str(root),
    )
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is True and summary["id"] == "R001"
    assert summary["snapshot_files"] > 0
    code = run_cli(
        monkeypatch, "repair", "verify", "--repair", "R001",
        "--project", str(root),
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    _write(root / "results/rogue.json", {"schema": 1})
    code = run_cli(
        monkeypatch, "repair", "verify", "--repair", "R001",
        "--project", str(root),
    )
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert "out_of_scope_change: results/rogue.json" in report["violations"]
    # verify 结果 append-only 写回记录。
    stored = json.loads(
        (root / ".harness/repairs/R001.json").read_text(encoding="utf-8")
    )
    assert [item["ok"] for item in stored["verifies"]] == [True, False]
