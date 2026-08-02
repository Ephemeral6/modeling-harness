from __future__ import annotations

import json

from modelharness.checks_core import evaluate_acceptance
from modelharness.evaluation import score_project
from modelharness.evidence import EvidenceGraph
from modelharness.narrative import audit_paper
from modelharness.problem_graph import ProblemGraph
from modelharness.scaffold import create
from modelharness.supervisor import StateCapsule


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_frontier_new_value_factors_are_opt_in(tmp_path):
    root = create(tmp_path / "project", "frontier")
    graph = ProblemGraph(root)
    baseline = graph.frontier({}, [], "s0")[0]["priority"]
    proposal = graph.data
    proposal["nodes"]["s0.problem_definition"]["risk"].update({
        "improvement_value": 0.5,
        "coverage_value": 0.25,
    })
    graph.replace(proposal, "exercise M7 value factors")

    assert baseline == 20.0
    assert graph.frontier({}, [], "s0")[0]["priority"] == 2.5


def test_profile_mandatory_outputs_require_verified_evidence(tmp_path):
    root = create(tmp_path / "project", "profile acceptance")
    _write(root / "config/delivery_profile.json", {
        "name": "test",
        "version": "1",
        "renderer": "test",
        "mandatory_outputs": ["result"],
        "quality_dimensions": [],
    })
    acceptance = [{"kind": "profile_mandatory_outputs"}]
    assert evaluate_acceptance(root, acceptance)["ok"] is False
    artifact = root / "results" / "answer.json"
    _write(artifact, {"answer": 1})
    evidence = EvidenceGraph(root)
    evidence.add("answer.result", "result", "answer", "results/answer.json")
    evidence.verify("answer.result")

    assert evaluate_acceptance(root, acceptance)["ok"] is True


def test_evaluation_adds_answer_quality_without_replacing_old_keys(tmp_path):
    root = create(tmp_path / "project", "quality")
    _write(root / "problem/requirements.json", {
        "schema": 1,
        "requirements": {
            "Q.answer": {
                "type": "answer",
                "mandatory": True,
                "expected": {"kind": "number"},
                "claim_ids": [],
                "status": "open",
            }
        },
    })
    _write(root / "results/research_diagnostics.json", {
        "opportunities": [
            {"id": "opp-test", "severity": "high", "kind": "manual"}
        ]
    })
    _write(root / "results/opportunity_outcomes.json", {
        "schema": 1,
        "outcomes": [{
            "opportunity_id": "opp-test",
            "action": "expanded_search",
            "reason_code": "resolved_interior",
            "realized_delta": {"metric": "loss", "before": 2, "after": 1},
        }],
    })
    _write(root / "results/delivery_check.json", {
        "schema": 1,
        "violations": [{"kind": "missing_section"}],
    })

    report = score_project(root)
    quality = report["answer_quality"]

    assert report["schema"] == 3
    assert "problem_graph" in report
    assert quality["requirement_coverage_rate"] == 0.0
    assert quality["mandatory_open"] == ["Q.answer"]
    assert quality["delivery_violations"] == [{"kind": "missing_section"}]
    assert quality["open_opportunities"]["high"] == 0
    assert quality["discharge_mix"]["expanded_search"] == 1
    assert quality["opportunity_hit_rate"] == 1.0
    assert quality["holdout_separation_ok"] is None
    assert set(quality["quality_dimensions"]) == {
        "correctness", "reproducibility", "calibration"
    }


def test_evaluation_counts_registered_claim_drift(tmp_path):
    root = create(tmp_path / "project", "claim drift")
    _write(root / "config/claim_bindings.json", {
        "schema": 1,
        "claims": {
            "Q.value": {
                "value_type": "number",
                "tolerance": {"abs": 0.01},
            }
        },
    })
    _write(root / "results/claim_values.json", {
        "schema": 1,
        "generator": "modelharness.claims.evaluate_claims",
        "claims": {
            "Q.value": {
                "status": "valid",
                "source_value": 10.0,
                "errors": [],
            }
        },
    })
    _write(root / "predictions/registered.json", {
        "schema": 1,
        "claims": {"Q.value": {"value": 11.0}},
    })

    assert score_project(root)["answer_quality"]["claim_drift_count"] == 1


def test_old_project_without_v41_artifacts_remains_readable(tmp_path):
    root = create(tmp_path / "legacy", "legacy")
    for relative in (
        "config/scheduling.json",
        "docs/assumptions.json",
        "docs/assumptions.md",
        "paper/final.md",
        "prompts/roles/improvement-review.md",
    ):
        path = root / relative
        if path.is_file():
            path.unlink()

    state = StateCapsule(root).build()
    report = score_project(root)
    paper_errors = audit_paper(root)

    assert "ledgers" in state
    assert "answer_quality" in report
    assert isinstance(paper_errors, list)
