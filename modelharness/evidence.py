from __future__ import annotations

import subprocess
from collections import deque
from pathlib import Path

from .contracts import EVIDENCE_KINDS, ID_RE, safe_relative, validate_evidence_graph
from .storage import json_transaction, read_json
from .util import now, sha256

KINDS = EVIDENCE_KINDS
STATUSES = {"candidate", "verified", "rejected", "revoked"}


class EvidenceGraph:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / ".harness" / "evidence.json"

    @property
    def data(self) -> dict:
        data = read_json(self.path, {"schema": 2, "revision": 0, "nodes": {}})
        data.setdefault("revision", 0)
        return validate_evidence_graph(data)

    @property
    def nodes(self) -> dict:
        return self.data["nodes"]

    def add(self, node_id: str, kind: str, statement: str, artifact: str,
            depends: list[str] | None = None, check: str | None = None) -> dict:
        if ID_RE.match(node_id) is None:
            raise ValueError(f"非法 evidence id: {node_id}")
        if kind not in KINDS:
            raise ValueError(f"未知 evidence kind: {kind}")
        artifact_path = safe_relative(self.root, artifact)
        deps = list(dict.fromkeys(depends or []))
        with json_transaction(
            self.path, {"schema": 2, "revision": 0, "nodes": {}}
        ) as data:
            validate_evidence_graph(data)
            if node_id in data["nodes"]:
                raise ValueError(f"证据节点已存在: {node_id}")
            unknown = [x for x in deps if x not in data["nodes"]]
            if unknown:
                raise ValueError(f"依赖节点不存在: {unknown}")
            record = {
                "id": node_id, "kind": kind, "statement": statement.strip(),
                "artifact": artifact_path.relative_to(self.root).as_posix(),
                "artifact_sha256": sha256(artifact_path) if artifact_path.is_file() else None,
                "depends_on": deps, "check": check, "status": "candidate",
                "created_at": now(), "verified_at": None,
                "verification": None, "revocation": None,
            }
            data["nodes"][node_id] = record
            data["revision"] = int(data.get("revision", 0)) + 1
            validate_evidence_graph(data)
        return record

    def verify(self, node_id: str, timeout: int = 1800) -> dict:
        # Run the potentially long check outside the state lock.
        snapshot = self.data
        if node_id not in snapshot["nodes"]:
            raise ValueError(f"证据节点不存在: {node_id}")
        node = snapshot["nodes"][node_id]
        bad = [x for x in node["depends_on"]
               if snapshot["nodes"][x]["status"] != "verified"]
        if bad:
            raise ValueError(f"依赖尚未验证: {bad}")
        artifact = safe_relative(self.root, node["artifact"])
        if not artifact.is_file():
            raise ValueError(f"证据文件不存在: {node['artifact']}")
        artifact_hash = sha256(artifact)
        check = node.get("check")
        verification = {
            "time": now(), "command": check, "returncode": 0,
            "stdout_tail": "", "stderr_tail": "",
        }
        if check:
            proc = subprocess.run(
                check, cwd=self.root, shell=True, text=True,
                capture_output=True, timeout=timeout,
            )
            verification.update({
                "returncode": proc.returncode,
                "stdout_tail": (proc.stdout or "")[-4000:],
                "stderr_tail": (proc.stderr or "")[-4000:],
            })
        failed = bool(verification["returncode"])
        with json_transaction(self.path, snapshot) as data:
            current = data["nodes"].get(node_id)
            if current is None:
                raise RuntimeError("验证期间证据节点被删除")
            # Optimistic concurrency: recheck dependencies and artifact.
            for dep in current["depends_on"]:
                if data["nodes"][dep]["status"] != "verified":
                    raise RuntimeError(f"dependency invalidated during verification: {dep}")
            if sha256(artifact) != artifact_hash:
                raise RuntimeError(f"artifact changed during verification: {node_id}")
            for field in ("artifact", "depends_on", "check"):
                if current.get(field) != node.get(field):
                    raise RuntimeError(f"验证期间节点定义发生变化: {node_id}")
            if failed:
                current.update({"status": "rejected", "verification": verification})
            else:
                current.update({
                    "status": "verified", "artifact_sha256": artifact_hash,
                    "verified_at": now(), "verification": verification,
                    "revocation": None,
                })
            data["revision"] += 1
            result = dict(current)
        if failed:
            raise RuntimeError(f"证据验证失败: {node_id}")
        return result

    def audit(self) -> list[str]:
        data = self.data
        errors: list[str] = []
        for node_id, node in data["nodes"].items():
            if node["status"] != "verified":
                continue
            artifact = safe_relative(self.root, node["artifact"])
            if not artifact.is_file():
                errors.append(f"{node_id}: 已验证证据文件缺失")
            elif sha256(artifact) != node.get("artifact_sha256"):
                errors.append(f"{node_id}: 验证后证据被修改")
            for dep in node["depends_on"]:
                if data["nodes"][dep]["status"] != "verified":
                    errors.append(f"{node_id}: 依赖 {dep} 已非 verified")
        return errors

    def revoke(self, node_id: str, reason: str) -> list[str]:
        if not reason.strip():
            raise ValueError("撤销原因不能为空")
        with json_transaction(
            self.path, {"schema": 2, "revision": 0, "nodes": {}}
        ) as data:
            if node_id not in data["nodes"]:
                raise ValueError(f"证据节点不存在: {node_id}")
            reverse: dict[str, list[str]] = {}
            for key, node in data["nodes"].items():
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
                data["nodes"][key].update({
                    "status": "revoked",
                    "revocation": {
                        "time": now(), "root": node_id, "reason": reason.strip(),
                    },
                })
            data["revision"] += 1
        return affected

    def verified(self, kinds: set[str] | None = None) -> list[dict]:
        return [
            node for node in self.data["nodes"].values()
            if node["status"] == "verified"
            and (not kinds or node["kind"] in kinds)
        ]
