"""Delivery Profile facade with task/evidence invalidation."""
from __future__ import annotations

from .profiles_core import validate_profile
from .profiles_overlay_core import (
    PROFILE_OUTPUTS,
    ProfileService as _ProfileService,
)


class ProfileService(_ProfileService):
    def _apply_problem_overlay(self, profile: dict) -> dict | None:
        result = super()._apply_problem_overlay(profile)
        if not result:
            return None
        from .evidence import EvidenceGraph
        from .problem_graph import ProblemGraph
        from .workflow import WorkflowEngine

        graph = ProblemGraph(self.root)
        workflow = WorkflowEngine(self.root)
        for node_id in result["changed_nodes"]:
            if node_id in graph.nodes:
                workflow.supersede(node_id, graph.contract_hash(node_id))
            else:
                workflow.supersede(node_id)
        evidence = EvidenceGraph(self.root)
        revoked = []
        for evidence_id in result["impacted_evidence"]:
            node = evidence.nodes.get(evidence_id)
            if node and node["status"] != "revoked":
                revoked.extend(evidence.revoke(
                    evidence_id,
                    f"delivery profile changed: {profile['name']}",
                ))
        result["revoked_evidence"] = sorted(set(revoked))
        return result
