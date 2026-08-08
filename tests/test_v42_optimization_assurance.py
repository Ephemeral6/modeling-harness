from __future__ import annotations

import json

from modelharness.coverage import (
    audit_explanation_coverage,
    initialize_coverage_matrix,
)
from modelharness.optimization import (
    assess_optimization,
    audit_constraint_ledger,
    audit_feasibility,
    audit_human_review,
    audit_optimality,
    audit_result_provenance,
    build_result_provenance,
    build_review_packet,
    initialize_constraint_ledger,
    optimization_relevant,
)
from modelharness.problem_graph import ProblemGraph
from modelharness.scaffold import create
from modelharness.util import sha256


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _optimization_project(tmp_path):
    root = create(tmp_path / "project", "optimization assurance")
    _write(root / "config/optimization_assurance.json", {
        "schema": 1,
        "mode": "required",
        "human_policy": "risk_triggered",
        "suspicious_improvement_fraction": 0.01,
        "near_bound_gap_fraction": 0.02,
        "require_human_for": [
            "source_ambiguity",
            "unmodeled_hard_constraint",
            "unresolved_load_bearing_assumption",
        ],
    })
    _write(root / "problem/requirements.json", {
        "schema": 1,
        "requirements": {
            "Q.capacity": {
                "type": "constraint",
                "mandatory": True,
                "question": "Capacity cannot exceed 112 pens.",
                "claim_ids": ["claim.capacity"],
                "status": "satisfied",
            },
            "Q.answer": {
                "type": "answer",
                "mandatory": True,
                "question": "Maximize the number of ewes.",
                "optimization": True,
                "claim_ids": ["claim.ewes"],
                "status": "satisfied",
            },
        },
    })
    initialize_constraint_ledger(root)
    ledger_path = root / "problem/constraint_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    item = ledger["constraints"]["constraint.Q.capacity"]
    item.update({
        "status": "MODELED",
        "mathematical_form": "max_t occupied_pens[t] <= 112",
        "implementation": {
            "solver_artifact": "src/solver.py",
            "checker_artifact": "src/feasibility.py",
        },
    })
    _write(ledger_path, ledger)
    _write(root / "src/solver.py", "def solve(): return 427\n")
    _write(root / "src/feasibility.py", "def check(x): return x <= 434\n")
    _write(root / "results/candidate_solution.json", {
        "candidate_id": "ewes-427",
        "ewes": 427,
    })
    candidate = root / "results/candidate_solution.json"
    solver = root / "src/solver.py"
    checker = root / "src/feasibility.py"
    _write(root / "results/feasibility_audit.json", {
        "schema": 1,
        "execution_status": "completed",
        "verdict": "pass",
        "authority": "machine",
        "constraint_ledger_sha256": sha256(ledger_path),
        "candidate": {
            "id": "ewes-427",
            "artifact": "results/candidate_solution.json",
            "sha256": sha256(candidate),
            "producer_id": "solver-agent",
        },
        "solver": {
            "artifact": "src/solver.py",
            "sha256": sha256(solver),
            "identity": "solver-agent",
        },
        "checker": {
            "artifact": "src/feasibility.py",
            "sha256": sha256(checker),
            "identity": "feasibility-checker",
            "implementation_reuse": False,
        },
        "constraints": {
            "constraint.Q.capacity": {
                "execution_status": "completed",
                "verdict": "pass",
                "max_violation": 0.0,
                "tolerance": 1e-9,
            }
        },
    })
    feasibility_path = root / "results/feasibility_audit.json"
    _write(root / "results/optimality.json", {
        "schema": 1,
        "candidate_id": "ewes-427",
        "scope": "BOUNDED_GAP",
        "model_scope": "All schedules under the documented housing interpretation.",
        "objective": {"sense": "max", "value": 427, "baseline_value": 416},
        "lower_bound": 427,
        "upper_bound": 434,
        "gap_fraction": 7 / 434,
        "gap_definition": "bound",
        "feasibility_audit_sha256": sha256(feasibility_path),
    })
    _write(root / "results/headline.json", {"ewes": 427})
    _write(root / "config/claim_bindings.json", {
        "schema": 1,
        "claims": {
            "claim.ewes": {
                "headline": True,
                "semantic_type": "best_known_solution",
                "candidate_id": "ewes-427",
                "optimality_scope": "BOUNDED_GAP",
                "requirement_ids": ["Q.answer"],
                "source": {
                    "artifact": "results/headline.json",
                    "pointer": "/ewes",
                },
                "value_type": "number",
                "unit": "ewes",
            }
        },
    })
    _write(root / "results/claim_values.json", {
        "schema": 1,
        "generator": "modelharness.claims.evaluate_claims",
        "claims": {
            "claim.ewes": {
                "status": "valid",
                "source_value": 427,
                "source": {
                    "artifact": "results/headline.json",
                    "pointer": "/ewes",
                    "artifact_sha256": sha256(root / "results/headline.json"),
                },
                "derivation_inputs": {},
            }
        },
    })
    build_result_provenance(root)
    return root


def test_v42_is_opt_in_and_reuses_existing_stages(tmp_path):
    plain = create(tmp_path / "plain", "plain analysis")
    assert optimization_relevant(plain) is False
    graph = ProblemGraph(plain)
    assert graph.nodes["s1.model_formulation"]["acceptance"][0]["kind"] == "constraint_coverage"
    assert graph.nodes["s3.solver_validation"]["acceptance"][0]["kind"] == "optimization_assurance"
    assert len(graph.nodes) == 7


