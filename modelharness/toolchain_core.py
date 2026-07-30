"""Autonomous tool planning, explicit choice, execution and audit facade."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .method_packs import MethodPackRegistry
from .problem_graph import ProblemGraph, canonical_hash
from .storage import atomic_write_json, read_json
from .toolchain_execution import ToolRunExecutor
from .toolchain_registry import ToolRegistry
from .util import now


def validate_tool_policy(value: Any) -> dict:
    if value is None:
        return {
            "decision_required": False,
            "required_capabilities": [],
            "preferred_capabilities": [],
            "validation_protocols": [],
        }
    if not isinstance(value, dict):
        raise ValueError("method pack tool_policy 必须是对象")
    result = {
        "decision_required": value.get("decision_required", False),
        "required_capabilities": value.get("required_capabilities", []),
        "preferred_capabilities": value.get("preferred_capabilities", []),
        "validation_protocols": value.get("validation_protocols", []),
    }
    if not isinstance(result["decision_required"], bool):
        raise ValueError("tool_policy.decision_required 非法")
    for field in (
        "required_capabilities", "preferred_capabilities",
        "validation_protocols",
    ):
        if not isinstance(result[field], list) or not all(
            isinstance(item, str) and item for item in result[field]
        ):
            raise ValueError(f"tool_policy.{field} 非法")
    return result


class ToolchainService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.registry = ToolRegistry(self.root)
        self.executor = ToolRunExecutor(self.root, self.registry)
        self.graph = ProblemGraph(self.root)
        self.packs = MethodPackRegistry(self.root)
        self.plan_directory = self.root / ".harness" / "tool_plans"
        self.decision_directory = self.root / ".harness" / "tool_decisions"

    def plan_path(self, node_id: str) -> Path:
        return self.plan_directory / f"{node_id}.json"

    def decision_path(self, node_id: str) -> Path:
        return self.decision_directory / f"{node_id}.json"

    def _node_pack(self, node_id: str) -> tuple[dict, dict, str]:
        node = self.graph.nodes[node_id]
        pack = self.packs.match(node["task_type"], node.get("method_pack"))
        return node, pack, self.graph.contract_hash(node_id)

    @staticmethod
    def _greedy_tools(
        capabilities: list[str], candidates: dict[str, list[dict]]
    ) -> list[str]:
        selected = []
        for capability in capabilities:
            options = candidates.get(capability, [])
            if options and options[0]["id"] not in selected:
                selected.append(options[0]["id"])
        return selected

    def recommend(
        self,
        node_id: str,
        *,
        extra_capabilities: list[str] | None = None,
        persist: bool = True,
    ) -> dict:
        node, pack, contract = self._node_pack(node_id)
        policy = validate_tool_policy(pack.get("tool_policy"))
        autonomy = self.registry.autonomy
        capabilities = list(dict.fromkeys(
            policy["required_capabilities"]
            + policy["preferred_capabilities"]
            + list(extra_capabilities or [])
        ))
        candidates = self.registry.candidates(
            capabilities,
            node["task_type"],
            allowed_risks=set(autonomy["autonomous_risks"]),
        )
        required_missing = [
            capability for capability in policy["required_capabilities"]
            if not candidates.get(capability)
        ]
        suggested = self._greedy_tools(capabilities, candidates)
        record = {
            "schema": 1,
            "node_id": node_id,
            "contract_hash": contract,
            "time": now(),
            "mode": autonomy["mode"],
            "decision_required": policy["decision_required"],
            "required_capabilities": policy["required_capabilities"],
            "preferred_capabilities": policy["preferred_capabilities"],
            "requested_extra_capabilities": list(extra_capabilities or []),
            "validation_protocols": policy["validation_protocols"],
            "candidates": candidates,
            "suggested_tools": suggested,
            "required_missing": required_missing,
            "recommendation": (
                "blocked" if required_missing
                else "use" if suggested
                else "skip"
            ),
            "catalog_hash": self.registry.catalog_hash(),
        }
        record["plan_hash"] = canonical_hash({
            key: record[key] for key in (
                "node_id", "contract_hash", "mode", "decision_required",
                "required_capabilities", "preferred_capabilities",
                "requested_extra_capabilities", "validation_protocols",
                "candidates", "suggested_tools", "required_missing",
                "recommendation", "catalog_hash",
            )
        })
        if persist:
            atomic_write_json(self.plan_path(node_id), record)
        return record

    def decide(
        self,
        node_id: str,
        action: str,
        reason: str,
        *,
        tools: list[str] | None = None,
        extra_capabilities: list[str] | None = None,
    ) -> dict:
        if not reason.strip():
            raise ValueError("工具选择或跳过理由不能为空")
        if action not in {"auto", "use", "skip"}:
            raise ValueError("tool decision.action 必须为 auto/use/skip")
        plan = self.recommend(
            node_id, extra_capabilities=extra_capabilities, persist=True
        )
        autonomy = self.registry.autonomy
        if autonomy["mode"] == "disabled" and action != "skip":
            raise RuntimeError("当前项目禁用自主计算工具")
        if action == "auto":
            if plan["required_missing"]:
                raise RuntimeError(
                    f"必需计算能力不可用: {plan['required_missing']}"
                )
            action = "use" if plan["suggested_tools"] else "skip"
        if action == "skip" and not autonomy["allow_skip"]:
            raise RuntimeError("当前工具自主策略不允许跳过")
        selected = list(dict.fromkeys(tools or plan["suggested_tools"]))
        if action == "skip":
            selected = []
        if action == "use" and not selected:
            raise ValueError("选择 use 时至少需要一个工具")
        allowed_risks = set(autonomy["autonomous_risks"])
        selected_records = []
        for tool_id in selected:
            tool = self.registry.get(tool_id)
            probe = self.registry.probe(tool_id)
            if not probe["available"]:
                raise RuntimeError(f"所选计算工具不可用: {tool_id}")
            if tool.get("risk", "local") not in allowed_risks:
                raise RuntimeError(
                    f"工具 {tool_id} 风险为 {tool.get('risk')}，需要新授权"
                )
            selected_records.append({
                "id": tool_id,
                "version": probe["version"],
                "risk": tool.get("risk", "local"),
                "warnings": probe["warnings"],
            })
        previous = read_json(self.decision_path(node_id))
        revision = (
            int(previous.get("revision", 0)) + 1
            if isinstance(previous, dict) else 1
        )
        history = (
            list(previous.get("history", [])) if isinstance(previous, dict) else []
        )
        if isinstance(previous, dict):
            history.append({
                "revision": previous.get("revision"),
                "decision_hash": previous.get("decision_hash"),
                "action": previous.get("action"),
                "time": previous.get("time"),
            })
        record = {
            "schema": 1,
            "node_id": node_id,
            "contract_hash": plan["contract_hash"],
            "plan_hash": plan["plan_hash"],
            "catalog_hash": plan["catalog_hash"],
            "revision": revision,
            "time": now(),
            "action": action,
            "reason": reason.strip(),
            "selected_tools": selected_records,
            "required_capabilities": plan["required_capabilities"],
            "extra_capabilities": list(extra_capabilities or []),
            "validation_protocols": plan["validation_protocols"],
            "history": history[-20:],
        }
        record["decision_hash"] = canonical_hash({
            key: record[key] for key in (
                "node_id", "contract_hash", "plan_hash", "catalog_hash",
                "revision", "action", "reason", "selected_tools",
                "required_capabilities", "extra_capabilities",
                "validation_protocols",
            )
        })
        atomic_write_json(self.decision_path(node_id), record)
        return record

    def decision(self, node_id: str) -> dict | None:
        value = read_json(self.decision_path(node_id))
        return value if isinstance(value, dict) else None

    def run(
        self,
        node_id: str,
        *,
        argv: list[str],
        inputs: list[str],
        outputs: list[str],
        validators: list[dict] | None,
        tool_ids: list[str] | None,
        seed: int,
        timeout: int,
    ) -> dict:
        decision = self.decision(node_id)
        if not decision or decision.get("action") != "use":
            raise RuntimeError("必须先为该问题节点登记 use 工具决策")
        contract = self.graph.contract_hash(node_id)
        if decision.get("contract_hash") != contract:
            raise RuntimeError("工具决策已因问题合同变化而过期")
        selected = [item["id"] for item in decision["selected_tools"]]
        requested = list(dict.fromkeys(tool_ids or selected))
        if any(tool_id not in selected for tool_id in requested):
            raise RuntimeError("tool run 只能使用当前决策中已选择的工具")
        return self.executor.run(
            node_id=node_id,
            contract_hash=contract,
            decision_hash=decision["decision_hash"],
            tool_ids=requested,
            argv=argv,
            inputs=inputs,
            outputs=outputs,
            validators=validators,
            seed=seed,
            timeout=timeout,
        )

    def audit_decision(self, node_id: str, *, required: bool = True) -> list[str]:
        errors = []
        decision = self.decision(node_id)
        if decision is None:
            return [f"{node_id}: 缺少 Agent 工具调用/跳过决策"] if required else []
        try:
            contract = self.graph.contract_hash(node_id)
        except (KeyError, ValueError) as exc:
            return [f"{node_id}: {exc}"]
        if decision.get("contract_hash") != contract:
            errors.append(f"{node_id}: 工具决策未绑定当前问题合同")
        if decision.get("catalog_hash") != self.registry.catalog_hash():
            errors.append(f"{node_id}: 工具决策使用了过期工具目录")
        if not str(decision.get("reason", "")).strip():
            errors.append(f"{node_id}: 工具决策缺少理由")
        action = decision.get("action")
        if action not in {"use", "skip"}:
            errors.append(f"{node_id}: 工具决策 action 非法")
            return errors
        if action == "skip":
            return errors
        selected = {
            item.get("id") for item in decision.get("selected_tools", [])
            if isinstance(item, dict)
        }
        current_runs = [
            run for run in self.executor.list(node_id)
            if run.get("contract_hash") == contract
            and run.get("decision_hash") == decision.get("decision_hash")
            and run.get("verification", {}).get("status") == "verified"
        ]
        used = {
            tool.get("id")
            for run in current_runs for tool in run.get("tools", [])
            if isinstance(tool, dict)
        }
        missing = sorted(selected - used)
        if missing:
            errors.append(f"{node_id}: 所选工具尚无 verified run: {missing}")
        if not current_runs:
            errors.append(f"{node_id}: use 决策没有 verified tool run")
        return errors

    def audit_stage(self, stage: str) -> list[str]:
        errors = []
        for node_id, node in self.graph.nodes.items():
            if node.get("superseded", False) or node["milestone"] != stage:
                continue
            pack = self.packs.match(node["task_type"], node.get("method_pack"))
            policy = validate_tool_policy(pack.get("tool_policy"))
            if policy["decision_required"]:
                errors.extend(self.audit_decision(node_id, required=True))
        return errors

    def acceptance_record(self, node_id: str, contract_hash: str) -> dict:
        errors = self.audit_decision(node_id, required=True)
        decision = self.decision(node_id)
        if decision and decision.get("contract_hash") != contract_hash:
            errors.append(f"{node_id}: task contract 与工具决策不匹配")
        return {
            "kind": "tool_decision",
            "node_id": node_id,
            "decision": decision.get("action") if decision else None,
            "decision_hash": (
                decision.get("decision_hash") if decision else None
            ),
            "errors": sorted(set(errors)),
            "ok": not errors,
        }
