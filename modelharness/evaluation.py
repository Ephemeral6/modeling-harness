"""Mechanical evaluation of research, computation and project traces."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .contracts import STAGES
from .evidence import EvidenceGraph
from .integration import audit_integration
from .method_packs import MethodPackRegistry
from .claims import GENERATOR, audit_holdout
from .opportunities import build_ledger
from .problem_graph import ProblemGraph
from .requirements import audit_requirements
from .stages import StageService
from .storage import read_json
from .toolchain import ToolchainService, validate_tool_policy
from .toolchain_registry import ToolRegistry
from .util import project_root
from .workflow import WorkflowEngine


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _favorable_outcome(item: dict) -> bool:
    for field in ("favorable", "improved"):
        if isinstance(item.get(field), bool):
            return item[field]
    direction = str(item.get("direction", "")).casefold()
    if direction in {"favorable", "improved", "beneficial", "positive"}:
        return True
    if direction in {"unfavorable", "degraded", "harmful", "negative"}:
        return False
    delta = item.get("realized_delta")
    if isinstance(delta, (int, float)) and not isinstance(delta, bool):
        return delta > 0
    if not isinstance(delta, dict):
        return False
    if isinstance(delta.get("favorable"), bool):
        return delta["favorable"]
    before, after = delta.get("before"), delta.get("after")
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in (before, after)
    ):
        return False
    metric = str(delta.get("metric", "")).casefold()
    lower_is_better = any(
        token in metric
        for token in ("loss", "cost", "error", "regret", "risk", "violation")
    )
    return after < before if lower_is_better else after > before


def _dimension_scores(profile: dict, quality: dict) -> dict:
    metric_for = {
        "correctness": "claim_closure_rate",
        "numeric_correctness": "claim_closure_rate",
        "model_justification": "claim_closure_rate",
        "conceptual_clarity": "requirement_coverage_rate",
        "process_completeness": "requirement_coverage_rate",
        "reproducibility": "claim_closure_rate",
        "robustness": "holdout_separation_ok",
        "calibration": "holdout_separation_ok",
        "communication": "delivery_violations",
        "decision_utility": "requirement_coverage_rate",
        "operational_feasibility": "requirement_coverage_rate",
        "data_governance": "requirement_coverage_rate",
        "monitorability": "requirement_coverage_rate",
    }
    scores = {}
    for dimension in profile.get("quality_dimensions", []):
        metric = metric_for.get(
            str(dimension), "requirement_coverage_rate"
        )
        actual = quality.get(metric)
        if metric == "delivery_violations":
            score = 1.0 if isinstance(actual, list) and not actual else 0.0
        elif isinstance(actual, bool):
            score = 1.0 if actual else 0.0
        elif isinstance(actual, (int, float)):
            score = max(0.0, min(1.0, float(actual)))
        else:
            score = None
        scores[str(dimension)] = {
            "metric": metric,
            "value": actual,
            "score": score,
        }
    return scores


def _claim_values_equal(left, right, tolerance: dict) -> bool:
    if not isinstance(tolerance, dict):
        tolerance = {}
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        try:
            return math.isclose(
                float(left),
                float(right),
                rel_tol=float(tolerance.get("rel", 0)),
                abs_tol=float(tolerance.get("abs", 0)),
            )
        except (TypeError, ValueError):
            return False
    return left == right


def _answer_quality(project: Path) -> dict:
    requirement_doc = read_json(
        project / "problem" / "requirements.json", {}
    )
    requirements = (
        requirement_doc.get("requirements", {})
        if isinstance(requirement_doc, dict) else {}
    )
    if not isinstance(requirements, dict):
        requirements = {}
    requirement_errors = (
        audit_requirements(project) if requirements else []
    )
    mandatory = {
        requirement_id: item
        for requirement_id, item in requirements.items()
        if isinstance(item, dict) and item.get("mandatory") is True
    }
    global_requirement_error = any(
        not any(error.startswith(requirement_id) for requirement_id in requirements)
        for error in requirement_errors
    )
    mandatory_open = []
    for requirement_id, item in mandatory.items():
        has_error = global_requirement_error or any(
            error.startswith(requirement_id)
            for error in requirement_errors
        )
        if (
            item.get("status") not in {"satisfied", "not_applicable"}
            or has_error
        ):
            mandatory_open.append(requirement_id)
    requirement_rate = (
        (len(mandatory) - len(mandatory_open)) / len(mandatory)
        if mandatory else 0.0
    )

    bindings_doc = read_json(
        project / "config" / "claim_bindings.json", {}
    )
    bindings = (
        bindings_doc.get("claims", {})
        if isinstance(bindings_doc, dict) else {}
    )
    if not isinstance(bindings, dict):
        bindings = {}
    values_doc = read_json(
        project / "results" / "claim_values.json", {}
    )
    values = (
        values_doc.get("claims", {})
        if isinstance(values_doc, dict)
        and values_doc.get("generator") == GENERATOR
        else {}
    )
    if not isinstance(values, dict):
        values = {}
    valid_claims = sum(
        1 for claim_id in bindings
        if isinstance(values.get(claim_id), dict)
        and values[claim_id].get("status") == "valid"
    )
    claim_closure_rate = (
        valid_claims / len(bindings) if bindings else 0.0
    )
    drifted_claims = {
        claim_id for claim_id in bindings
        if isinstance(values.get(claim_id), dict)
        and (
            values[claim_id].get("status") == "value_drift"
            or any(
                "value_drift" in str(error)
                for error in values[claim_id].get("errors", [])
            )
        )
    }
    registered_doc = read_json(
        project / "predictions" / "registered.json", {}
    )
    registered = (
        registered_doc.get("claims", {})
        if isinstance(registered_doc, dict) else {}
    )
    if isinstance(registered, dict):
        for claim_id, binding in bindings.items():
            record = values.get(claim_id)
            if (
                claim_id not in registered
                or not isinstance(binding, dict)
                or not isinstance(record, dict)
                or record.get("status") != "valid"
            ):
                continue
            locked = registered[claim_id]
            if isinstance(locked, dict) and "value" in locked:
                locked = locked["value"]
            if not _claim_values_equal(
                locked,
                record.get("source_value"),
                binding.get("tolerance", {}),
            ):
                drifted_claims.add(claim_id)
    claim_drift_count = len(drifted_claims)

    delivery = read_json(
        project / "results" / "delivery_check.json", {}
    )
    violations = (
        delivery.get("violations", [])
        if isinstance(delivery, dict) else []
    )
    delivery_violations = violations if isinstance(violations, list) else []

    ledger = build_ledger(project)
    outcomes_doc = read_json(
        project / "results" / "opportunity_outcomes.json", {}
    )
    outcomes = (
        outcomes_doc.get("outcomes", [])
        if isinstance(outcomes_doc, dict) else []
    )
    if not isinstance(outcomes, list):
        outcomes = []
    discharged = {
        item.get("opportunity_id")
        for item in outcomes
        if isinstance(item, dict) and item.get("opportunity_id")
    }
    open_opportunities = {"high": 0, "medium": 0, "low": 0}
    for item in ledger:
        if item["id"] in discharged:
            continue
        severity = str(item.get("severity", "medium")).casefold()
        open_opportunities[
            severity if severity in open_opportunities else "medium"
        ] += 1
    discharge_mix = {
        action: sum(
            1 for item in outcomes
            if isinstance(item, dict) and item.get("action") == action
        )
        for action in (
            "expanded_search",
            "dominance_proof",
            "budget_qualified_stop",
            "strength_downgrade",
            "deferred",
        )
    }
    expanded = [
        item for item in outcomes
        if isinstance(item, dict)
        and item.get("action") == "expanded_search"
    ]
    opportunity_hit_rate = (
        sum(_favorable_outcome(item) for item in expanded) / len(expanded)
        if expanded else None
    )

    binding_items = bindings.values()
    has_report_claim = any(
        isinstance(item, dict) and item.get("scenario_set") == "report"
        for item in binding_items
    )
    scenario_present = (
        project / "results" / "scenario_sets.json"
    ).is_file()
    holdout_separation_ok = (
        not audit_holdout(project)
        if scenario_present or has_report_claim else None
    )
    quality = {
        "requirement_coverage_rate": requirement_rate,
        "mandatory_open": sorted(mandatory_open),
        "claim_closure_rate": claim_closure_rate,
        "claim_drift_count": claim_drift_count,
        "delivery_violations": delivery_violations,
        "open_opportunities": open_opportunities,
        "discharge_mix": discharge_mix,
        "opportunity_hit_rate": opportunity_hit_rate,
        "holdout_separation_ok": holdout_separation_ok,
    }
    profile = read_json(
        project / "config" / "delivery_profile.json", {}
    )
    if not isinstance(profile, dict):
        profile = {}
    quality["quality_dimensions"] = _dimension_scores(profile, quality)
    return quality


def score_project(project: Path) -> dict:
    project = project.resolve()
    evidence = EvidenceGraph(project)
    workflow = WorkflowEngine(project)
    stages = StageService(project)
    tasks = workflow.list_tasks()
    nodes = evidence.nodes
    problem = ProblemGraph(project)
    states = problem.states(nodes, tasks) if problem.exists else {}
    state_counts = {
        state: sum(1 for value in states.values() if value == state)
        for state in sorted(set(states.values()))
    }
    active_problem_nodes = sum(
        1 for node in problem.nodes.values()
        if not node.get("superseded", False)
    ) if problem.exists else 0
    verified_problem_nodes = state_counts.get("verified", 0)
    task_counts = {
        status: sum(1 for task in tasks if task["status"] == status)
        for status in sorted({task["status"] for task in tasks})
    }
    completed = [task for task in tasks if task["status"] == "completed"]
    acceptance_passed = sum(
        1 for task in completed
        if task.get("result", {}).get("acceptance", {}).get("ok") is True
    )
    attempted = [task for task in tasks if int(task.get("attempt", 0)) > 0]
    retried = [task for task in tasks if int(task.get("attempt", 0)) > 1]
    evidence_counts = {
        status: sum(1 for node in nodes.values() if node["status"] == status)
        for status in ("candidate", "verified", "rejected", "revoked", "invalidated")
    }
    prefix = stages.valid_prefix()
    integration_errors = audit_integration(project) if problem.exists else []
    pack_errors = MethodPackRegistry(project).audit() if problem.exists else []
    tool_catalog_errors = (
        ToolRegistry(project).audit_catalog() if problem.exists else []
    )
    tool_report = {
        "decisions_required": 0,
        "decisions_recorded": 0,
        "use": 0,
        "skip": 0,
        "verified_runs": 0,
        "failed_runs": 0,
        "recovery_pending_runs": 0,
        "decision_errors": [],
        "catalog_errors": tool_catalog_errors,
    }
    if problem.exists:
        service = ToolchainService(project)
        packs = MethodPackRegistry(project)
        for node_id, node in problem.nodes.items():
            if node.get("superseded", False):
                continue
            policy = validate_tool_policy(
                packs.match(
                    node["task_type"], node.get("method_pack")
                ).get("tool_policy")
            )
            if not policy["decision_required"]:
                continue
            tool_report["decisions_required"] += 1
            decision = service.decision(node_id)
            if decision:
                tool_report["decisions_recorded"] += 1
                action = decision.get("action")
                if action in {"use", "skip"}:
                    tool_report[action] += 1
                tool_report["decision_errors"].extend(
                    service.audit_decision(node_id, required=True)
                )
        runs = service.executor.list()
        tool_report["verified_runs"] = sum(
            1 for run in runs
            if run.get("verification", {}).get("status") == "verified"
        )
        tool_report["failed_runs"] = sum(
            1 for run in runs
            if run.get("verification", {}).get("status") == "failed"
        )
        tool_report["recovery_pending_runs"] = sum(
            1 for run in runs
            if run.get("execution_status") == "recovery_pending"
        )
        tool_report["decision_rate"] = _rate(
            tool_report["decisions_recorded"],
            tool_report["decisions_required"],
        )
    integrity_ok = (
        not evidence.audit()
        and not integration_errors
        and not pack_errors
        and not tool_catalog_errors
    )
    return {
        "schema": 3,
        "project": str(project),
        "problem_graph": {
            "available": problem.exists,
            "active_nodes": active_problem_nodes,
            "verified_nodes": verified_problem_nodes,
            "closure_rate": _rate(
                verified_problem_nodes, active_problem_nodes
            ),
            "states": state_counts,
        },
        "workflow": {
            "tasks": len(tasks),
            "status_counts": task_counts,
            "attempted": len(attempted),
            "retried": len(retried),
            "retry_rate": _rate(len(retried), len(attempted)),
            "accepted_completed": acceptance_passed,
            "acceptance_rate": _rate(
                acceptance_passed, len(completed)
            ),
        },
        "evidence": {
            **evidence_counts,
            "audit_errors": evidence.audit(),
        },
        "toolchain": tool_report,
        "milestones": {
            "valid_prefix": prefix,
            "depth": len(prefix),
            "completion_rate": len(prefix) / len(STAGES),
        },
        "integrity": {
            "integration_errors": integration_errors,
            "method_pack_errors": pack_errors,
            "tool_catalog_errors": tool_catalog_errors,
            "ok": integrity_ok,
        },
        "answer_quality": _answer_quality(project),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="机械评估局部研究、工具调用与端到端闭合质量"
    )
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = score_project(project_root(args.project))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return int(not report["integrity"]["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
