"""Adaptive scheduler with autonomous tool plans and stale-review rejection."""
from __future__ import annotations

from .review_store import (
    next_review_path,
    resolve_review,
)
from .scheduler_core import (
    AdaptiveScheduler as _AdaptiveScheduler,
    _active_outputs,
    _artifact_signature,
    _review_signature,
)
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
            active_outputs = self.graph.active_outputs(node_id)
            outputs = stream.get(
                "outputs", [x["evidence_id"] for x in active_outputs]
            )
            output_records = [
                x for x in active_outputs if x["evidence_id"] in outputs
            ]
            acceptance = list(node.get("acceptance", []))
            acceptance.extend(stream.get("acceptance", []))
            for required_test in pack.get("required_tests", []):
                if (
                    isinstance(required_test, dict)
                    and required_test.get("acceptance") not in acceptance
                ):
                    acceptance.append(required_test["acceptance"])
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
                f"生成者登记 candidate 时必须传 producer_task_id=本任务 ID，不得自我审核。"
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
                    side_effect_class=stream.get("side_effect_class", "project_local"),
                    idempotent=bool(stream.get("idempotent", True)),
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

    def _artifact_hashes(self, node: dict) -> dict[str, str | None]:
        return {
            output["artifact"]: (
                sha256(self.project / output["artifact"])
                if (self.project / output["artifact"]).is_file() else None
            )
            for output in _active_outputs(self.project, node)
        }

    def _migrate_legacy_review_tasks(self) -> list[str]:
        """Adopt versioned drafts created for old canonical review tasks."""
        tasks = self.workflow.list_tasks()
        occupied = [
            scope for task in tasks for scope in task.get("owns", [])
        ]
        rebound = []
        for task in tasks:
            if (
                task.get("task_type") != "independent_review"
                or task.get("status") not in self.workflow.ACTIVE_STATES
                or len(task.get("owns", [])) != 1
            ):
                continue
            node_id = task.get("work_item_id")
            node = self.graph.nodes.get(node_id)
            if not node:
                continue
            contract = self.graph.contract_hash(node_id)
            current_hashes = self._artifact_hashes(node)
            for review in node.get("reviews", []):
                logical = review["path"]
                prefix = f"review:{node_id}:{logical}:"
                if not str(task.get("idempotency_key", "")).startswith(prefix):
                    continue
                resolution = resolve_review(
                    self.project,
                    logical,
                    contract_hash=contract,
                    artifact_hashes=current_hashes,
                )
                target = None
                if (
                    resolution
                    and resolution.get("record")
                    and resolution["record"].get("task_id") == task["id"]
                    and resolution["path"].casefold()
                    != task["owns"][0].casefold()
                ):
                    target = resolution["path"]
                elif (
                    task.get("status") == "pending"
                    and (self.project / logical).is_file()
                    and task["owns"][0].casefold() == logical.casefold()
                ):
                    target = next_review_path(
                        self.project, logical, occupied=occupied
                    )
                if target:
                    migrated = self.workflow.rebind_review_output(
                        task["id"], target
                    )
                    occupied.append(target)
                    rebound.append(migrated["id"])
                break
        return rebound

    def next_packet(self) -> dict:
        self._migrate_legacy_review_tasks()
        return super().next_packet()

    def _review_tasks(self, item: dict) -> list[dict]:
        node_id, node = item["id"], item["node"]
        contract = item["contract_hash"]
        artifact_signature = _artifact_signature(self.project, node)
        current_hashes = self._artifact_hashes(node)
        tasks = self.workflow.list_tasks(work_item_id=node_id)
        occupied = [
            scope for task in self.workflow.list_tasks()
            for scope in task.get("owns", [])
        ]
        created = []
        for review in node.get("reviews", []):
            logical = review["path"]
            resolution = resolve_review(
                self.project,
                logical,
                contract_hash=contract,
                artifact_hashes=current_hashes,
            )
            if (
                resolution
                and resolution.get("record")
                and resolution["record"]["verdict"].upper() == "APPROVE"
            ):
                continue
            key_prefix = (
                f"review:{node_id}:{logical}:{artifact_signature}"
            )
            active = [
                task for task in tasks
                if task.get("status") in self.workflow.ACTIVE_STATES
                and (
                    task.get("idempotency_key") == key_prefix
                    or str(task.get("idempotency_key", "")).startswith(
                        key_prefix + ":"
                    )
                )
            ]
            if active:
                created.append(active[-1])
                continue
            output_path = next_review_path(
                self.project, logical, occupied=occupied
            )
            try:
                task = self.workflow.ensure_task(
                    f"{key_prefix}:{output_path}",
                    node["milestone"],
                    review.get("role", "independent-reviewer"),
                    (
                        f"冷启动审核局部问题 {node_id}：{node['question']}。"
                        f"只读取正式输入、产物、tool run manifest 和检查；"
                        f"输出审核必须绑定 contract_hash={contract}、task_id "
                        f"和全部当前产物哈希。写入不可变审核文件 "
                        f"{output_path}；不得覆盖或归档旧审核，也不需要为复审"
                        f"请求额外授权。"
                    ),
                    [output_path],
                    inputs=self._input_artifacts(node) + [
                        x["artifact"]
                        for x in _active_outputs(self.project, node)
                    ],
                    acceptance=[{
                        "kind": "artifact_exists", "path": output_path,
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
            occupied.append(output_path)
        return created
