"""Content-addressed evidence graph with freshness and cascade semantics."""
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
from .problem_graph import canonical_hash
from .storage import json_transaction, read_json
from .util import now, sha256

KINDS = EVIDENCE_KINDS
STATUSES = {
    "candidate", "verified", "rejected", "revoked", "invalidated",
}


class EvidenceGraph:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / ".harness" / "evidence.json"
        self.last_cascade = {"tasks": [], "milestones": []}

    @property
    def data(self) -> dict:
        data = read_json(
            self.path, {"schema": 3, "revision": 0, "nodes": {}}
        )
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
            raise ValueError(f"invalid evidence id: {node_id}")
        if kind not in KINDS:
            raise ValueError(f"unknown evidence kind: {kind}")
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
                raise ValueError(f"evidence already exists: {node_id}")
            unknown = [item for item in deps if item not in data["nodes"]]
            if unknown:
                raise ValueError(f"unknown evidence dependencies: {unknown}")
            record = {
                "id": node_id,
                "kind": kind,
                "statement": statement.strip(),
                "artifact": artifact_path.relative_to(
                    self.root
                ).as_posix(),
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
                "freshness": (
                    "valid" if artifact_path.is_file() else "missing"
                ),
                "created_at": now(),
                "verified_at": None,
                "verification": None,
                "revocation": None,
            }
            data["nodes"][node_id] = record
            data["revision"] = int(data.get("revision", 0)) + 1
            validate_evidence_graph(data)
        return record

    def _review_path(
        self, node_id: str, node: dict, relative: str
    ) -> Path:
        return safe_relative(self.root, relative)

    def _review_records(
        self, node_id: str, node: dict
    ) -> tuple[list[dict], list[str]]:
        records, errors = [], []
        for relative in node.get("reviews", []):
            path = self._review_path(node_id, node, relative)
            actual = (
                path.resolve().relative_to(self.root.resolve()).as_posix()
            )
            if not path.is_file():
                errors.append(f"independent review missing: {relative}")
                continue
            try:
                review = validate_review(read_json(path), path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if review["verdict"].upper() != "APPROVE":
                errors.append(f"review rejected: {relative}")
            checked = review.get("evidence_checked", [])
            if checked and node_id not in checked:
                errors.append(
                    f"review does not cover evidence {node_id}: {relative}"
                )
            contract = review.get("contract_hash")
            if contract and node.get("obligation_hash"):
                if contract != node["obligation_hash"]:
                    errors.append(f"review contract mismatch: {relative}")
            records.append({
                "path": actual,
                "sha256": sha256(path),
                "reviewer": review.get("reviewer"),
                "task_id": review.get("task_id"),
            })
        return records, errors

    @staticmethod
    def _verification_policy(node: dict) -> str:
        return canonical_hash({
            "checks": node.get("checks") or [],
            "reviews": node.get("reviews") or [],
            "obligation_hash": node.get("obligation_hash"),
            "depends_on": node.get("depends_on") or [],
        })

    def freshness(self, node_id: str, data: dict | None = None) -> str:
        graph = data or self.data
        node = graph["nodes"].get(node_id)
        if node is None:
            return "missing"
        artifact = safe_relative(self.root, node["artifact"])
        if not artifact.is_file():
            return "missing"
        if node.get("status") != "verified":
            return "valid"
        if sha256(artifact) != node.get("artifact_sha256"):
            return "tampered"
        verification = node.get("verification") or {}
        binding = verification.get("binding") or {}
        if (
            binding.get("verification_policy_hash")
            and binding["verification_policy_hash"]
            != self._verification_policy(node)
        ):
            return "stale"
        expected_inputs = binding.get("input_evidence_hashes", {})
        for dep in node.get("depends_on", []):
            dep_node = graph["nodes"].get(dep)
            if not dep_node or dep_node.get("status") != "verified":
                return "stale"
            expected = expected_inputs.get(dep)
            if expected and expected != dep_node.get("artifact_sha256"):
                return "stale"
            if self.freshness(dep, graph) != "valid":
                return "stale"
        for review in verification.get("reviews", []):
            path = safe_relative(self.root, review["path"])
            if not path.is_file() or sha256(path) != review["sha256"]:
                return "stale"
        return "valid"

    def verify(self, node_id: str, timeout: int = 1800) -> dict:
        snapshot = self.data
        if node_id not in snapshot["nodes"]:
            raise ValueError(f"evidence does not exist: {node_id}")
        node = snapshot["nodes"][node_id]
        bad = [
            dep for dep in node["depends_on"]
            if snapshot["nodes"][dep]["status"] != "verified"
            or self.freshness(dep, snapshot) != "valid"
        ]
        if bad:
            raise ValueError(f"dependencies are not currently verified: {bad}")
        artifact = safe_relative(self.root, node["artifact"])
        if not artifact.is_file():
            raise ValueError(f"evidence artifact does not exist: {node['artifact']}")
        artifact_hash = sha256(artifact)
        configured_checks = node.get("checks")
        if configured_checks is None:
            configured_checks = (
                [node["check"]] if node.get("check") else []
            )
        check_records = run_checks(
            self.root, configured_checks
        )
        review_records, review_errors = self._review_records(node_id, node)
        failed = (
            any(not item["ok"] for item in check_records)
            or bool(review_errors)
        )
        verification = {
            "time": now(),
            "execution_status": "completed",
            "verdict": "fail" if failed else "pass",
            "authority": (
                "hybrid" if review_records and check_records
                else "human" if review_records else "machine"
            ),
            "freshness": "valid",
            "checks": check_records,
            "reviews": review_records,
            "review_errors": review_errors,
            "returncode": 1 if failed else 0,
            "command": node.get("check"),
            "stdout_tail": "\n".join(
                item.get("stdout_tail", "") for item in check_records
            )[-4000:],
            "stderr_tail": (
                "\n".join(
                    item.get("stderr_tail", "") for item in check_records
                )
                + "\n" + "\n".join(review_errors)
            )[-4000:],
            "binding": {
                "artifact_hashes": {node["artifact"]: artifact_hash},
                "input_evidence_hashes": {
                    dep: snapshot["nodes"][dep].get("artifact_sha256")
                    for dep in node["depends_on"]
                },
                "review_hashes": {
                    item["path"]: item["sha256"]
                    for item in review_records
                },
                "verification_policy_hash": self._verification_policy(node),
                "producer_task_id": node.get("producer_task_id"),
                "review_task_ids": [
                    item.get("task_id") for item in review_records
                    if item.get("task_id")
                ],
            },
        }
        with json_transaction(self.path, snapshot) as data:
            current = data["nodes"].get(node_id)
            if current is None:
                raise RuntimeError("evidence was removed during verification")
            for dep in current["depends_on"]:
                if data["nodes"][dep]["status"] != "verified":
                    raise RuntimeError(
                        f"dependency invalidated during verification: {dep}"
                    )
            if sha256(artifact) != artifact_hash:
                raise RuntimeError(
                    f"artifact changed during verification: {node_id}"
                )
            for field in (
                "artifact", "depends_on", "check", "checks",
                "obligation_hash", "reviews", "producer_task_id",
            ):
                if current.get(field) != node.get(field):
                    raise RuntimeError(
                        f"evidence definition changed during verification: "
                        f"{node_id}"
                    )
            if failed:
                current.update({
                    "status": "rejected",
                    "freshness": "valid",
                    "verification": verification,
                })
            else:
                current.update({
                    "status": "verified",
                    "freshness": "valid",
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

    @staticmethod
    def _downstream(
        data: dict, node_id: str, include_root: bool
    ) -> list[str]:
        reverse: dict[str, list[str]] = {}
        for key, node in data["nodes"].items():
            for dep in node["depends_on"]:
                reverse.setdefault(dep, []).append(key)
        queue = deque([node_id] if include_root else reverse.get(node_id, []))
        affected = []
        while queue:
            current = queue.popleft()
            if current in affected:
                continue
            affected.append(current)
            queue.extend(reverse.get(current, []))
        return affected

    def _propagate(
        self, evidence_ids: list[str], reason: str
    ) -> dict:
        if not evidence_ids:
            return {"tasks": [], "milestones": []}
        from .stages import StageService
        from .workflow import WorkflowEngine

        tasks = WorkflowEngine(self.root).invalidate_for_evidence(
            evidence_ids, reason
        )
        milestones = StageService(self.root).invalidate_for_evidence(
            evidence_ids, reason
        )
        self.last_cascade = {
            "tasks": tasks,
            "milestones": milestones,
        }
        return self.last_cascade

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
            raise ValueError("revision reason cannot be empty")
        material_change = False
        with json_transaction(
            self.path, {"schema": 3, "revision": 0, "nodes": {}}
        ) as data:
            if node_id not in data["nodes"]:
                raise ValueError(f"evidence does not exist: {node_id}")
            current = data["nodes"][node_id]
            artifact_value = (
                current["artifact"] if artifact is None else
                safe_relative(self.root, artifact).relative_to(self.root).as_posix()
            )
            material_change = any((
                statement is not None and statement.strip() != current["statement"],
                artifact_value != current["artifact"],
                depends is not None and list(dict.fromkeys(depends)) != current["depends_on"],
                checks is not None and list(checks) != current.get("checks", []),
                obligation_hash is not None and obligation_hash != current.get("obligation_hash"),
                reviews is not None and list(dict.fromkeys(reviews)) != current.get("reviews", []),
            ))
            downstream = self._downstream(data, node_id, False) if material_change else []
            for key in downstream:
                data["nodes"][key].update({
                    "status": "invalidated",
                    "freshness": "stale",
                    "revocation": {
                        "time": now(),
                        "root": node_id,
                        "reason": f"upstream revised: {reason.strip()}",
                    },
                })
            if artifact is not None:
                artifact_path = safe_relative(self.root, artifact)
                current["artifact"] = artifact_path.relative_to(
                    self.root
                ).as_posix()
            else:
                artifact_path = safe_relative(
                    self.root, current["artifact"]
                )
            if depends is not None:
                unknown = [
                    item for item in depends if item not in data["nodes"]
                ]
                if unknown:
                    raise ValueError(
                        f"unknown evidence dependencies: {unknown}"
                    )
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
                "freshness": (
                    "valid" if artifact_path.is_file() else "missing"
                ),
                "artifact_sha256": (
                    sha256(artifact_path)
                    if artifact_path.is_file() else None
                ),
                "verified_at": None,
                "verification": None,
                "revocation": {
                    "time": now(),
                    "root": node_id,
                    "reason": f"revised: {reason.strip()}",
                },
            })
            data["revision"] += 1
            validate_evidence_graph(data)
            result = dict(current)
        cascade = (
            self._propagate(
                [node_id, *downstream],
                f"evidence {node_id} revised: {reason.strip()}",
            )
            if material_change
            else {"tasks": [], "milestones": []}
        )
        return {
            "node": result,
            "revoked_downstream": downstream,
            "invalidated_downstream": downstream,
            "cascade": cascade,
        }

    def audit(self) -> list[str]:
        data = self.data
        errors: list[str] = []
        for node_id, node in data["nodes"].items():
            if node["status"] != "verified":
                continue
            state = self.freshness(node_id, data)
            if state != "valid":
                errors.append(f"{node_id}: freshness={state}")
        return errors

    def freshness_report(self) -> dict[str, str]:
        data = self.data
        return {
            node_id: self.freshness(node_id, data)
            for node_id in data["nodes"]
        }

    def revoke(self, node_id: str, reason: str) -> list[str]:
        if not reason.strip():
            raise ValueError("revocation reason cannot be empty")
        with json_transaction(
            self.path, {"schema": 3, "revision": 0, "nodes": {}}
        ) as data:
            if node_id not in data["nodes"]:
                raise ValueError(f"evidence does not exist: {node_id}")
            affected = self._downstream(data, node_id, True)
            for key in affected:
                is_root = key == node_id
                data["nodes"][key].update({
                    "status": "revoked" if is_root else "invalidated",
                    "freshness": "stale",
                    "revocation": {
                        "time": now(),
                        "root": node_id,
                        "reason": reason.strip(),
                    },
                })
            data["revision"] += 1
        self._propagate(
            affected,
            f"evidence {node_id} revoked: {reason.strip()}",
        )
        return affected

    def verified(self, kinds: set[str] | None = None) -> list[dict]:
        data = self.data
        return [
            node for node_id, node in data["nodes"].items()
            if node["status"] == "verified"
            and self.freshness(node_id, data) == "valid"
            and (not kinds or node["kind"] in kinds)
        ]
