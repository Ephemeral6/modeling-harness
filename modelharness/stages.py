"""Authoritative stage service with self-validating chained stamps."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .contracts import STAGES, validate_review, validate_stage_config
from .evidence import EvidenceGraph
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

    def validate_stamp(self, stage: str) -> list[str]:
        errors = []
        path = self.stamp_path(stage)
        try:
            stamp = read_json(path)
        except RuntimeError as exc:
            return [str(exc)]
        if not isinstance(stamp, dict):
            return [f"{stage}: 印章缺失或损坏"]
        if stamp.get("schema") != 2 or stamp.get("stage") != stage:
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
        graph = EvidenceGraph(self.project)
        nodes = graph.nodes
        for item in stamp.get("evidence", []):
            node = nodes.get(item.get("id"))
            if not node or node.get("status") != "verified":
                errors.append(f"{stage}: 印章证据已失效: {item.get('id')}")
            elif node.get("artifact_sha256") != item.get("sha256"):
                errors.append(f"{stage}: 印章证据哈希不匹配: {item.get('id')}")
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

    @staticmethod
    def _command_spec(value) -> tuple[list[str] | str, bool, int]:
        if isinstance(value, list) and all(isinstance(x, str) for x in value):
            return value, False, 1800
        if isinstance(value, dict):
            argv = value.get("argv")
            if not isinstance(argv, list) or not all(isinstance(x, str) for x in argv):
                raise ValueError("check.argv 必须是字符串数组")
            return argv, False, int(value.get("timeout", 1800))
        # v1 compatibility. New projects should use argv arrays.
        if isinstance(value, str):
            return value, True, 1800
        raise ValueError(f"非法检查命令: {value!r}")

    def gate(self, stage: str) -> dict:
        if stage != self.current():
            raise RuntimeError(f"当前不可签发 {stage}；有效前缀为 {self.valid_prefix()}")
        spec = self.config[stage]
        graph = EvidenceGraph(self.project)
        errors = graph.audit()
        nodes = graph.nodes
        for node_id in spec["requires"]:
            if nodes.get(node_id, {}).get("status") != "verified":
                errors.append(f"缺少 verified 证据: {node_id}")
        reviews = []
        for relative in spec["reviews"]:
            path = self.project / relative
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
            reviews.append({"path": relative, "sha256": sha256(path)})
        checks = []
        for raw in spec["checks"]:
            argv, shell, timeout = self._command_spec(raw)
            try:
                proc = subprocess.run(
                    argv, cwd=self.project, shell=shell, text=True,
                    capture_output=True, timeout=timeout,
                )
                item = {
                    "argv": argv, "legacy_shell": shell,
                    "returncode": proc.returncode,
                    "stdout_tail": (proc.stdout or "")[-2000:],
                    "stderr_tail": (proc.stderr or "")[-2000:],
                }
            except subprocess.TimeoutExpired:
                item = {"argv": argv, "legacy_shell": shell, "returncode": 124,
                        "stdout_tail": "", "stderr_tail": "timeout"}
            checks.append(item)
            if item["returncode"]:
                errors.append(f"机械检查失败: {argv}")
        if errors:
            raise RuntimeError("\n".join(errors))
        index = STAGES.index(stage)
        previous_hash = (
            sha256(self.stamp_path(STAGES[index - 1])) if index else None
        )
        record = {
            "schema": 2, "stage": stage, "name": spec["name"], "time": now(),
            "config_sha256": sha256(self.config_path),
            "previous_stamp_sha256": previous_hash,
            "evidence_revision": graph.data["revision"],
            "evidence": [
                {"id": item, "sha256": nodes[item]["artifact_sha256"]}
                for item in spec["requires"]
            ],
            "reviews": reviews, "checks": checks,
        }
        with file_lock(self.project / ".harness" / "stage", timeout=30):
            with file_lock(graph.path, timeout=30):
                # Evidence writers use the same lock. Recompute every signed input.
                fresh_graph = EvidenceGraph(self.project)
                fresh_nodes = fresh_graph.nodes
                if fresh_graph.data["revision"] != record["evidence_revision"]:
                    raise RuntimeError("evidence changed during gate signing")
                if fresh_graph.audit():
                    raise RuntimeError("evidence became invalid during gate signing")
                for item in record["evidence"]:
                    node = fresh_nodes.get(item["id"])
                    artifact = self.project / node["artifact"] if node else None
                    if (not node or node["status"] != "verified" or
                            not artifact.is_file() or
                            sha256(artifact) != item["sha256"]):
                        raise RuntimeError(f"signed evidence changed: {item['id']}")
                if sha256(self.config_path) != record["config_sha256"]:
                    raise RuntimeError("stage config changed during gate signing")
                for item in record["reviews"]:
                    path = self.project / item["path"]
                    if not path.is_file() or sha256(path) != item["sha256"]:
                        raise RuntimeError(f"review changed: {item['path']}")
                if stage != self.current():
                    raise RuntimeError("stage state changed during gate signing")
                write_json(self.stamp_path(stage), record)
        return record

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
                    "time": now(), "from_stage": stage,
                    "reason": reason, "stamps": moved,
                })
        return moved
