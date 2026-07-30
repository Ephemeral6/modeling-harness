"""Mechanical evaluation of research, computation and project traces."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import STAGES
from .evidence import EvidenceGraph
from .integration import audit_integration
from .method_packs import MethodPackRegistry
from .problem_graph import ProblemGraph
from .stages import StageService
from .toolchain import ToolchainService, validate_tool_policy
from .toolchain_registry import ToolRegistry
from .util import project_root
from .workflow import WorkflowEngine


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


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
