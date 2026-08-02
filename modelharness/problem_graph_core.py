"""Problem-obligation graph for adaptive local research scheduling."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import EVIDENCE_KINDS, ID_RE, STAGES, require
from .storage import json_transaction, read_json


NODE_STATES = {
    "blocked", "ready", "active", "review", "repair", "verified", "superseded",
}
ACTIVE_TASK_STATES = {"pending", "claimed", "running", "recovery_pending"}


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _safe_project_path(value: str) -> bool:
    path = Path(value.replace("\\", "/"))
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def node_contract(node: dict) -> dict:
    """Semantic fields whose change invalidates work and produced evidence."""
    return {
        key: node.get(key)
        for key in (
            "question", "task_type", "depends_on", "input_evidence",
            "outputs", "acceptance", "method_pack", "workstreams", "reviews",
            "requirement_ids", "method_pack_options",
        )
    }


def node_contract_hash(node: dict) -> str:
    return canonical_hash(node_contract(node))


def validate_problem_graph(data: Any) -> dict:
    require(isinstance(data, dict), "problem_graph 必须是对象")
    require(data.get("schema") == 1, "problem_graph.schema 必须为 1")
    require(isinstance(data.get("revision"), int), "problem_graph.revision 必须是整数")
    require(isinstance(data.get("nodes"), dict), "problem_graph.nodes 必须是对象")
    nodes = data["nodes"]
    output_owners: dict[str, str] = {}
    for node_id, node in nodes.items():
        require(ID_RE.match(node_id) is not None, f"非法问题节点 ID: {node_id}")
        require(isinstance(node, dict), f"问题节点必须是对象: {node_id}")
        require(
            isinstance(node.get("question"), str) and node["question"].strip(),
            f"问题节点 question 缺失: {node_id}",
        )
        require(
            isinstance(node.get("task_type"), str) and node["task_type"],
            f"问题节点 task_type 缺失: {node_id}",
        )
        require(node.get("milestone") in STAGES, f"问题节点 milestone 非法: {node_id}")
        require(
            isinstance(node.get("depends_on", []), list),
            f"问题节点 depends_on 必须是数组: {node_id}",
        )
        require(
            isinstance(node.get("input_evidence", []), list),
            f"问题节点 input_evidence 必须是数组: {node_id}",
        )
        for evidence_id in node.get("input_evidence", []):
            require(
                isinstance(evidence_id, str) and ID_RE.match(evidence_id),
                f"问题节点输入证据 ID 非法: {node_id}",
            )
        outputs = node.get("outputs", [])
        require(isinstance(outputs, list) and outputs, f"问题节点 outputs 为空: {node_id}")
        for output in outputs:
            require(isinstance(output, dict), f"问题节点 output 必须是对象: {node_id}")
            evidence_id = output.get("evidence_id")
            require(
                isinstance(evidence_id, str) and ID_RE.match(evidence_id),
                f"问题节点输出证据 ID 非法: {node_id}",
            )
            require(
                output.get("kind") in EVIDENCE_KINDS,
                f"问题节点输出 kind 非法: {node_id}/{evidence_id}",
            )
            require(
                isinstance(output.get("artifact"), str)
                and _safe_project_path(output["artifact"]),
                f"问题节点输出路径非法: {node_id}/{evidence_id}",
            )
            require(
                evidence_id not in output_owners,
                f"输出证据被多个节点声明: {evidence_id}",
            )
            output_owners[evidence_id] = node_id
        workstreams = node.get("workstreams", [])
        require(
            isinstance(workstreams, list) and workstreams,
            f"问题节点 workstreams 为空: {node_id}",
        )
        stream_ids = set()
        for stream in workstreams:
            require(isinstance(stream, dict), f"workstream 必须是对象: {node_id}")
            stream_id = stream.get("id")
            require(
                isinstance(stream_id, str) and stream_id and stream_id not in stream_ids,
                f"workstream.id 缺失或重复: {node_id}",
            )
            stream_ids.add(stream_id)
            owns = stream.get("owns", [])
            require(
                isinstance(owns, list) and owns
                and all(isinstance(x, str) and _safe_project_path(x) for x in owns),
                f"workstream.owns 非法: {node_id}/{stream_id}",
            )
        reviews = node.get("reviews", [])
        require(isinstance(reviews, list), f"问题节点 reviews 必须是数组: {node_id}")
        for review in reviews:
            require(isinstance(review, dict), f"review 必须是对象: {node_id}")
            require(
                isinstance(review.get("path"), str)
                and _safe_project_path(review["path"]),
                f"review.path 非法: {node_id}",
            )
        requirement_ids = node.get("requirement_ids", [])
        require(
            isinstance(requirement_ids, list)
            and all(
                isinstance(item, str) and ID_RE.match(item)
                for item in requirement_ids
            ),
            f"问题节点 requirement_ids 必须是合法 ID 数组: {node_id}",
        )
        method_pack_options = node.get("method_pack_options", [])
        require(
            isinstance(method_pack_options, list)
            and all(
                isinstance(item, str) and item
                for item in method_pack_options
            )
            and (
                not method_pack_options
                or node.get("method_pack") in method_pack_options
            ),
            f"问题节点 method_pack_options 必须是字符串数组: {node_id}",
        )
        risk = node.get("risk", {})
        require(isinstance(risk, dict), f"问题节点 risk 必须是对象: {node_id}")
        for field in (
            "downstream_impact",
            "uncertainty",
            "estimated_cost",
            "improvement_value",
            "coverage_value",
        ):
            value = risk.get(field, 1)
            require(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                and value > 0,
                f"问题节点 risk.{field} 必须为正数: {node_id}",
            )
    for node_id, node in nodes.items():
        for dep in node.get("depends_on", []):
            require(dep in nodes, f"问题节点存在未知依赖: {node_id} -> {dep}")
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError(f"问题图存在环: {node_id}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dep in nodes[node_id].get("depends_on", []):
            walk(dep)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in nodes:
        walk(node_id)
    return data


class ProblemGraph:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / ".harness" / "problem_graph.json"

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    @property
    def data(self) -> dict:
        data = read_json(self.path)
        if data is None:
            raise ValueError("Problem Graph 缺失；旧项目请先运行 plan init")
        return validate_problem_graph(data)

    @property
    def nodes(self) -> dict:
        return self.data["nodes"]

    def contract_hash(self, node_id: str) -> str:
        return node_contract_hash(self.nodes[node_id])

    def active_outputs(self, node_id: str) -> list[dict]:
        outputs = self.nodes[node_id]["outputs"]
        source_present = False
        directory = self.root / "problem" / "data_raw"
        if directory.is_dir():
            source_present = any(
                path.is_file() for path in directory.rglob("*")
            )
        return [
            output for output in outputs
            if output.get("required_when") != "source_present"
            or source_present
        ]

    def output_ids(self, node_id: str) -> list[str]:
        return [x["evidence_id"] for x in self.active_outputs(node_id)]

    def completion(
        self, node_id: str, evidence_nodes: dict
    ) -> bool:
        node = self.nodes[node_id]
        expected = self.contract_hash(node_id)
        for output in self.active_outputs(node_id):
            evidence = evidence_nodes.get(output["evidence_id"])
            if not evidence or evidence.get("status") != "verified":
                return False
            if node.get("enforce_contract", True):
                if evidence.get("obligation_hash") != expected:
                    return False
        return True

    def state(self, node_id: str, evidence_nodes: dict, tasks: list[dict]) -> str:
        node = self.nodes[node_id]
        if node.get("superseded", False):
            return "superseded"
        if self.completion(node_id, evidence_nodes):
            return "verified"
        if any(
            not self.completion(dep, evidence_nodes)
            for dep in node.get("depends_on", [])
            if not self.nodes[dep].get("superseded", False)
        ):
            return "blocked"
        if any(
            evidence_nodes.get(item, {}).get("status") != "verified"
            for item in node.get("input_evidence", [])
        ):
            return "blocked"
        related = [x for x in tasks if x.get("work_item_id") == node_id]
        if any(x.get("status") in ACTIVE_TASK_STATES for x in related):
            return "active"
        outputs = [
            evidence_nodes.get(output["evidence_id"])
            for output in self.active_outputs(node_id)
        ]
        review_paths = [self.root / x["path"] for x in node.get("reviews", [])]
        if any(x and x.get("status") == "rejected" for x in outputs):
            return "repair"
        if any(path.is_file() for path in review_paths):
            for path in review_paths:
                if not path.is_file():
                    continue
                review = read_json(path)
                if str(review.get("verdict", "")).upper() == "REJECT":
                    return "repair"
        if outputs and all(x is not None for x in outputs):
            return "review"
        return "ready"

    def states(self, evidence_nodes: dict, tasks: list[dict]) -> dict[str, str]:
        return {
            node_id: self.state(node_id, evidence_nodes, tasks)
            for node_id in self.nodes
        }

    def frontier(
        self, evidence_nodes: dict, tasks: list[dict], milestone: str
    ) -> list[dict]:
        states = self.states(evidence_nodes, tasks)
        index = STAGES.index(milestone)
        ready = []
        for node_id, node in self.nodes.items():
            state = states[node_id]
            if state not in {"ready", "review", "repair"}:
                continue
            if STAGES.index(node["milestone"]) > index:
                continue
            risk = node.get("risk", {})
            base = (
                float(risk.get("downstream_impact", 1))
                * float(risk.get("uncertainty", 1))
                / float(risk.get("estimated_cost", 1))
            )
            value = (
                max(0.0, float(risk.get("decision_change_probability", 1)))
                * max(0.0, float(risk.get("information_gain", 1)))
                * max(0.0, float(risk.get("falsification_value", 1)))
                * max(0.0, float(risk.get("improvement_value", 1)))
                * max(0.0, float(risk.get("coverage_value", 1)))
            )
            penalties = sum(max(0.0, float(risk.get(key, 0))) for key in (
                "risk_penalty", "latency_penalty", "repeat_penalty"
            ))
            score = base * value / (1 + penalties)
            ready.append({
                "id": node_id,
                "state": state,
                "priority": score,
                "contract_hash": self.contract_hash(node_id),
                "node": node,
            })
        return sorted(ready, key=lambda x: (-x["priority"], x["id"]))

    def milestone_outputs(self, stage: str) -> list[tuple[str, str, bool]]:
        outputs = []
        for node_id, node in self.nodes.items():
            if node.get("superseded", False) or node["milestone"] != stage:
                continue
            for output in self.active_outputs(node_id):
                outputs.append((
                    output["evidence_id"],
                    self.contract_hash(node_id),
                    node.get("enforce_contract", True),
                ))
        return outputs

    def closure_hash(self, stage: str) -> str:
        index = STAGES.index(stage)
        contracts = {
            node_id: self.contract_hash(node_id)
            for node_id, node in self.nodes.items()
            if not node.get("superseded", False)
            and STAGES.index(node["milestone"]) <= index
        }
        return canonical_hash(contracts)

    def replace(self, proposal: dict, reason: str) -> dict:
        if not reason.strip():
            raise ValueError("问题图修订原因不能为空")
        validated = validate_problem_graph(proposal)
        with json_transaction(
            self.path, {"schema": 1, "revision": 0, "nodes": {}}
        ) as current:
            old_nodes = current.get("nodes", {})
            old_hashes = {
                key: node_contract_hash(value) for key, value in old_nodes.items()
            }
            new_nodes = validated["nodes"]
            new_hashes = {
                key: node_contract_hash(value) for key, value in new_nodes.items()
            }
            changed = sorted({
                *[key for key in old_hashes if old_hashes.get(key) != new_hashes.get(key)],
                *[key for key in new_hashes if old_hashes.get(key) != new_hashes.get(key)],
            })
            impacted = sorted({
                output["evidence_id"]
                for key in changed
                for output in old_nodes.get(key, {}).get("outputs", [])
            })
            current.clear()
            current.update(validated)
            current["revision"] = int(current.get("revision", 0)) + 1
            current["last_revision_reason"] = reason.strip()
        return {
            "changed_nodes": changed,
            "impacted_evidence": impacted,
            "revision": self.data["revision"],
        }
