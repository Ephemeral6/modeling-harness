"""Adaptive scheduler facade rejecting stale review approvals."""
from __future__ import annotations

from .contracts import validate_review
from .scheduler_core import (
    AdaptiveScheduler as _AdaptiveScheduler,
    _artifact_signature,
)
from .storage import read_json
from .util import sha256


class AdaptiveScheduler(_AdaptiveScheduler):
    def _review_tasks(self, item: dict) -> list[dict]:
        node_id, node = item["id"], item["node"]
        contract = item["contract_hash"]
        artifact_signature = _artifact_signature(self.project, node)
        current_hashes = {
            output["artifact"]: (
                sha256(self.project / output["artifact"])
                if (self.project / output["artifact"]).is_file() else None
            )
            for output in node["outputs"]
        }
        created = []
        for review in node.get("reviews", []):
            path = self.project / review["path"]
            approved = False
            if path.is_file():
                try:
                    record = validate_review(read_json(path), path)
                    reviewed = record.get("artifact_hashes", {})
                    approved = (
                        record["verdict"].upper() == "APPROVE"
                        and record.get("contract_hash") == contract
                        and all(
                            reviewed.get(relative) == digest
                            for relative, digest in current_hashes.items()
                        )
                    )
                except ValueError:
                    approved = False
            if approved:
                continue
            try:
                task = self.workflow.ensure_task(
                    f"review:{node_id}:{review['path']}:{artifact_signature}",
                    node["milestone"],
                    review.get("role", "independent-reviewer"),
                    (
                        f"冷启动审核局部问题 {node_id}：{node['question']}。"
                        f"只读取正式输入、产物和检查；输出审核必须绑定 "
                        f"contract_hash={contract}、task_id 和全部当前产物哈希。"
                    ),
                    [review["path"]],
                    inputs=self._input_artifacts(node) + [
                        x["artifact"] for x in node["outputs"]
                    ],
                    acceptance=[{
                        "kind": "artifact_exists", "path": review["path"],
                    }],
                    budget={"max_attempts": 3},
                    work_item_id=node_id,
                    task_type="independent_review",
                    contract_hash=contract,
                    generation=1 + len(self.workflow.list_tasks(
                        work_item_id=node_id
                    )),
                )
            except ValueError as exc:
                task = {
                    "work_item_id": node_id,
                    "role": review.get("role", "independent-reviewer"),
                    "status": "conflict",
                    "error": str(exc),
                }
            created.append(task)
        return created
