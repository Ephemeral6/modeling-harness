from __future__ import annotations

import json
from pathlib import Path

from modelharness.evidence import EvidenceGraph
from modelharness.narrative import audit_paper, build_brief
from modelharness.scaffold import create


def test_evidence_verify_and_cascade(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    a = root / "docs" / "a.txt"; a.write_text("a", encoding="utf-8")
    b = root / "docs" / "b.txt"; b.write_text("b", encoding="utf-8")
    graph = EvidenceGraph(root)
    graph.add("a", "claim", "A", "docs/a.txt")
    graph.verify("a")
    graph.add("b", "result", "B", "docs/b.txt", ["a"])
    graph.verify("b")
    assert not graph.audit()
    assert graph.revoke("a", "counterexample") == ["a", "b"]
    assert graph.nodes["b"]["status"] == "revoked"


def test_tamper_and_narrative_audit(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    p = root / "results" / "x.json"
    p.write_text(json.dumps({"x": 1}), encoding="utf-8")
    graph = EvidenceGraph(root)
    graph.add("result.x", "result", "X", "results/x.json")
    graph.verify("result.x")
    (root / "paper" / "draft.md").write_text(
        "结论 [[result.x]]", encoding="utf-8"
    )
    assert not audit_paper(root)
    p.write_text(json.dumps({"x": 2}), encoding="utf-8")
    assert graph.audit()
