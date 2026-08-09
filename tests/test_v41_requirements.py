from __future__ import annotations

import json

from modelharness.checks_core import evaluate_acceptance
from modelharness.claims import GENERATOR
from modelharness.problem_graph import ProblemGraph
from modelharness.requirements import (
    audit_requirement_extraction,
    extract_sources,
)
from modelharness.scaffold import create
from modelharness.scheduler import AdaptiveScheduler


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def test_source_presence_activates_s0_requirement_outputs(tmp_path):
    root = create(tmp_path / "project", "requirements")
    graph = ProblemGraph(root)
    assert graph.output_ids("s0.problem_definition") == [
        "problem.statement",
        "problem.success",
    ]
    contract_before_source = graph.contract_hash("s0.problem_definition")
    _write(root / "problem/data_raw/problem.txt", "请给出合理数量。")

    assert graph.contract_hash("s0.problem_definition") != contract_before_source
    assert graph.output_ids("s0.problem_definition") == [
        "problem.statement",
        "problem.success",
        "problem.requirements",
        "problem.segmentation",
    ]
    packet = AdaptiveScheduler(root).next_packet()
    acceptance = packet["tasks"][0]["acceptance"]
    assert any(
        item.get("kind") == "requirement_coverage"
        and item.get("phase") == "extraction"
        for item in acceptance
    )
    assert any(
        item.get("path") == "problem/requirements.json"
        for item in acceptance
    )


def test_requirement_extraction_requires_bidirectional_mapping(tmp_path):
    root = tmp_path
    _write(root / "problem/data_raw/problem.txt", "请估算年化产量范围。")
    segmentation = extract_sources(root)
    segment_id = segmentation["sources"][0]["segments"][0]["id"]
    _write(root / "problem/requirements.json", {
        "schema": 1,
        "source_segmentation": "problem/source_segmentation.json",
        "requirements": {
            "Q1.output_range": {
                "source": {"segment_ids": [segment_id]},
                "type": "answer",
                "mandatory": True,
                "question": "估算范围",
                "expected": {
                    "kind": "interval",
                    "fields": ["lower", "upper"],
                },
                "claim_ids": [],
                "status": "open",
            }
        },
    })
    assert any("未映射" in error for error in audit_requirement_extraction(root))
    path = root / "problem/source_segmentation.json"
    segmentation = json.loads(path.read_text(encoding="utf-8"))
    segmentation["sources"][0]["segments"][0]["requirement_ids"] = [
        "Q1.output_range"
    ]
    _write(path, segmentation)

    assert audit_requirement_extraction(root) == []


def test_requirement_coverage_checks_verified_fresh_claim_evidence(tmp_path):
    root = tmp_path
    _write(root / "problem/requirements.json", {
        "schema": 1,
        "requirements": {
            "Q.answer": {
                "type": "answer",
                "mandatory": True,
                "question": "answer",
                "expected": {"kind": "number"},
                "claim_ids": ["claim.answer"],
                "status": "satisfied",
            }
        },
    })
    _write(root / "config/claim_bindings.json", {
        "schema": 1,
        "claims": {
            "claim.answer": {
                "requirement_ids": ["Q.answer"],
                "value_type": "number",
                "scenario_id": "baseline",
            }
        },
    })
    _write(root / "results/claim_values.json", {
        "schema": 1,
        "generator": GENERATOR,
        "claims": {
            "claim.answer": {"status": "valid", "source_value": 4.0}
        },
    })
    evidence_path = root / ".harness/evidence.json"
    _write(evidence_path, {
        "schema": 4,
        "nodes": {
            "claim.answer": {
                "status": "verified",
                "freshness": "stale",
            }
        },
    })
    acceptance = [{"kind": "requirement_coverage"}]

    assert evaluate_acceptance(root, acceptance)["ok"] is False
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["nodes"]["claim.answer"]["freshness"] = "valid"
    _write(evidence_path, evidence)
    assert evaluate_acceptance(root, acceptance)["ok"] is True


def test_extraction_skips_binary_attachments(tmp_path):
    """Intake keeps the official PDF verbatim; segmentation must not crash."""
    root = tmp_path
    _write(root / "problem/data_raw/001__statement.md", "请估算年化出栏范围。")
    (root / "problem/data_raw/002__statement.pdf").write_bytes(
        b"%PDF-1.7\n\xb5\xff\xfe binary payload \x00\x01\x02"
    )

    segmentation = extract_sources(root)

    assert [item["artifact"] for item in segmentation["sources"]] == [
        "problem/data_raw/001__statement.md"
    ]
    skipped = segmentation["skipped_sources"]
    assert [item["artifact"] for item in skipped] == [
        "problem/data_raw/002__statement.pdf"
    ]
    assert skipped[0]["sha256"] and skipped[0]["reason"]
    assert segmentation["sources"][0]["segments"]


def test_extraction_still_fails_when_no_text_source_exists(tmp_path):
    root = tmp_path
    (root / "problem/data_raw").mkdir(parents=True)
    (root / "problem/data_raw/only.pdf").write_bytes(b"%PDF-1.7\n\xb5\xff")

    try:
        extract_sources(root)
    except ValueError as exc:
        assert "没有可分句源文件" in str(exc)
    else:
        raise AssertionError("binary-only data_raw must not silently pass")
