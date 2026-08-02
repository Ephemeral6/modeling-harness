"""Problem Graph facade binding methods, tools, and proposal audit."""
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
        catalog = self.root / "config" / "tools" / "catalog.json"
        autonomy = self.root / "config" / "tool_autonomy.json"
        environments = self.root / "config" / "tool_environments"
        if catalog.is_file() or autonomy.is_file() or environments.is_dir():
            if (
                not catalog.is_file()
                or not autonomy.is_file()
                or not environments.is_dir()
            ):
                raise ValueError(
                    "计算工具目录、自主策略或环境 Profile 不完整"
                )
            profiles = {
                path.name: sha256(path)
                for path in sorted(environments.glob("*.json"))
            }
            if not profiles:
                raise ValueError("计算环境 Profile 为空")
            contract["tool_catalog_sha256"] = sha256(catalog)
            contract["tool_autonomy_sha256"] = sha256(autonomy)
            contract[
                "tool_environment_profiles_sha256"
            ] = canonical_hash(profiles)
        claim_bindings = self.root / "config" / "claim_bindings.json"
        if node.get("milestone") == "s5" and claim_bindings.is_file():
            contract["claim_bindings_sha256"] = sha256(claim_bindings)
        return canonical_hash(contract)

    def replace(self, proposal: dict, reason: str) -> dict:
        # During initial scaffolding there is no runtime to audit yet.
        if (
            (self.root / "modeling-project.json").is_file()
            and (self.root / "config" / "tool_autonomy.json").is_file()
        ):
            from .proposals import ProposalService

            action = ProposalService(self.root).auto(
                "problem_graph.replace",
                reason,
                {"proposed_revision": proposal.get("revision")},
            )
            if action["policy"]["decision"] not in {
                "ALLOW", "ALLOW_WITH_OBLIGATIONS"
            }:
                raise RuntimeError(action["policy"]["reason"])
        return super().replace(proposal, reason)
