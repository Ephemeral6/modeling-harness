"""Final Evidence facade: graph-required reviews cannot be removed by callers."""
from __future__ import annotations

from .evidence_contract_core import (
    KINDS,
    STATUSES,
    EvidenceGraph as _EvidenceGraph,
)


class EvidenceGraph(_EvidenceGraph):
    def add(
        self, node_id, kind, statement, artifact, depends=None, check=None,
        *, checks=None, obligation_hash=None, reviews=None,
        producer_task_id=None,
    ):
        _contract, required_reviews = self._problem_binding(node_id)
        combined_reviews = list(dict.fromkeys(
            required_reviews + list(reviews or [])
        ))
        return super().add(
            node_id, kind, statement, artifact, depends, check,
            checks=checks,
            obligation_hash=obligation_hash,
            reviews=combined_reviews,
            producer_task_id=producer_task_id,
        )

    def revise(
        self, node_id, *, statement=None, artifact=None, depends=None,
        checks=None, obligation_hash=None, reviews=None, reason,
    ):
        _contract, required_reviews = self._problem_binding(node_id)
        combined_reviews = list(dict.fromkeys(
            required_reviews + list(reviews or [])
        ))
        return super().revise(
            node_id,
            statement=statement,
            artifact=artifact,
            depends=depends,
            checks=checks,
            obligation_hash=obligation_hash,
            reviews=combined_reviews,
            reason=reason,
        )
