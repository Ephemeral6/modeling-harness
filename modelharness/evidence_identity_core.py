"""Evidence facade enforcing durable independent-review identity."""
from __future__ import annotations

from .contracts import validate_review
from .evidence_review_core import (
    KINDS,
    STATUSES,
    EvidenceGraph as _EvidenceGraph,
)
from .storage import read_json


class EvidenceGraph(_EvidenceGraph):
    def _review_records(self, node_id: str, node: dict):
        records, errors = super()._review_records(node_id, node)
        if not node.get("obligation_hash"):
            return records, errors
        from .workflow import WorkflowEngine, owners_overlap

        workflow = WorkflowEngine(self.root)
        producer = None
        producer_task_id = node.get("producer_task_id")
        if producer_task_id:
            try:
                producer = workflow.get_task(producer_task_id)
            except ValueError:
                errors.append(
                    f"producer task does not exist: {producer_task_id}"
                )
            if producer:
                if producer.get("task_type") == "independent_review":
                    errors.append(
                        "an independent-review task cannot be the producer"
                    )
                if producer.get("status") != "completed":
                    errors.append(
                        f"producer task is not completed: {producer_task_id}"
                    )
                if (
                    producer.get("contract_hash")
                    and producer.get("contract_hash")
                    != node["obligation_hash"]
                ):
                    errors.append("producer task contract mismatch")
        else:
            producing_tasks = [
                task
                for task in workflow.list_tasks()
                if task.get("task_type") != "independent_review"
                and any(
                    owners_overlap(node["artifact"], scope)
                    for scope in task.get("owns", [])
                )
            ]
            if producing_tasks:
                errors.append(
                    "producer_task_id is required because a workflow task "
                    "owns this evidence artifact"
                )
        for relative in node.get("reviews", []):
            path = self.root / relative
            if not path.is_file():
                continue
            try:
                review = validate_review(read_json(path), path)
                task_id = review.get("task_id")
                task = workflow.get_task(task_id) if task_id else None
            except (ValueError, TypeError):
                task = None
            if task is None:
                errors.append(f"review task_id does not exist: {relative}")
                continue
            if task.get("status") != "completed":
                errors.append(f"review task is not completed: {relative}")
            if task.get("task_type") != "independent_review":
                errors.append(f"review task type is invalid: {relative}")
            if task.get("contract_hash") != node["obligation_hash"]:
                errors.append(f"review task contract mismatch: {relative}")
            if producer:
                if task["id"] == producer["id"]:
                    errors.append(
                        f"producer cannot approve its own evidence: {relative}"
                    )
                producer_worker = producer.get("worker")
                reviewer_worker = task.get("worker")
                if (
                    producer_worker
                    and reviewer_worker
                    and producer_worker == reviewer_worker
                ):
                    errors.append(
                        f"producer and reviewer worker must differ: {relative}"
                    )
        return records, sorted(set(errors))
