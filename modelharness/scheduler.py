"""Adaptive scheduler with autonomous tool plans and stale-review rejection."""
from __future__ import annotations

from .contracts import validate_review
from .scheduler_core import (
    AdaptiveScheduler as _AdaptiveScheduler,
    _artifact_signature,
    _review_signature,
)
from .storage import read_json
from .toolchain import ToolchainService, validate_tool_policy
from .util import sha256


class AdaptiveScheduler(_AdaptiveScheduler):
    def _builder_tasks(self, item: dict) -> list[dict]:
        node_id, node = item["id"], item["node"]
        contract = item["contract_hash"]
        pack = self.packs.match(node["task_type"], node.get("method_pack"))
        tool_policy = validate_tool_policy(pack.get("tool_policy"))
        tools = ToolchainService(self.project)
        tool_plan = tools.recommend(node_id, persist=True)
        created = []
        repair_signature = _review_signature(self.project, node)
        for stream in node["workstreams"]:
            stream_id = stream["id"]
            key_prefix = "repair" if item["state"] == "repair" else "build"
            suffix = repair_signature if item["state"] == "repair" else contract
            outputs = stream.get(
                "outputs", [x["evidence_id"] for x in node["outputs"]]
            )
            output_records = [
                x for x in node["outputs"] if x["evidence_id"] in outputs
            ]
            acceptance = list(stream.get("acceptance", []))
            acceptance.extend(
                {"kind": "artifact_exists", "path": output["artifact"]}
                for output in output_records
            )
            acceptance.extend(
                {"kind": "evidence_exists", "id": output["evidence_id"]}
                for output in output_records
            )
            if tool_policy["decision_required"]:
                acceptance.append({
                    "kind": "tool_decision",
                    "node_id": node_id,
                    "contract_hash": contract,
                })
            protocol = "；".join(pack.get("protocol", []))
            tool_instruction = (
                f"工具计划={tool_plan['plan_hash']}，建议="
                f"{tool_plan['suggested_tools']}。你有权根据局部问题选择 use 或 "
                "skip，但必须运行 `modelharness tool decide` 登记理由；"
                "use 后必须通过 `modelharness tool run` 形成 verified run。"
            )
            description = (
                f"{node['question']}。方法包={pack['name']}@{pack['version']}。"
                f"研究协议：{protocol}。{tool_instruction}"
                f"产出证据必须绑定 obligation_hash={contract}；"
                f"生成者只登记 candidate，不得自我审核。"
            )
            budget = dict(stream.get("budget", {"max_attempts": 3}))
            budget["tool_plan"] = {
                "plan_hash": tool_plan["plan_hash"],
                "recommendation": tool_plan["recommendation"],
                "suggested_tools": tool_plan["suggested_tools"],
                "required_missing": tool_plan["required_missing"],
                "validation_protocols": tool_plan["validation_protocols"],
            }
            try:
                task = self.workflow.ensure_task(
                    f"{key_prefix}:{node_id}:{stream_id}:{suffix}",
                    node["milestone"],
                    stream.get("role", "research-worker"),
                    stream.get("description", description) + " " + description,
                    stream["owns"],
                    inputs=self._input_artifacts(node),
                    acceptance=acceptance,
                    budget=budget,
                    work_item_id=node_id,
                    task_type=node["task_type"],
                    contract_hash=contract,
                    generation=1 + len(self.workflow.list_tasks(
                        work_item_id=node_id
                    )),
                )
            except ValueError as exc:
                task = {
                    "work_item_id": node_id,
                    "role": stream.get("role", "research-worker"),
                    "status": "conflict",
                    "error": str(exc),
                }
            created.append(task)
        return created

    def _review_tasks(self, item: dict) -> list[dict]:
        node_id, node = item["id"], item["node"]
        contract = item["contract_hash"]
        artifact_signature = _artifact_signature(self.project, node)
        current_hashes = {
            output["artifact"]: (
                sha256(self.project / output["artifact"])
                if (self.project / output["artifact"]).is_file() else None
            )
            for output in node["outputs"]
        }
        created = []
        for review in node.get("reviews", []):
            path = self.project / review["path"]
            approved = False
            if path.is_file():
                try:
                    record = validate_review(read_json(path), path)
                    reviewed = record.get("artifact_hashes", {})
                    approved = (
                        record["verdict"].upper() == "APPROVE"
                        and record.get("contract_hash") == contract
                        and all(
                            reviewed.get(relative) == digest
                            for relative, digest in current_hashes.items()
                        )
                    )
                except ValueError:
                    approved = False
            if approved:
                continue
            try:
                task = self.workflow.ensure_task(
                    f"review:{node_id}:{review['path']}:{artifact_signature}",
                    node["milestone"],
                    review.get("role", "independent-reviewer"),
                    (
                        f"冷启动审核局部问题 {node_id}：{node['question']}。"
                        f"只读取正式输入、产物、tool run manifest 和检查；"
                        f"输出审核必须绑定 contract_hash={contract}、task_id "
                        f"和全部当前产物哈希。"
                    ),
                    [review["path"]],
                    inputs=self._input_artifacts(node) + [
                        x["artifact"] for x in node["outputs"]
                    ],
                    acceptance=[{
                        "kind": "artifact_exists", "path": review["path"],
                    }],
                    budget={"max_attempts": 3},
                    work_item_id=node_id,
                    task_type="independent_review",
                    contract_hash=contract,
                    generation=1 + len(self.workflow.list_tasks(
                        work_item_id=node_id
                    )),
                )
            except ValueError as exc:
                task = {
                    "work_item_id": node_id,
                    "role": review.get("role", "independent-reviewer"),
                    "status": "conflict",
                    "error": str(exc),
                }
            created.append(task)
        return created