def test_independent_feasibility_catches_self_check_and_stale_ledger(tmp_path):
    root = _optimization_project(tmp_path)
    assert audit_constraint_ledger(root, "implementation") == []
    assert audit_feasibility(root) == []

    path = root / "results/feasibility_audit.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["checker"]["identity"] = "solver-agent"
    _write(path, report)
    assert any("identity is not independent" in error for error in audit_feasibility(root))

    report["checker"]["identity"] = "feasibility-checker"
    _write(path, report)
    ledger = root / "problem/constraint_ledger.json"
    value = json.loads(ledger.read_text(encoding="utf-8"))
    value["constraints"]["constraint.Q.capacity"]["mathematical_form"] += " + reserve"
    _write(ledger, value)
    assert any("constraint ledger hash is stale" in error for error in audit_feasibility(root))


def test_optimality_scope_and_semantic_provenance_prevent_overclaim(tmp_path):
    root = _optimization_project(tmp_path)
    assert audit_optimality(root) == []
    assert audit_result_provenance(root) == []

    optimality_path = root / "results/optimality.json"
    optimality = json.loads(optimality_path.read_text(encoding="utf-8"))
    optimality["gap_fraction"] = 0.0
    _write(optimality_path, optimality)
    assert any("understates certified bounds" in error for error in audit_optimality(root))
    optimality["gap_fraction"] = 7 / 434
    _write(optimality_path, optimality)

    binding_path = root / "config/claim_bindings.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding["claims"]["claim.ewes"]["semantic_type"] = "upper_bound"
    _write(binding_path, binding)
    build_result_provenance(root)
    assert any("differs from its optimization semantic" in error for error in audit_result_provenance(root))

    binding["claims"]["claim.ewes"]["semantic_type"] = "optimal_solution"
    _write(binding_path, binding)
    build_result_provenance(root)
    assert any("lacks GLOBAL_CERTIFICATE" in error for error in audit_result_provenance(root))


def test_risk_triggered_human_review_does_not_override_machine_checks(tmp_path):
    root = _optimization_project(tmp_path)
    _write(root / "docs/assumptions.json", {
        "schema": 1,
        "assumptions": {
            "cross_batch": {
                "statement": "Same-stage batches may share pens.",
                "forced_by_source": False,
                "estimated_impact": "high",
                "direction_of_bias": "optimistic",
                "cost_acceptable": True,
                "resolution": "unresolved",
                "alternative_branches": [
                    {"id": "no-sharing", "materialized": False, "results": []}
                ],
            }
        },
    })
    assessment = assess_optimization(root)
    assert assessment["machine_status"] == "READY"
    assert assessment["human_level"] == "HUMAN_REQUIRED"
    assert {item["code"] for item in assessment["triggers"]} >= {
        "suspicious_improvement", "unresolved_load_bearing_assumption",
    }
    packet = build_review_packet(root)
    packet_path = root / "reviews/optimization_review_packet.json"
    _write(root / "reviews/optimization_human_review.json", {
        "schema": 1,
        "authority": "human",
        "verdict": "approve",
        "reviewer_id": "domain-expert",
        "packet_sha256": sha256(packet_path),
        "notes": "The interpretation is accepted for this decision scope.",
    })
    assert packet["assessment"]["human_level"] == "HUMAN_REQUIRED"
    assert audit_human_review(root) == []

    candidate = root / "results/candidate_solution.json"
    _write(candidate, {"candidate_id": "ewes-427", "ewes": 999})
    assert any("cannot override" in error for error in audit_human_review(root))


def test_requirement_to_explanation_matrix_detects_missing_detail(tmp_path):
    root = _optimization_project(tmp_path)
    matrix = initialize_coverage_matrix(root)
    _write(root / "paper/final.md", "# Q1\nThe answer is 427.\n")
    assert any("coverage" in error for error in audit_explanation_coverage(root))

    anchors = {
        "answer": "answer is 427",
        "model": "time-indexed occupancy model",
        "constraints": "peak occupancy is at most 112",
        "algorithm": "branch-and-bound search",
        "derivation": "427 is obtained from the candidate schedule",
        "verification": "independent feasibility checker",
        "scope": "bounded-gap result under the documented interpretation",
    }
    text = "# Q1 complete solution\n" + "\n".join(anchors.values()) + "\n"
    _write(root / "paper/final.md", text)
    record = matrix["requirements"]["Q.answer"]
    for name in record["required_elements"]:
        record["elements"][name] = {
            "section": "Q1 complete solution",
            "anchors": [anchors[name]],
            "evidence_ids": ["claim.ewes"] if name == "verification" else [],
        }
    constraint = matrix["requirements"]["Q.capacity"]
    constraint_anchors = {
        "constraints": anchors["constraints"],
        "verification": anchors["verification"],
        "scope": anchors["scope"],
    }
    for name in constraint["required_elements"]:
        constraint["elements"][name] = {
            "section": "Q1 complete solution",
            "anchors": [constraint_anchors[name]],
            "evidence_ids": ["claim.capacity"] if name == "verification" else [],
        }
    _write(root / "paper/coverage_matrix.json", matrix)
    _write(root / ".harness/evidence.json", {
        "schema": 4,
        "nodes": {
            "claim.ewes": {"status": "verified", "freshness": "valid"},
            "claim.capacity": {"status": "verified", "freshness": "valid"},
        },
    })
    assert audit_explanation_coverage(root) == []
