"""Evidence facade enforcing identity and current review artifact hashes."""
from __future__ import annotations

from .contracts import validate_review
from .evidence_identity_core import (
    KINDS,
    STATUSES,
    EvidenceGraph as _EvidenceGraph,
)
from .storage import read_json
from .util import sha256


class EvidenceGraph(_EvidenceGraph):
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
            expected_path = node["artifact"]
            expected_hash = sha256(self.root / expected_path)
            reviewed_hash = review.get(
                "artifact_hashes", {}
            ).get(expected_path)
            if reviewed_hash is None:
                errors.append(
                    f"V3 审核未记录当前证据产物哈希: "
                    f"{relative}/{expected_path}"
                )
            elif reviewed_hash != expected_hash:
                errors.append(
                    f"V3 审核对应旧版产物: {relative}/{expected_path}"
                )
        return records, sorted(set(errors))
