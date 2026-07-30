"""Content-addressed evidence graph with contract-bound verification."""
from __future__ import annotations

from collections import deque
from pathlib import Path

from .checks import run_checks
from .contracts import (
    EVIDENCE_KINDS,
    ID_RE,
    safe_relative,
    validate_evidence_graph,
    validate_review,
)
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
        data = read_json(self.path, {"schema": 3, "revision": 0, "nodes": {}})
        data.setdefault("revision", 0)
        return validate_evidence_graph(data)

    @property
    def nodes(self) -> dict:
        return self.data["nodes"]

    def add(
        self,
        node_id: str,
        kind: str,
        statement: str,
        artifact: str,
        depends: list[str] | None = None,
        check: str | list | dict | None = None,
        *,
        checks: list | None = None,
        obligation_hash: str | None = None,
        reviews: list[str] | None = None,
        producer_task_id: str | None = None,
    ) -> dict:
        if ID_RE.match(node_id) is None:
            raise ValueError(f"非法 evidence id: {node_id}")
        if kind not in KINDS:
            raise ValueError(f"未知 evidence kind: {kind}")
        artifact_path = safe_relative(self.root, artifact)
        deps = list(dict.fromkeys(depends or []))
        check_list = list(checks or ([] if check is None else [check]))
        review_list = list(dict.fromkeys(reviews or []))
        for review in review_list:
            safe_relative(self.root, review)
        with json_transaction(
            self.path, {"schema": 3, "revision": 0, "nodes": {}}
        ) as data:
            validate_evidence_graph(data)
            if node_id in data["nodes"]:
                raise ValueError(f"证据节点已存在: {node_id}")
            unknown = [x for x in deps if x not in data["nodes"]]
            if unknown:
                raise ValueError(f"依赖节点不存在: {unknown}")
            record = {
                "id": node_id,
                "kind": kind,
                "statement": statement.strip(),
                "artifact": artifact_path.relative_to(self.root).as_posix(),
                "artifact_sha256": (
                    sha256(artifact_path) if artifact_path.is_file() else None
                ),
                "depends_on": deps,
                "check": check if isinstance(check, str) else None,
                "checks": check_list,
                "obligation_hash": obligation_hash,
                "reviews": review_list,
                "producer_task_id": producer_task_id,
                "status": "candidate",
                "created_at": now(),
                "verified_at": None,
                "verification": None,
                "revocation": None,
            }
            data["schema"] = max(3, int(data.get("schema", 2)))
            data["nodes"][node_id] = record
            data["revision"] = int(data.get("revision", 0)) + 1
            validate_evidence_graph(data)
        return record

    def _review_records(self, node_id: str, node: dict) -> tuple[list[dict], list[str]]:
        records, errors = [], []
        for relative in node.get("reviews", []):
            path = safe_relative(self.root, relative)
            if not path.is_file():
                errors.append(f"独立审核缺失: {relative}")
                continue
            try:
                review = validate_review(read_json(path), path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if review["verdict"].upper() != "APPROVE":
                errors.append(f"审核拒绝: {relative}")
            checked = review.get("evidence_checked", [])
            if checked and node_id not in checked:
                errors.append(f"审核范围未覆盖证据 {node_id}: {relative}")
            contract = review.get("contract_hash")
            if contract and node.get("obligation_hash"):
                if contract != node["obligation_hash"]:
                    errors.append(f"审核合同哈希不匹配: {relative}")
            records.append({
                "path": relative,
                "sha256": sha256(path),
                "reviewer": review.get("reviewer"),
                "task_id": review.get("task_id"),
            })
        return records, errors

    def verify(self, node_id: str, timeout: int = 1800) -> dict:
        # Potentially long checks run outside the evidence lock.
        snapshot = self.data
        if node_id not in snapshot["nodes"]:
            raise ValueError(f"证据节点不存在: {node_id}")
        node = snapshot["nodes"][node_id]
        bad = [
            x for x in node["depends_on"]
            if snapshot["nodes"][x]["status"] != "verified"
        ]
        if bad:
            raise ValueError(f"依赖尚未验证: {bad}")
        artifact = safe_relative(self.root, node["artifact"])
        if not artifact.is_file():
            raise ValueError(f"证据文件不存在: {node['artifact']}")
        artifact_hash = sha256(artifact)
        configured_checks = node.get("checks")
        if configured_checks is None:
            configured_checks = [node["check"]] if node.get("check") else []
        check_records = run_checks(self.root, configured_checks)
        review_records, review_errors = self._review_records(node_id, node)
        failed = any(not item["ok"] for item in check_records) or bool(review_errors)
        verification = {
            "time": now(),
            "checks": check_records,
            "reviews": review_records,
            "review_errors": review_errors,
            "returncode": 1 if failed else 0,
            # V2 compatibility fields.
            "command": node.get("check"),
            "stdout_tail": "\n".join(
                item.get("stdout_tail", "") for item in check_records
            )[-4000:],
            "stderr_tail": (
                "\n".join(item.get("stderr_tail", "") for item in check_records)
                + "\n" + "\n".join(review_errors)
            )[-4000:],
        }
        with json_transaction(self.path, snapshot) as data:
            current = data["nodes"].get(node_id)
            if current is None:
                raise RuntimeError("验证期间证据节点被删除")
            for dep in current["depends_on"]:
                if data["nodes"][dep]["status"] != "verified":
                    raise RuntimeError(
                        f"dependency invalidated during verification: {dep}"
                    )
            if sha256(artifact) != artifact_hash:
                raise RuntimeError(f"artifact changed during verification: {node_id}")
            for field in (
                "artifact", "depends_on", "check", "checks",
                "obligation_hash", "reviews",
            ):
                if current.get(field) != node.get(field):
                    raise RuntimeError(f"验证期间节点定义发生变化: {node_id}")
            if failed:
                current.update({
                    "status": "rejected",
                    "verification": verification,
                })
            else:
                current.update({
                    "status": "verified",
                    "artifact_sha256": artifact_hash,
                    "verified_at": now(),
                    "verification": verification,
                    "revocation": None,
                })
            data["revision"] += 1
            result = dict(current)
        if failed:
            raise RuntimeError(f"证据验证失败: {node_id}")
        return result

    def revise(
        self,
        node_id: str,
        *,
        statement: str | None = None,
        artifact: str | None = None,
        depends: list[str] | None = None,
        checks: list | None = None,
        obligation_hash: str | None = None,
        reviews: list[str] | None = None,
        reason: str,
    ) -> dict:
        if not reason.strip():
            raise ValueError("修订原因不能为空")
        with json_transaction(
            self.path, {"schema": 3, "revision": 0, "nodes": {}}
        ) as data:
            if node_id not in data["nodes"]:
                raise ValueError(f"证据节点不存在: {node_id}")
            current = data["nodes"][node_id]
            reverse: dict[str, list[str]] = {}
            for key, node in data["nodes"].items():
                for dep in node["depends_on"]:
                    reverse.setdefault(dep, []).append(key)
            queue, downstream = deque(reverse.get(node_id, [])), []
            while queue:
                key = queue.popleft()
                if key in downstream:
                    continue
                downstream.append(key)
                queue.extend(reverse.get(key, []))
            for key in downstream:
                data["nodes"][key].update({
                    "status": "revoked",
                    "revocation": {
                        "time": now(), "root": node_id,
                        "reason": f"upstream revised: {reason.strip()}",
                    },
                })
            if artifact is not None:
                artifact_path = safe_relative(self.root, artifact)
                current["artifact"] = artifact_path.relative_to(
                    self.root
                ).as_posix()
            else:
                artifact_path = safe_relative(self.root, current["artifact"])
            if depends is not None:
                unknown = [x for x in depends if x not in data["nodes"]]
                if unknown:
                    raise ValueError(f"依赖节点不存在: {unknown}")
                current["depends_on"] = list(dict.fromkeys(depends))
            if statement is not None:
                current["statement"] = statement.strip()
            if checks is not None:
                current["checks"] = list(checks)
                current["check"] = None
            if obligation_hash is not None:
                current["obligation_hash"] = obligation_hash
            if reviews is not None:
                for relative in reviews:
                    safe_relative(self.root, relative)
                current["reviews"] = list(dict.fromkeys(reviews))
            current.update({
                "status": "candidate",
                "artifact_sha256": (
                    sha256(artifact_path) if artifact_path.is_file() else None
                ),
                "verified_at": None,
                "verification": None,
                "revocation": {
                    "time": now(), "root": node_id,
                    "reason": f"revised: {reason.strip()}",
                },
            })
            data["schema"] = max(3, int(data.get("schema", 2)))
            data["revision"] += 1
            validate_evidence_graph(data)
            result = dict(current)
        return {"node": result, "revoked_downstream": downstream}

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
            for review in node.get("verification", {}).get("reviews", []):
                path = safe_relative(self.root, review["path"])
                if not path.is_file() or sha256(path) != review["sha256"]:
                    errors.append(f"{node_id}: 验证审核文件缺失或变更")
        return errors

    def revoke(self, node_id: str, reason: str) -> list[str]:
        if not reason.strip():
            raise ValueError("撤销原因不能为空")
        with json_transaction(
            self.path, {"schema": 3, "revision": 0, "nodes": {}}
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
