"""Delivery profiles with problem-graph output overlays."""
from __future__ import annotations

import copy

from .profiles_core import ProfileService as _ProfileService, validate_profile


PROFILE_OUTPUTS = {
    "general": [],
    "cumcm": [
        {
            "evidence_id": "result.subquestions",
            "kind": "result",
            "statement": "按题目子问组织的机器可读数值结果",
            "artifact": "results/subquestion_results.json",
            "profile_managed": True,
        },
        {
            "evidence_id": "narrative.algorithm_process",
            "kind": "narrative",
            "statement": "算法流程、误差、复杂度和复现入口",
            "artifact": "results/algorithm_process.json",
            "profile_managed": True,
        },
    ],
    "mcm_icm": [
        {
            "evidence_id": "narrative.summary",
            "kind": "narrative",
            "statement": "面向评审与决策者的独立摘要页",
            "artifact": "paper/summary.md",
            "profile_managed": True,
        }
    ],
    "real_world": [
        {
            "evidence_id": "decision.packet",
            "kind": "decision",
            "statement": "面向利益相关者的决策与实施包",
            "artifact": "docs/decision_packet.md",
            "profile_managed": True,
        },
        {
            "evidence_id": "narrative.monitoring",
            "kind": "narrative",
            "statement": "上线监控指标、漂移检测和模型重做触发器",
            "artifact": "docs/monitoring_plan.md",
            "profile_managed": True,
        },
    ],
}


class ProfileService(_ProfileService):
    def _apply_problem_overlay(self, profile: dict) -> dict | None:
        from .problem_graph import ProblemGraph

        graph = ProblemGraph(self.root)
        if not graph.exists or "s6.delivery" not in graph.nodes:
            return None
        proposal = copy.deepcopy(graph.data)
        node = proposal["nodes"]["s6.delivery"]
        managed_paths = {
            output["artifact"]
            for outputs in PROFILE_OUTPUTS.values()
            for output in outputs
        }
        node["outputs"] = [
            output for output in node["outputs"]
            if not output.get("profile_managed", False)
        ] + copy.deepcopy(PROFILE_OUTPUTS.get(profile["name"], []))
        stream = node["workstreams"][0]
        stream["owns"] = [
            path for path in stream["owns"] if path not in managed_paths
        ] + [
            output["artifact"]
            for output in PROFILE_OUTPUTS.get(profile["name"], [])
        ]
        stream["outputs"] = [
            output["evidence_id"] for output in node["outputs"]
        ]
        return graph.replace(
            proposal, f"delivery profile overlay: {profile['name']}"
        )

    def use(self, name: str) -> dict:
        profile = super().use(name)
        self._apply_problem_overlay(profile)
        return profile
