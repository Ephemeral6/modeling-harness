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


# profile.mandatory_outputs 的权威解析表。
#
# 4.6 之前 mandatory_outputs 写的是语义名（``algorithm_process``），而问题图
# 声明的是 evidence id（``narrative.algorithm_process``），验收只能靠“取 id
# 最后一段”去猜——猜中的成了死锁，猜不中的逼 agent 凭空注册 harness 从不索要
# 的证据。这里把每个语义名显式钉到问题图（seed + overlay）真实声明的输出上：
# 名单只有一份，解析不再有猜测环节。
#
# 4.7 起 cumcm / mcm_icm 的 mandatory_outputs 直接写 evidence id；本表继续保留
# 旧语义名，保证 4.6 及更早落盘的 config/delivery_profile.json 仍能解析。
MANDATORY_OUTPUT_TARGETS: dict[str, dict[str, list[str]]] = {
    "general": {
        "problem": ["problem.statement"],
        "model": ["model.spec"],
        "result": ["result.nominal"],
        "decision": ["decision.answer"],
        # 边界与回退模型登记在假设证据里，seed 图没有 limitation kind 的输出。
        "limitation": ["model.assumptions"],
    },
    "cumcm": {
        "subquestion_results": ["result.subquestions"],
        "algorithm_process": ["narrative.algorithm_process"],
        # 可复现代码就是 s3 声明的唯一正式求值器，不是第二份交付物。
        "reproducible_code": ["code.solver"],
        # 误差分析落在 s4 的 UQ 证据上。
        "error_analysis": ["result.uq"],
    },
    "mcm_icm": {
        "executive_summary": ["narrative.summary"],
        "model_rationale": ["model.spec"],
        "sensitivity": ["result.robustness"],
        "policy_implications": ["decision.answer"],
    },
    "real_world": {
        "stakeholder_objective": ["problem.success"],
        "data_governance": ["data.ledger"],
        "decision_policy": ["decision.packet"],
        "monitoring_plan": ["narrative.monitoring"],
        # 重做触发器与监控计划同属一份上线监控证据。
        "rebuild_triggers": ["narrative.monitoring"],
    },
}


def profile_output_ids(name: str) -> list[str]:
    """Evidence ids the profile overlay declares on the delivery node."""
    return [
        output["evidence_id"]
        for output in PROFILE_OUTPUTS.get(str(name), [])
    ]


def resolve_mandatory_output(profile: str, output: str) -> list[str]:
    """Map one mandatory-output name to the evidence ids that satisfy it."""
    name = str(output).strip()
    targets = MANDATORY_OUTPUT_TARGETS.get(str(profile), {}).get(name)
    if targets:
        return list(dict.fromkeys(targets))
    # 名单本身写 evidence id 时（4.7 起的出厂 profile）解析即恒等。
    return [name] if name else []


def resolve_mandatory_outputs(
    profile: str, outputs: list | tuple | None
) -> dict[str, list[str]]:
    """Resolve a whole mandatory_outputs list into evidence ids."""
    return {
        str(output): resolve_mandatory_output(profile, output)
        for output in (outputs or [])
        if isinstance(output, str)
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
