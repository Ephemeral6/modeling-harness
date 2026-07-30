"""Cross-node consistency audit for problem, evidence and tool interfaces."""
from __future__ import annotations

from pathlib import Path

from .evidence import EvidenceGraph
from .method_packs import MethodPackRegistry
from .problem_graph import ProblemGraph
from .toolchain import ToolchainService, validate_tool_policy
from .workflow import WorkflowEngine


def audit_integration(root: Path) -> list[str]:
    graph = ProblemGraph(root)
    evidence_graph = EvidenceGraph(root)
    evidence = evidence_graph.nodes
    tasks = WorkflowEngine(root).list_tasks()
    errors = []
    declared_outputs = {
        output["evidence_id"]
        for node in graph.nodes.values()
        if not node.get("superseded", False)
        for output in node["outputs"]
    }
    packs = MethodPackRegistry(root)
    tools = ToolchainService(root)
    for node_id, node in graph.nodes.items():
        if node.get("superseded", False):
            continue
        contract = graph.contract_hash(node_id)
        for output in node["outputs"]:
            evidence_id = output["evidence_id"]
            record = evidence.get(evidence_id)
            if not record:
                continue
            if record.get("artifact") != output["artifact"]:
                errors.append(
                    f"{node_id}/{evidence_id}: 证据产物路径与问题合同不一致"
                )
            if node.get("enforce_contract", True):
                if record.get("obligation_hash") != contract:
                    errors.append(
                        f"{node_id}/{evidence_id}: obligation_hash 不匹配"
                    )
        for input_id in node.get("input_evidence", []):
            if input_id not in evidence and input_id not in declared_outputs:
                errors.append(
                    f"{node_id}: 输入证据既不存在也无生产义务: {input_id}"
                )
        if graph.completion(node_id, evidence):
            pack = packs.match(node["task_type"], node.get("method_pack"))
            policy = validate_tool_policy(pack.get("tool_policy"))
            if policy["decision_required"]:
                errors.extend(tools.audit_decision(node_id, required=True))
    return errors
