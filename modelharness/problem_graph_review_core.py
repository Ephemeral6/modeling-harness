"""Problem Graph facade with review-aware repair state transitions."""
from __future__ import annotations

from .problem_graph_core import (
    ACTIVE_TASK_STATES,
    NODE_STATES,
    ProblemGraph as _ProblemGraph,
    canonical_hash,
    node_contract,
    node_contract_hash,
    validate_problem_graph,
)
from .storage import json_transaction, read_json
from .util import sha256


class ProblemGraph(_ProblemGraph):
    def state(self, node_id: str, evidence_nodes: dict, tasks: list[dict]) -> str:
        node = self.nodes[node_id]
        if node.get("superseded", False):
            return "superseded"
        if self.completion(node_id, evidence_nodes):
            return "verified"
        if any(
            not self.completion(dep, evidence_nodes)
            for dep in node.get("depends_on", [])
            if not self.nodes[dep].get("superseded", False)
        ):
            return "blocked"
        if any(
            evidence_nodes.get(item, {}).get("status") != "verified"
            for item in node.get("input_evidence", [])
        ):
            return "blocked"
        related = [x for x in tasks if x.get("work_item_id") == node_id]
        if any(x.get("status") in ACTIVE_TASK_STATES for x in related):
            return "active"
        outputs = [
            evidence_nodes.get(output["evidence_id"])
            for output in node["outputs"]
        ]
        if any(x and x.get("status") == "rejected" for x in outputs):
            return "repair"
        current_hashes = {
            output["artifact"]: (
                sha256(self.root / output["artifact"])
                if (self.root / output["artifact"]).is_file() else None
            )
            for output in node["outputs"]
        }
        for review_spec in node.get("reviews", []):
            path = self.root / review_spec["path"]
            if not path.is_file():
                continue
            review = read_json(path)
            if str(review.get("verdict", "")).upper() != "REJECT":
                continue
            reviewed_hashes = review.get("artifact_hashes", {})
            # A rejection applies only to the exact artifacts it reviewed.
            if not reviewed_hashes or all(
                current_hashes.get(relative) == digest
                for relative, digest in reviewed_hashes.items()
            ):
                return "repair"
        if outputs and all(x is not None for x in outputs):
            return "review"
        return "ready"

    def replace(self, proposal: dict, reason: str) -> dict:
        if not reason.strip():
            raise ValueError("问题图修订原因不能为空")
        validated = validate_problem_graph(proposal)
        with json_transaction(
            self.path, {"schema": 1, "revision": 0, "nodes": {}}
        ) as current:
            old_revision = int(current.get("revision", 0))
            old_nodes = current.get("nodes", {})
            old_hashes = {
                key: node_contract_hash(value)
                for key, value in old_nodes.items()
            }
            new_nodes = validated["nodes"]
            new_hashes = {
                key: node_contract_hash(value)
                for key, value in new_nodes.items()
            }
            changed = sorted({
                *[
                    key for key in old_hashes
                    if old_hashes.get(key) != new_hashes.get(key)
                ],
                *[
                    key for key in new_hashes
                    if old_hashes.get(key) != new_hashes.get(key)
                ],
            })
            impacted = sorted({
                output["evidence_id"]
                for key in changed
                for output in old_nodes.get(key, {}).get("outputs", [])
            })
            current.clear()
            current.update(validated)
            current["revision"] = old_revision + 1
            current["last_revision_reason"] = reason.strip()
        return {
            "changed_nodes": changed,
            "impacted_evidence": impacted,
            "revision": self.data["revision"],
        }
