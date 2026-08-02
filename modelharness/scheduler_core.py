"""Critical-frontier scheduler for local research work units."""
from __future__ import annotations

from pathlib import Path

from .contracts import validate_review
from .evidence import EvidenceGraph
from .integration import audit_integration
from .method_packs import MethodPackRegistry
from .problem_graph import ProblemGraph, canonical_hash
from .profiles import ProfileService
from .review_store import review_lineage_signature
from .stages import StageService
from .storage import read_json
from .util import sha256
from .workflow import WorkflowEngine


def _active_outputs(root: Path, node: dict) -> list[dict]:
    directory = root / "problem" / "data_raw"
    source_present = directory.is_dir() and any(
        path.is_file() for path in directory.rglob("*")
    )
    return [
        output for output in node["outputs"]
        if output.get("required_when") != "source_present"
        or source_present
    ]


def _artifact_signature(root: Path, node: dict) -> str:
    values = {}
    for output in _active_outputs(root, node):
        path = root / output["artifact"]
        values[output["evidence_id"]] = (
            sha256(path) if path.is_file() else None
        )
    return canonical_hash(values)


def _review_signature(root: Path, node: dict) -> str:
    values = {}
    for review in node.get("reviews", []):
        values[review["path"]] = review_lineage_signature(
            root, review["path"]
        )
    return canonical_hash(values)


class AdaptiveScheduler:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.stages = StageService(self.project)
        self.workflow = WorkflowEngine(self.project)
        self.evidence = EvidenceGraph(self.project)
        self.graph = ProblemGraph(self.project)
        self.packs = MethodPackRegistry(self.project)
        self.profiles = ProfileService(self.project)

    def _input_artifacts(self, node: dict) -> list[str]:
        artifacts = ["problem/statement.md", "modeling-project.json"]
        evidence = self.evidence.nodes
        for evidence_id in node.get("input_evidence", []):
            record = evidence.get(evidence_id)
            if record:
                artifacts.append(record["artifact"])
        return list(dict.fromkeys(artifacts))

    def _builder_tasks(self, item: dict) -> list[dict]:
        node_id, node = item["id"], item["node"]
        contract = item["contract_hash"]
        pack = self.packs.match(node["task_type"], node.get("method_pack"))
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
            protocol = "；".join(pack.get("protocol", []))
            description = (
                f"{node['question']}。方法包={pack['name']}@{pack['version']}。"
                f"研究协议：{protocol}。产出证据必须绑定 obligation_hash={contract}；"
                f"生成者只登记 candidate，不得自我审核。"
            )
            try:
                task = self.workflow.ensure_task(
                    f"{key_prefix}:{node_id}:{stream_id}:{suffix}",
                    node["milestone"],
                    stream.get("role", "research-worker"),
                    stream.get("description", description) + " " + description,
                    stream["owns"],
                    inputs=self._input_artifacts(node),
                    acceptance=acceptance,
                    budget=stream.get("budget", {"max_attempts": 3}),
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
        created = []
        for review in node.get("reviews", []):
            path = self.project / review["path"]
            approved = False
            if path.is_file():
                try:
                    record = validate_review(read_json(path), path)
                    approved = (
                        record["verdict"].upper() == "APPROVE"
                        and record.get("contract_hash", contract) == contract
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
                        f"只读取正式输入、产物和检查；输出审核必须绑定 "
                        f"contract_hash={contract} 和当前产物哈希。"
                    ),
                    [review["path"]],
                    inputs=self._input_artifacts(node) + [
                        x["artifact"]
                        for x in _active_outputs(self.project, node)
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

    def next_packet(self) -> dict:
        stage = self.stages.current()
        recovered = self.workflow.reconcile()
        if stage is None:
            integration_errors = audit_integration(self.project)
            return {
                "continue": bool(integration_errors),
                "terminal": "completed" if not integration_errors else None,
                "stage": None,
                "integration_errors": integration_errors,
                "instruction": (
                    "全部里程碑有效，交付 Profile 产物。"
                    if not integration_errors else
                    "最终集成审计失败；修复问题图与证据接口。"
                ),
            }
        tasks = self.workflow.list_tasks()
        evidence_nodes = self.evidence.nodes
        states = self.graph.states(evidence_nodes, tasks)
        frontier = self.graph.frontier(evidence_nodes, tasks, stage)
        max_parallel = max(
            1, int(self.profiles.active.get("max_parallel_agents", 3))
        )
        selected = frontier[:max_parallel]
        planned = []
        for item in selected:
            if item["state"] in {"ready", "repair"}:
                planned.extend(self._builder_tasks(item))
            elif item["state"] == "review":
                planned.extend(self._review_tasks(item))
        missing = []
        for evidence_id, contract, enforce in self.graph.milestone_outputs(stage):
            record = evidence_nodes.get(evidence_id)
            if not record or record.get("status") != "verified":
                missing.append(evidence_id)
            elif enforce and record.get("obligation_hash") != contract:
                missing.append(evidence_id)
        phase = "work"
        instruction = "领取最高优先级局部研究任务，按方法包协议产出 candidate 证据。"
        if not missing:
            phase = "gate"
            instruction = f"局部义务已闭合；运行 modelharness gate {stage}。"
        elif selected and all(item["state"] == "review" for item in selected):
            phase = "review"
            instruction = (
                "完成冷启动审核；审核通过后由 verifier 运行 evidence verify。"
            )
        elif selected and any(item["state"] == "repair" for item in selected):
            phase = "repair"
            instruction = (
                "按 REJECT findings 修订同一证据 ID，级联撤销下游后重新冷审。"
            )
        elif not selected:
            phase = "blocked"
            instruction = "当前无可执行前沿；检查缺失输入、冲突任务或问题图依赖。"
        packet = {
            "continue": True,
            "terminal": None,
            "project": str(self.project),
            "stage": stage,
            "phase": phase,
            "profile": self.profiles.active["name"],
            "valid_stage_prefix": self.stages.valid_prefix(),
            "problem_revision": self.graph.data["revision"],
            "node_states": states,
            "frontier": [
                {
                    "id": item["id"],
                    "state": item["state"],
                    "priority": item["priority"],
                    "question": item["node"]["question"],
                    "contract_hash": item["contract_hash"],
                }
                for item in frontier
            ],
            "missing_verified_evidence": missing,
            "tasks": planned or [
                x for x in tasks
                if x.get("status") in {"pending", "claimed", "running", "recovery_pending"}
            ],
            "recovered_leases": recovered,
            "instruction": instruction,
            "stop_policy": (
                "仅全部交付闭合、需要新授权/用户输入，或同一阻断连续三轮时停止。"
            ),
        }
        self.workflow.event("scheduler.tick", {
            "stage": stage,
            "phase": phase,
            "frontier": [x["id"] for x in frontier],
            "missing": missing,
        })
        from .supervisor import StateCapsule
        capsule = StateCapsule(self.project).build()
        packet["state_hash"] = capsule["state_hash"]
        packet["state_capsule"] = capsule
        return packet
