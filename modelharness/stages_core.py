"""Authoritative milestone service with self-validating chained stamps."""
from __future__ import annotations

import shutil
from pathlib import Path

from .checks import evaluate_acceptance, run_check
from .contracts import STAGES, validate_review, validate_stage_config
from .evidence import EvidenceGraph
from .method_packs import MethodPackRegistry
from .problem_graph import ProblemGraph
from .profiles import ProfileService
from .review_store import (
    identity_enforced,
    latest_review_path,
    relative_review_path,
    review_identity_errors,
)
from .storage import file_lock, read_json
from .util import now, sha256, write_json


class StageService:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.config_path = self.project / "config" / "stages.json"
        self.stamps = self.project / ".harness" / "stamps"

    @property
    def config(self) -> dict:
        data = read_json(self.config_path)
        if data is None:
            raise ValueError(f"阶段配置缺失: {self.config_path}")
        return validate_stage_config(data)

    def stamp_path(self, stage: str) -> Path:
        if stage not in STAGES:
            raise ValueError(f"非法阶段: {stage}")
        return self.stamps / f"{stage}.json"

    def _problem_metadata(self, stage: str) -> dict:
        graph = ProblemGraph(self.project)
        if not graph.exists:
            return {}
        names = {
            node.get("method_pack", "generic")
            for node in graph.nodes.values()
            if not node.get("superseded", False)
            and STAGES.index(node["milestone"]) <= STAGES.index(stage)
        }
        return {
            "problem_closure_sha256": graph.closure_hash(stage),
            "delivery_profile_sha256": ProfileService(
                self.project
            ).content_hash(),
            "method_pack_closure_sha256": MethodPackRegistry(
                self.project
            ).closure_hash(names),
        }

    def validate_stamp(self, stage: str) -> list[str]:
        errors = []
        path = self.stamp_path(stage)
        try:
            stamp = read_json(path)
        except RuntimeError as exc:
            return [str(exc)]
        if not isinstance(stamp, dict):
            return [f"{stage}: 印章缺失或损坏"]
        if stamp.get("schema") not in {2, 3} or stamp.get("stage") != stage:
            errors.append(f"{stage}: 印章 schema/stage 非法")
        expected_config = sha256(self.config_path)
        if stamp.get("config_sha256") != expected_config:
            errors.append(f"{stage}: 阶段配置在落章后发生变化")
        index = STAGES.index(stage)
        if index:
            previous = self.stamp_path(STAGES[index - 1])
            if not previous.is_file():
                errors.append(f"{stage}: 上游印章缺失")
            elif stamp.get("previous_stamp_sha256") != sha256(previous):
                errors.append(f"{stage}: 上游印章链不匹配")
        if stamp.get("schema") == 3:
            try:
                expected_meta = self._problem_metadata(stage)
                for field, value in expected_meta.items():
                    if stamp.get(field) != value:
                        errors.append(f"{stage}: {field} 已变化")
            except (ValueError, RuntimeError) as exc:
                errors.append(f"{stage}: V3 合同审计失败: {exc}")
            try:
                acceptance = self._node_acceptance(
                    stage, include_checks=False
                )
                if acceptance["records"] and not acceptance["ok"]:
                    errors.append(f"{stage}: 问题节点验收已失效")
            except (ValueError, RuntimeError) as exc:
                errors.append(f"{stage}: 问题节点验收审计失败: {exc}")
        graph = EvidenceGraph(self.project)
        nodes = graph.nodes
        for item in stamp.get("evidence", []):
            node = nodes.get(item.get("id"))
            if not node or node.get("status") != "verified":
                errors.append(f"{stage}: 印章证据已失效: {item.get('id')}")
            elif node.get("artifact_sha256") != item.get("sha256"):
                errors.append(f"{stage}: 印章证据哈希不匹配: {item.get('id')}")
            elif (
                item.get("obligation_hash") is not None
                and node.get("obligation_hash") != item["obligation_hash"]
            ):
                errors.append(f"{stage}: 证据合同哈希不匹配: {item.get('id')}")
        errors.extend(f"{stage}: {item}" for item in graph.audit())
        for item in stamp.get("reviews", []):
            review = self.project / item["path"]
            if not review.is_file() or sha256(review) != item.get("sha256"):
                errors.append(f"{stage}: 审核文件缺失或变更: {item['path']}")
        return errors

    def valid_prefix(self) -> list[str]:
        valid = []
        for stage in STAGES:
            if not self.stamp_path(stage).is_file():
                break
            if self.validate_stamp(stage):
                break
            valid.append(stage)
        return valid

    def current(self) -> str | None:
        prefix = self.valid_prefix()
        return None if len(prefix) == len(STAGES) else STAGES[len(prefix)]

    def _required_evidence(self, stage: str) -> list[tuple[str, str | None, bool]]:
        required = [
            (node_id, None, False)
            for node_id in self.config[stage]["requires"]
        ]
        graph = ProblemGraph(self.project)
        if graph.exists:
            required.extend(graph.milestone_outputs(stage))
        result = {}
        for evidence_id, contract, enforce in required:
            existing = result.get(evidence_id)
            if existing and existing[0] and contract and existing[0] != contract:
                raise RuntimeError(
                    f"同一阶段对 {evidence_id} 声明了冲突合同"
                )
            result[evidence_id] = (
                contract or (existing[0] if existing else None),
                enforce or (existing[1] if existing else False),
            )
        return [
            (evidence_id, contract, enforce)
            for evidence_id, (contract, enforce) in result.items()
        ]

    def _stage_artifacts(
        self,
        stage: str,
        required: list[tuple[str, str | None, bool]],
        nodes: dict,
    ) -> list[str]:
        """本阶段独立审核实际在批的工件，用于判定审核者是否就是生成者。"""
        artifacts = []
        for node_id, _contract, _enforce in required:
            artifact = (nodes.get(node_id) or {}).get("artifact")
            if artifact:
                artifacts.append(artifact)
        graph = ProblemGraph(self.project)
        if graph.exists:
            for node in graph.nodes.values():
                if node.get("milestone") != stage or node.get("superseded"):
                    continue
                for output in node.get("outputs", []):
                    evidence_id = output.get("evidence_id")
                    artifact = (nodes.get(evidence_id) or {}).get("artifact")
                    if artifact:
                        artifacts.append(artifact)
        return sorted(set(artifacts))

    def _node_acceptance(
        self, stage: str, *, include_checks: bool = True
    ) -> dict:
        graph = ProblemGraph(self.project)
        if not graph.exists:
            return {
                "execution_status": "not_run",
                "verdict": "unassessed",
                "authority": "machine",
                "ok": False,
                "records": [],
            }
        acceptance = []
        for node in graph.nodes.values():
            if node.get("milestone") != stage or node.get("superseded", False):
                continue
            items = node.get("acceptance", [])
            if items in ({}, None):
                continue
            if not isinstance(items, list):
                raise ValueError("问题节点 acceptance 必须是数组")
            acceptance.extend(
                item for item in items
                if include_checks
                or not isinstance(item, dict)
                or item.get("kind") != "check"
            )
        return evaluate_acceptance(self.project, acceptance)

    def gate(self, stage: str) -> dict:
        if stage != self.current():
            raise RuntimeError(
                f"当前不可签发 {stage}；有效前缀为 {self.valid_prefix()}"
            )
        spec = self.config[stage]
        graph = EvidenceGraph(self.project)
        errors = graph.audit()
        nodes = graph.nodes
        required = self._required_evidence(stage)
        for node_id, contract, enforce in required:
            node = nodes.get(node_id)
            if not node or node.get("status") != "verified":
                errors.append(f"缺少 verified 证据: {node_id}")
            elif enforce and node.get("obligation_hash") != contract:
                errors.append(f"证据未绑定当前问题合同: {node_id}")
        node_acceptance = self._node_acceptance(stage)
        if node_acceptance["records"] and not node_acceptance["ok"]:
            for item in node_acceptance["records"]:
                if item.get("ok") is not True:
                    errors.append(
                        f"问题节点验收失败: {item.get('kind')}: "
                        f"{item.get('errors', item.get('error', ''))}"
                    )
        reviews = []
        stage_artifacts = self._stage_artifacts(stage, required, nodes)
        enforce_reviewer = identity_enforced(self.project)
        for relative in spec["reviews"]:
            path = (
                latest_review_path(self.project, relative)
                or self.project / relative
            )
            if not path.is_file():
                errors.append(f"独立审核缺失: {relative}")
                continue
            try:
                review = validate_review(read_json(path), path)
                if review["verdict"].upper() != "APPROVE":
                    errors.append(f"审核拒绝: {relative}")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            stored = relative_review_path(self.project, path)
            # 硬不变量 5：APPROVE 之外还要问「谁批的」。生成者自签的
            # APPROVE 不得开门，否则阶段 gate 只是一个 verdict 字符串检查。
            errors.extend(review_identity_errors(
                self.project,
                review,
                stored,
                stage_artifacts,
                label=f"{stage} 阶段独立审核",
                require_reviewer=enforce_reviewer,
            ))
            reviews.append({
                "path": stored,
                "sha256": sha256(path),
            })
        checks = [run_check(self.project, raw) for raw in spec["checks"]]
        for item in checks:
            if not item["ok"]:
                errors.append(f"机械检查失败: {item['argv']}")
        problem_meta = {}
        if ProblemGraph(self.project).exists:
            pack_errors = MethodPackRegistry(self.project).audit()
            errors.extend(pack_errors)
            problem_meta = self._problem_metadata(stage)
        if errors:
            raise RuntimeError("\n".join(errors))
        index = STAGES.index(stage)
        previous_hash = (
            sha256(self.stamp_path(STAGES[index - 1])) if index else None
        )
        record = {
            "schema": 3 if problem_meta else 2,
            "stage": stage,
            "name": spec["name"],
            "time": now(),
            "config_sha256": sha256(self.config_path),
            "previous_stamp_sha256": previous_hash,
            "evidence_revision": graph.data["revision"],
            "evidence": [
                {
                    "id": node_id,
                    "sha256": nodes[node_id]["artifact_sha256"],
                    "obligation_hash": (
                        nodes[node_id].get("obligation_hash")
                        if enforce else None
                    ),
                }
                for node_id, _contract, enforce in required
            ],
            "reviews": reviews,
            "checks": checks,
            "node_acceptance": node_acceptance,
            **problem_meta,
        }
        with file_lock(self.project / ".harness" / "stage", timeout=30):
            with file_lock(graph.path, timeout=30):
                fresh_graph = EvidenceGraph(self.project)
                fresh_nodes = fresh_graph.nodes
                if fresh_graph.data["revision"] != record["evidence_revision"]:
                    raise RuntimeError("evidence changed during gate signing")
                if fresh_graph.audit():
                    raise RuntimeError("evidence became invalid during gate signing")
                for item in record["evidence"]:
                    node = fresh_nodes.get(item["id"])
                    artifact = self.project / node["artifact"] if node else None
                    if (
                        not node
                        or node["status"] != "verified"
                        or not artifact.is_file()
                        or sha256(artifact) != item["sha256"]
                    ):
                        raise RuntimeError(f"signed evidence changed: {item['id']}")
                    if (
                        item.get("obligation_hash") is not None
                        and node.get("obligation_hash") != item["obligation_hash"]
                    ):
                        raise RuntimeError(
                            f"signed obligation changed: {item['id']}"
                        )
                if sha256(self.config_path) != record["config_sha256"]:
                    raise RuntimeError("stage config changed during gate signing")
                for item in record["reviews"]:
                    review_path = self.project / item["path"]
                    if (
                        not review_path.is_file()
                        or sha256(review_path) != item["sha256"]
                    ):
                        raise RuntimeError(f"review changed: {item['path']}")
                if problem_meta != self._problem_metadata(stage):
                    raise RuntimeError("problem/profile/method contract changed")
                if stage != self.current():
                    raise RuntimeError("stage state changed during gate signing")
                write_json(self.stamp_path(stage), record)
        return record

    def earliest_stage_for_evidence(self, evidence_ids: list[str]) -> str:
        targets = set(evidence_ids)
        for stage in STAGES:
            stamp = read_json(self.stamp_path(stage))
            if isinstance(stamp, dict) and any(
                item.get("id") in targets
                for item in stamp.get("evidence", [])
            ):
                return stage
        graph = ProblemGraph(self.project)
        if graph.exists:
            candidates = [
                node["milestone"]
                for node in graph.nodes.values()
                if any(
                    output["evidence_id"] in targets
                    for output in node["outputs"]
                )
            ]
            if candidates:
                return min(candidates, key=STAGES.index)
        return self.current() or "s6"

    def invalidate_for_evidence(
        self, evidence_ids: list[str], reason: str
    ) -> list[str]:
        return self.invalidate(
            self.earliest_stage_for_evidence(evidence_ids), reason
        )

    def invalidate(self, stage: str, reason: str) -> list[str]:
        if stage not in STAGES:
            raise ValueError(f"非法阶段: {stage}")
        archive = (
            self.project / ".harness" / "archive" /
            now().replace(":", "-")
        )
        moved = []
        with file_lock(self.project / ".harness" / "stage", timeout=30):
            for item in STAGES[STAGES.index(stage):]:
                path = self.stamp_path(item)
                if path.exists():
                    archive.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(archive / path.name))
                    moved.append(item)
            if moved:
                write_json(archive / "invalidation.json", {
                    "time": now(),
                    "from_stage": stage,
                    "reason": reason,
                    "stamps": moved,
                })
        return moved
