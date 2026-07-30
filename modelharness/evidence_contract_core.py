"""Problem-contract-aware facade over the evidence kernel."""
from __future__ import annotations

from .contracts import validate_review
from .evidence_core import KINDS, STATUSES, EvidenceGraph as _EvidenceGraph
from .review_store import resolve_review
from .storage import read_json
from .util import sha256


class EvidenceGraph(_EvidenceGraph):
    def _problem_binding(self, node_id: str) -> tuple[str | None, list[str]]:
        from .problem_graph import ProblemGraph

        graph = ProblemGraph(self.root)
        if not graph.exists:
            return None, []
        matches = [
            (problem_id, node)
            for problem_id, node in graph.nodes.items()
            if not node.get("superseded", False)
            and any(
                output["evidence_id"] == node_id
                for output in node["outputs"]
            )
        ]
        if not matches:
            return None, []
        if len(matches) != 1:
            raise ValueError(f"证据义务被多个问题节点声明: {node_id}")
        problem_id, node = matches[0]
        return (
            graph.contract_hash(problem_id),
            [review["path"] for review in node.get("reviews", [])],
        )

    def add(
        self, node_id, kind, statement, artifact, depends=None, check=None,
        *, checks=None, obligation_hash=None, reviews=None,
        producer_task_id=None,
    ):
        inferred_hash, inferred_reviews = self._problem_binding(node_id)
        return super().add(
            node_id, kind, statement, artifact, depends, check,
            checks=checks,
            obligation_hash=obligation_hash or inferred_hash,
            reviews=reviews if reviews is not None else inferred_reviews,
            producer_task_id=producer_task_id,
        )

    def revise(
        self, node_id, *, statement=None, artifact=None, depends=None,
        checks=None, obligation_hash=None, reviews=None, reason,
    ):
        inferred_hash, inferred_reviews = self._problem_binding(node_id)
        return super().revise(
            node_id,
            statement=statement,
            artifact=artifact,
            depends=depends,
            checks=checks,
            obligation_hash=obligation_hash or inferred_hash,
            reviews=reviews if reviews is not None else inferred_reviews,
            reason=reason,
        )

    def _review_path(
        self, node_id: str, node: dict, relative: str
    ):
        contract = node.get("obligation_hash")
        path = self.root / node["artifact"]
        expected = {
            node["artifact"]: sha256(path) if path.is_file() else None
        }
        resolution = resolve_review(
            self.root,
            relative,
            contract_hash=contract,
            artifact_hashes=expected,
        )
        if resolution:
            return self.root / resolution["path"]
        return super()._review_path(node_id, node, relative)

    def _review_records(self, node_id: str, node: dict):
        records, errors = super()._review_records(node_id, node)
        if not node.get("obligation_hash"):
            return records, errors
        for relative in node.get("reviews", []):
            path = self._review_path(node_id, node, relative)
            if not path.is_file():
                continue
            try:
                review = validate_review(read_json(path), path)
            except ValueError:
                continue
            if review.get("contract_hash") != node["obligation_hash"]:
                errors.append(f"V3 审核未绑定当前合同: {relative}")
            if node_id not in review.get("evidence_checked", []):
                errors.append(f"V3 审核未声明检查证据 {node_id}: {relative}")
            if not review.get("reviewer"):
                errors.append(f"V3 审核缺少 reviewer: {relative}")
            if not review.get("task_id"):
                errors.append(f"V3 审核缺少 task_id: {relative}")
        return records, sorted(set(errors))
