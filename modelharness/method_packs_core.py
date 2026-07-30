"""Versioned local-research method packs."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .problem_graph import canonical_hash
from .storage import atomic_write_json, read_json
from .util import sha256


def validate_method_pack(data: Any, path: Path | None = None) -> dict:
    where = f": {path}" if path else ""
    if not isinstance(data, dict):
        raise ValueError(f"method pack 必须是对象{where}")
    for field in ("name", "version", "description"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise ValueError(f"method pack.{field} 缺失{where}")
    task_types = data.get("task_types", [])
    if not isinstance(task_types, list) or not all(
        isinstance(x, str) and x for x in task_types
    ):
        raise ValueError(f"method pack.task_types 非法{where}")
    protocol = data.get("protocol", [])
    if not isinstance(protocol, list) or not all(
        isinstance(x, str) and x for x in protocol
    ):
        raise ValueError(f"method pack.protocol 非法{where}")
    for field in ("required_tests", "fallbacks"):
        if not isinstance(data.get(field, []), list):
            raise ValueError(f"method pack.{field} 必须是数组{where}")
    tool_policy = data.get("tool_policy")
    if tool_policy is not None:
        if not isinstance(tool_policy, dict):
            raise ValueError(f"method pack.tool_policy 必须是对象{where}")
        if not isinstance(tool_policy.get("decision_required", False), bool):
            raise ValueError(
                f"method pack.tool_policy.decision_required 非法{where}"
            )
        for field in (
            "required_capabilities", "preferred_capabilities",
            "validation_protocols",
        ):
            value = tool_policy.get(field, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise ValueError(
                    f"method pack.tool_policy.{field} 非法{where}"
                )
    return data


class MethodPackRegistry:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "config" / "method_packs"
        self.lock_path = self.root / "config" / "method_pack_lock.json"

    def list(self) -> list[dict]:
        records = []
        if not self.directory.is_dir():
            return records
        for path in sorted(self.directory.glob("*.json")):
            data = validate_method_pack(read_json(path), path)
            records.append({
                "name": data["name"],
                "version": data["version"],
                "task_types": data["task_types"],
                "path": path.relative_to(self.root).as_posix(),
                "sha256": sha256(path),
            })
        return records

    def get(self, name: str) -> dict:
        candidates = [
            path for path in self.directory.glob("*.json")
            if read_json(path, {}).get("name") == name
        ]
        if len(candidates) != 1:
            raise ValueError(f"方法包不存在或重名: {name}")
        return validate_method_pack(read_json(candidates[0]), candidates[0])

    def match(self, task_type: str, preferred: str | None = None) -> dict:
        if preferred:
            return self.get(preferred)
        matches = []
        for record in self.list():
            pack = self.get(record["name"])
            if task_type in pack["task_types"] or "*" in pack["task_types"]:
                matches.append(pack)
        if not matches:
            raise ValueError(f"没有方法包可处理 task_type={task_type}")
        matches.sort(key=lambda x: ("*" in x["task_types"], x["name"]))
        return matches[0]

    def expected_lock(self) -> dict:
        return {
            "schema": 1,
            "packs": {
                item["name"]: {
                    "version": item["version"],
                    "path": item["path"],
                    "sha256": item["sha256"],
                }
                for item in self.list()
            },
        }

    def refresh_lock(self) -> dict:
        record = self.expected_lock()
        atomic_write_json(self.lock_path, record)
        return record

    def audit(self) -> list[str]:
        expected = self.expected_lock()
        actual = read_json(self.lock_path)
        if actual is None:
            return ["method_pack_lock.json 缺失"]
        return [] if actual == expected else ["方法包内容与锁文件不匹配"]

    def closure_hash(self, names: set[str]) -> str:
        lock = self.expected_lock()["packs"]
        selected = {name: lock.get(name) for name in sorted(names)}
        if any(value is None for value in selected.values()):
            missing = [key for key, value in selected.items() if value is None]
            raise ValueError(f"问题图引用未知方法包: {missing}")
        return canonical_hash(selected)
