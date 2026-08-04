"""Profile-aware evidence delivery facade."""
from __future__ import annotations

from .evidence import EvidenceGraph
from .narrative_core import CLAIM_RE
from .narrative_core import audit_final as _audit_final
from .narrative_core import audit_paper as _audit_paper
from .narrative_core import build_brief as _build_brief
from .problem_graph import ProblemGraph
from .profiles import ProfileService
from .storage import atomic_write_json


def build_brief(root):
    output = _build_brief(root)
    profile = ProfileService(root).active
    graph = ProblemGraph(root)
    evidence = EvidenceGraph(root).nodes
    delivery_nodes = [
        {
            "work_item_id": node_id,
            "question": node["question"],
            "outputs": [
                {
                    **item,
                    "status": evidence.get(
                        item["evidence_id"], {}
                    ).get("status"),
                }
                for item in node["outputs"]
            ],
        }
        for node_id, node in graph.nodes.items()
        if node["milestone"] == "s6" and not node.get("superseded", False)
    ] if graph.exists else []
    atomic_write_json(root / "paper" / "delivery_manifest.json", {
        "schema": 1,
        "profile": profile["name"],
        "renderer": profile["renderer"],
        "report_sections": profile.get("report_sections", []),
        "quality_dimensions": profile.get("quality_dimensions", []),
        "paper_delivery": profile.get("paper_delivery"),
        "delivery_nodes": delivery_nodes,
    })
    return output


def audit_paper(root, paper="paper/draft.md"):
    errors = _audit_paper(root, paper)
    graph = ProblemGraph(root)
    if graph.exists:
        evidence = EvidenceGraph(root).nodes
        for evidence_id, contract, enforce in graph.milestone_outputs("s6"):
            if evidence_id == "narrative.worksheet":
                continue
            record = evidence.get(evidence_id)
            if not record or record.get("status") != "verified":
                errors.append(f"交付 Profile 缺少 verified 证据: {evidence_id}")
            elif enforce and record.get("obligation_hash") != contract:
                errors.append(f"交付证据合同已陈旧: {evidence_id}")
    return sorted(set(errors))


def audit_final(root, paper="paper/final.md"):
    return _audit_final(root, paper)
