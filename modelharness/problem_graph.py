"""Final Problem Graph facade with method-pack content in node contracts."""
from __future__ import annotations

from .problem_graph_core import (
    ACTIVE_TASK_STATES,
    NODE_STATES,
    canonical_hash,
    node_contract,
    node_contract_hash,
    validate_problem_graph,
)
from .problem_graph_review_core import ProblemGraph as _ProblemGraph
from .storage import read_json
from .util import sha256


class ProblemGraph(_ProblemGraph):
    def contract_hash(self, node_id: str) -> str:
        node = self.nodes[node_id]
        contract = node_contract(node)
        pack_name = node.get("method_pack")
        matches = []
        directory = self.root / "config" / "method_packs"
        if directory.is_dir():
            for path in directory.glob("*.json"):
                if read_json(path, {}).get("name") == pack_name:
                    matches.append(path)
        if len(matches) != 1:
            raise ValueError(
                f"问题节点方法包不存在或重名: {node_id}/{pack_name}"
            )
        contract["method_pack_sha256"] = sha256(matches[0])
        return canonical_hash(contract)
