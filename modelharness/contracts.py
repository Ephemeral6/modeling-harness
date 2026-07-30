"""Runtime contracts for configuration, evidence and review artifacts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

STAGES = tuple(f"s{i}" for i in range(7))
EVIDENCE_KINDS = {
    "problem", "data", "assumption", "claim", "model", "code",
    "experiment", "result", "decision", "limitation", "narrative",
}
EVIDENCE_STATUSES = {"candidate", "verified", "rejected", "revoked"}
ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def safe_relative(root: Path, value: str | Path) -> Path:
    require(bool(str(value)), "路径不能为空")
    candidate = (root / value).resolve()
    require(candidate.is_relative_to(root.resolve()), f"路径越出项目边界: {value}")
    return candidate


def validate_stage_config(data: Any) -> dict:
    require(isinstance(data, dict), "stages.json 必须是对象")
    require(set(data) == set(STAGES), "stages.json 必须且只能包含 s0–s6")
    for stage in STAGES:
        spec = data[stage]
        require(isinstance(spec, dict), f"{stage} 配置必须是对象")
        require(
            isinstance(spec.get("name"), str) and spec["name"],
            f"{stage}.name 缺失",
        )
        for field in ("requires", "reviews", "checks"):
            require(
                isinstance(spec.get(field, []), list),
                f"{stage}.{field} 必须是数组",
            )
        for node_id in spec.get("requires", []):
            require(
                isinstance(node_id, str) and ID_RE.match(node_id) is not None,
                f"{stage} 包含非法 evidence id: {node_id}",
            )
    return data


def validate_review(data: Any, path: Path) -> dict:
    require(isinstance(data, dict), f"审核必须是 JSON 对象: {path}")
    verdict = str(data.get("verdict", "")).upper()
    require(verdict in {"APPROVE", "REJECT"}, f"审核 verdict 非法: {path}")
    for field in ("scope", "findings", "required_fixes", "evidence_checked"):
        if field in data:
            require(isinstance(data[field], list), f"审核 {field} 必须是数组: {path}")
    for field in ("reviewer", "task_id", "contract_hash"):
        if field in data and data[field] is not None:
            require(isinstance(data[field], str), f"审核 {field} 必须是字符串: {path}")
    artifact_hashes = data.get("artifact_hashes", {})
    require(
        isinstance(artifact_hashes, dict),
        f"审核 artifact_hashes 必须是对象: {path}",
    )
    require(
        all(isinstance(k, str) and isinstance(v, str)
            for k, v in artifact_hashes.items()),
        f"审核 artifact_hashes 非法: {path}",
    )
    return data


def validate_evidence_graph(data: Any) -> dict:
    require(isinstance(data, dict), "evidence.json 必须是对象")
    require(data.get("schema", 2) in {2, 3}, "evidence.schema 必须为 2 或 3")
    require(isinstance(data.get("nodes"), dict), "evidence.nodes 必须是对象")
    nodes = data["nodes"]
    for node_id, node in nodes.items():
        require(ID_RE.match(node_id) is not None, f"非法 evidence id: {node_id}")
        require(isinstance(node, dict), f"节点必须是对象: {node_id}")
        require(node.get("kind") in EVIDENCE_KINDS, f"节点 kind 非法: {node_id}")
        require(
            node.get("status") in EVIDENCE_STATUSES,
            f"节点 status 非法: {node_id}",
        )
        deps = node.get("depends_on", [])
        require(isinstance(deps, list), f"节点依赖必须是数组: {node_id}")
        require(all(x in nodes for x in deps), f"节点存在未知依赖: {node_id}")
        checks = node.get("checks", [])
        require(
            checks is None or isinstance(checks, list),
            f"节点 checks 必须是数组: {node_id}",
        )
        reviews = node.get("reviews", [])
        require(
            reviews is None or (
                isinstance(reviews, list)
                and all(isinstance(x, str) for x in reviews)
            ),
            f"节点 reviews 必须是字符串数组: {node_id}",
        )
    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(node_id: str) -> None:
        if node_id in visiting:
            raise ValueError(f"证据图存在环: {node_id}")
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
