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
        from .workflow import WorkflowEngine

        workflow = WorkflowEngine(self.root)
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
                errors.append(f"V3 审核 task_id 不存在: {relative}")
                continue
            if task.get("status") != "completed":
                errors.append(f"V3 审核任务尚未完成: {relative}")
            if task.get("task_type") != "independent_review":
                errors.append(f"V3 审核任务类型非法: {relative}")
            if task.get("contract_hash") != node["obligation_hash"]:
                errors.append(f"V3 审核任务合同不匹配: {relative}")
        return records, sorted(set(errors))
