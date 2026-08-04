"""Derived state capsule and five lightweight ledgers for long research runs."""
from __future__ import annotations

from pathlib import Path

from .evidence import EvidenceGraph
from .opportunities import build_ledger
from .optimization import assess_optimization
from .problem_graph import ProblemGraph, canonical_hash
from .profiles import ProfileService
from .stages import StageService
from .util import now
from .workflow import WorkflowEngine


class StateCapsule:
    """Build state on demand; do not introduce another authoritative database."""

    def __init__(self, project: Path):
        self.project = project.resolve()

    def build(self) -> dict:
        workflow = WorkflowEngine(self.project)
        evidence = EvidenceGraph(self.project)
        graph = ProblemGraph(self.project)
        tasks = workflow.list_tasks()
        evidence_data = evidence.data
        stage = StageService(self.project).current()
        states = (
            graph.states(evidence_data["nodes"], tasks)
            if graph.exists else {}
        )
        task_ledger = [
            {
                "id": task["id"],
                "work_item_id": task.get("work_item_id"),
                "status": task["status"],
                "execution_status": task.get("execution_status"),
                "verdict": task.get("verdict"),
            }
            for task in tasks
            if task["status"] not in {
                "completed", "superseded", "invalidated"
            }
        ]
        progress_ledger = {
            "verified_evidence": [
                node_id for node_id, node in evidence_data["nodes"].items()
                if node.get("status") == "verified"
                and evidence.freshness(node_id, evidence_data) == "valid"
            ],
            "verified_nodes": [
                node_id for node_id, state in states.items()
                if state == "verified"
            ],
            "valid_stage_prefix": StageService(
                self.project
            ).valid_prefix(),
        }
        failure_ledger = [
            {
                "id": task["id"],
                "work_item_id": task.get("work_item_id"),
                "status": task["status"],
                "attempt": task.get("attempt", 0),
                "note": task.get("recovery_note"),
            }
            for task in tasks
            if task["status"] in {
                "failed", "recovery_pending", "invalidated"
            }
        ]
        resource_ledger = {
            "task_count": len(tasks),
            "attempts": sum(int(task.get("attempt", 0)) for task in tasks),
            "active": sum(
                task["status"] in WorkflowEngine.ACTIVE_STATES
                for task in tasks
            ),
            "recovery_pending": sum(
                task["status"] == "recovery_pending" for task in tasks
            ),
        }
        opportunity_ledger = []
        if graph.exists and stage is not None:
            for item in graph.frontier(
                evidence_data["nodes"], tasks, stage
            ):
                opportunity_ledger.append({
                    "id": item["id"],
                    "state": item["state"],
                    "priority": item["priority"],
                    "question": item["node"]["question"],
                })
        opportunity_ledger.extend(build_ledger(self.project))
        payload = {
            "profile": ProfileService(self.project).active["name"],
            "stage": stage,
            "problem_revision": graph.data["revision"] if graph.exists else 0,
            "evidence_revision": evidence_data["revision"],
            "node_states": states,
            "freshness": evidence.freshness_report(),
            "ledgers": {
                "task": task_ledger,
                "progress": progress_ledger,
                "failure": failure_ledger,
                "resource": resource_ledger,
                "opportunity": opportunity_ledger,
            },
            "assurance": assess_optimization(self.project),
            "recent_events": workflow.list_events(20),
        }
        return {
            "generated_at": now(),
            "state_hash": canonical_hash(payload),
            **payload,
        }
