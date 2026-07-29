from __future__ import annotations

import subprocess
import sys
from collections import deque
from pathlib import Path

from .util import now, read_json, sha256, write_json

KINDS = {
    "problem", "data", "assumption", "claim", "model", "code",
    "experiment", "result", "decision", "limitation", "narrative",
}
STATUSES = {"candidate", "verified", "rejected", "revoked"}


class EvidenceGraph:
    """Content-aware evidence DAG.

    Candidate material may be explored freely. Only the verifier command can
    promote it to verified truth. Revocation cascades through dependencies.
    """

    def __init__(self, root: Path):
        self.root = root
        self.path = root / ".harness" / "evidence.json"
        self.data = read_json(self.path, {"schema": 1, "nodes": {}})

    @property
    def nodes(self) -> dict:
        return self.data["nodes"]

    def save(self) -> None:
        write_json(self.path, self.data)

    def add(
        self, node_id: str, kind: str, statement: str, artifact: str,
        depends: list[str] | None = None, check: str | None = None,
    ) -> dict:
        if kind not in KINDS:
            raise ValueError(f"未知 kind: {kind}; 可选 {sorted(KINDS)}")
        if node_id in self.nodes:
            raise ValueError(f"节点已存在: {node_id}")
        deps = depends or []
        missing = [x for x in deps if x not in self.nodes]
        if missing:
            raise ValueError(f"依赖节点不存在: {missing}")
        p = (self.root / artifact).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError("artifact 必须位于项目目录内")
        node = {
            "id": node_id,
            "kind": kind,
            "statement": statement,
            "artifact": artifact.replace("\\", "/"),
            "artifact_sha256": sha256(p) if p.is_file() else None,
            "depends_on": deps,
            "check": check,
            "status": "candidate",
            "created_at": now(),
            "verified_at": None,
            "verification": None,
        }
        self.nodes[node_id] = node
        self.save()
        return node

    def verify(self, node_id: str) -> dict:
        node = self.nodes[node_id]
        bad = [d for d in node["depends_on"]
               if self.nodes[d]["status"] != "verified"]
        if bad:
            raise ValueError(f"依赖尚未验证: {bad}")
        artifact = self.root / node["artifact"]
        if not artifact.is_file():
            raise ValueError(f"证据文件不存在: {node['artifact']}")
        current_hash = sha256(artifact)
        cmd = node.get("check")
        record = {"time": now(), "command": cmd, "returncode": 0,
                  "stdout_tail": "", "stderr_tail": ""}
        if cmd:
            run = subprocess.run(
                cmd, cwd=self.root, shell=True, text=True,
                capture_output=True, timeout=1800,
            )
            record.update({
                "returncode": run.returncode,
                "stdout_tail": (run.stdout or "")[-2000:],
                "stderr_tail": (run.stderr or "")[-2000:],
            })
            if run.returncode:
                node["status"] = "rejected"
                node["verification"] = record
                self.save()
                raise RuntimeError(f"验证失败（退出码 {run.returncode}）")
        node.update({
            "status": "verified",
            "artifact_sha256": current_hash,
            "verified_at": now(),
            "verification": record,
        })
        self.save()
        return node

    def audit(self) -> list[str]:
        errors: list[str] = []
        for node_id, node in self.nodes.items():
            if node["status"] != "verified":
                continue
            artifact = self.root / node["artifact"]
            if not artifact.is_file():
                errors.append(f"{node_id}: 已验证证据文件缺失")
            elif sha256(artifact) != node["artifact_sha256"]:
                errors.append(f"{node_id}: 验证后证据被修改")
            for dep in node["depends_on"]:
                if self.nodes.get(dep, {}).get("status") != "verified":
                    errors.append(f"{node_id}: 依赖 {dep} 已非 verified")
        return errors

    def revoke(self, node_id: str, reason: str) -> list[str]:
        reverse: dict[str, list[str]] = {}
        for key, node in self.nodes.items():
            for dep in node["depends_on"]:
                reverse.setdefault(dep, []).append(key)
        queue, affected = deque([node_id]), []
        while queue:
            current = queue.popleft()
            if current in affected:
                continue
            affected.append(current)
            queue.extend(reverse.get(current, []))
        for key in affected:
            self.nodes[key]["status"] = "revoked"
            self.nodes[key]["revocation"] = {
                "time": now(), "root": node_id, "reason": reason,
            }
        self.save()
        return affected

    def verified(self, kinds: set[str] | None = None) -> list[dict]:
        return [
            node for node in self.nodes.values()
            if node["status"] == "verified"
            and (not kinds or node["kind"] in kinds)
        ]
