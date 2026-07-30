"""Toolchain facade with events, explicit fallback and live integrity audits."""
from __future__ import annotations

from .contracts import safe_relative
from .toolchain_core import (
    ToolchainService as _ToolchainService,
    validate_tool_policy,
)
from .toolchain_execution import validate_validators
from .util import sha256
from .workflow import WorkflowEngine


class ToolchainService(_ToolchainService):
    def decide(self, *args, **kwargs) -> dict:
        record = super().decide(*args, **kwargs)
        WorkflowEngine(self.root).event("tool.decision", {
            "node_id": record["node_id"],
            "contract_hash": record["contract_hash"],
            "decision_hash": record["decision_hash"],
            "revision": record["revision"],
            "action": record["action"],
            "tools": [
                item["id"] for item in record.get("selected_tools", [])
            ],
        })
        return record

    def fallback(
        self,
        node_id: str,
        failed_tools: list[str],
        reason: str,
        *,
        allow_skip: bool = False,
    ) -> dict:
        if not failed_tools:
            raise ValueError("fallback 至少需要一个 failed tool")
        previous = self.decision(node_id) or {}
        extras = list(previous.get("extra_capabilities", []))
        plan = self.recommend(
            node_id, extra_capabilities=extras, persist=True
        )
        excluded = set(failed_tools)
        selected = []
        unavailable_capabilities = []
        for capability in (
            plan["required_capabilities"]
            + plan["preferred_capabilities"]
            + extras
        ):
            options = [
                item for item in plan["candidates"].get(capability, [])
                if item["id"] not in excluded
            ]
            if options:
                if options[0]["id"] not in selected:
                    selected.append(options[0]["id"])
            elif capability in plan["required_capabilities"]:
                unavailable_capabilities.append(capability)
        fallback_reason = (
            f"工具 {sorted(excluded)} 失败，执行显式降级：{reason.strip()}"
        )
        if unavailable_capabilities or not selected:
            if allow_skip:
                return self.decide(
                    node_id, "skip",
                    fallback_reason
                    + f"；无可用替代能力={unavailable_capabilities or 'none'}",
                    extra_capabilities=extras,
                )
            raise RuntimeError(
                "不存在满足必需能力的自主降级工具；"
                f"missing={unavailable_capabilities}"
            )
        return self.decide(
            node_id,
            "use",
            fallback_reason,
            tools=selected,
            extra_capabilities=extras,
        )

    def run(self, *args, **kwargs) -> dict:
        record = super().run(*args, **kwargs)
        WorkflowEngine(self.root).event("tool.run", {
            "run_id": record["id"],
            "node_id": record["node_id"],
            "contract_hash": record["contract_hash"],
            "decision_hash": record["decision_hash"],
            "tools": [item["id"] for item in record.get("tools", [])],
            "returncode": record["returncode"],
            "verification": record["verification"]["status"],
        })
        return record

    def audit_decision(self, node_id: str, *, required: bool = True) -> list[str]:
        live_errors = []
        decision = self.decision(node_id)
        if decision and decision.get("action") == "use":
            try:
                contract = self.graph.contract_hash(node_id)
            except (KeyError, ValueError):
                contract = None
            for run in self.executor.list(node_id):
                if (
                    run.get("contract_hash") != contract
                    or run.get("decision_hash") != decision.get("decision_hash")
                ):
                    continue
                verification = self.executor._verify_record(
                    run, validate_validators(run.get("validators", []))
                )
                if verification["status"] != "verified":
                    live_errors.append(
                        f"{node_id}: tool run 当前产物验证失败: {run.get('id')}"
                    )
                for log_key in ("stdout", "stderr"):
                    relative = run.get("logs", {}).get(log_key)
                    expected = run.get("logs", {}).get(f"{log_key}_sha256")
                    try:
                        path = safe_relative(self.root, relative or "")
                        valid = path.is_file() and sha256(path) == expected
                    except ValueError:
                        valid = False
                    if not valid:
                        live_errors.append(
                            f"{node_id}: tool run 日志哈希失效: "
                            f"{run.get('id')}/{log_key}"
                        )
        return [
            *super().audit_decision(node_id, required=required),
            *live_errors,
        ]


__all__ = ["ToolchainService", "validate_tool_policy"]
