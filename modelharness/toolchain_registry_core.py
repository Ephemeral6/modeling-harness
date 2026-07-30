"""Versioned computation-tool registry and environment probes."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .problem_graph import canonical_hash
from .storage import atomic_write_json, read_json
from .util import now, sha256


TOOL_RISKS = {"local", "network", "commercial"}
TOOL_KINDS = {"python_module", "command"}
_VERSION_PART = re.compile(r"\d+")


def _version_tuple(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    return tuple(int(item) for item in _VERSION_PART.findall(value))


def _compare_version(left: str | None, operator: str, right: str) -> bool:
    a, b = _version_tuple(left), _version_tuple(right)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return {
        "<": a < b,
        "<=": a <= b,
        "==": a == b,
        "!=": a != b,
        ">=": a >= b,
        ">": a > b,
    }.get(operator, False)


def validate_catalog(data: Any, path: Path | None = None) -> dict:
    where = f": {path}" if path else ""
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError(f"tool catalog.schema 必须为 1{where}")
    tools = data.get("tools")
    if not isinstance(tools, dict) or not tools:
        raise ValueError(f"tool catalog.tools 必须是非空对象{where}")
    for tool_id, tool in tools.items():
        if not isinstance(tool_id, str) or not tool_id:
            raise ValueError(f"tool id 非法{where}")
        if not isinstance(tool, dict):
            raise ValueError(f"tool 必须是对象: {tool_id}{where}")
        if tool.get("kind") not in TOOL_KINDS:
            raise ValueError(f"tool.kind 非法: {tool_id}{where}")
        detect = tool.get("detect")
        if not isinstance(detect, dict):
            raise ValueError(f"tool.detect 缺失: {tool_id}{where}")
        key = "module" if tool["kind"] == "python_module" else "command"
        if not isinstance(detect.get(key), str) or not detect[key]:
            raise ValueError(f"tool.detect.{key} 缺失: {tool_id}{where}")
        for field in ("capabilities", "task_types", "runners"):
            value = tool.get(field, [])
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item for item in value
            ):
                raise ValueError(f"tool.{field} 非法: {tool_id}{where}")
        if tool.get("risk", "local") not in TOOL_RISKS:
            raise ValueError(f"tool.risk 非法: {tool_id}{where}")
        if not isinstance(tool.get("priority", 0), (int, float)):
            raise ValueError(f"tool.priority 非法: {tool_id}{where}")
        if not isinstance(tool.get("cost", 1), (int, float)):
            raise ValueError(f"tool.cost 非法: {tool_id}{where}")
        rules = tool.get("compatibility", [])
        if not isinstance(rules, list):
            raise ValueError(f"tool.compatibility 非法: {tool_id}{where}")
    return data


def validate_autonomy(data: Any, path: Path | None = None) -> dict:
    where = f": {path}" if path else ""
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError(f"tool autonomy.schema 必须为 1{where}")
    if data.get("mode") not in {"agent_choice", "disabled", "required"}:
        raise ValueError(f"tool autonomy.mode 非法{where}")
    risks = data.get("autonomous_risks", [])
    if not isinstance(risks, list) or any(x not in TOOL_RISKS for x in risks):
        raise ValueError(f"tool autonomy.autonomous_risks 非法{where}")
    if not isinstance(data.get("max_timeout_seconds", 7200), int):
        raise ValueError(f"tool autonomy.max_timeout_seconds 非法{where}")
    for field in (
        "allow_skip", "allow_install", "require_reason",
        "require_reproducible_run", "require_validation",
    ):
        if not isinstance(data.get(field), bool):
            raise ValueError(f"tool autonomy.{field} 非法{where}")
    return data


def validate_environment_profile(data: Any, path: Path | None = None) -> dict:
    where = f": {path}" if path else ""
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError(f"tool environment.schema 必须为 1{where}")
    if not isinstance(data.get("name"), str) or not data["name"]:
        raise ValueError(f"tool environment.name 缺失{where}")
    for field in ("required_tools", "optional_tools"):
        if not isinstance(data.get(field, []), list) or not all(
            isinstance(x, str) and x for x in data.get(field, [])
        ):
            raise ValueError(f"tool environment.{field} 非法{where}")
    return data


class ToolRegistry:
    """Project-local, lockable catalog with read-only capability discovery."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "config" / "tools"
        self.catalog_path = self.directory / "catalog.json"
        self.catalog_lock_path = self.directory / "catalog.lock.json"
        self.environment_lock_path = self.directory / "environment.lock.json"
        self.autonomy_path = self.root / "config" / "tool_autonomy.json"
        self.environment_directory = (
            self.root / "config" / "tool_environments"
        )

    @property
    def catalog(self) -> dict:
        data = read_json(self.catalog_path)
        if data is None:
            raise ValueError("计算工具目录缺失；请重新初始化或迁移项目")
        return validate_catalog(data, self.catalog_path)

    @property
    def autonomy(self) -> dict:
        data = read_json(self.autonomy_path)
        if data is None:
            raise ValueError("tool_autonomy.json 缺失")
        return validate_autonomy(data, self.autonomy_path)

    def get(self, tool_id: str) -> dict:
        tool = self.catalog["tools"].get(tool_id)
        if tool is None:
            raise ValueError(f"未知计算工具: {tool_id}")
        return {"id": tool_id, **tool}

    def list(self) -> list[dict]:
        return [self.get(tool_id) for tool_id in sorted(self.catalog["tools"])]

    def expected_catalog_lock(self) -> dict:
        return {
            "schema": 1,
            "catalog": "config/tools/catalog.json",
            "sha256": sha256(self.catalog_path),
        }

    def refresh_catalog_lock(self) -> dict:
        record = self.expected_catalog_lock()
        atomic_write_json(self.catalog_lock_path, record)
        return record

    def catalog_hash(self) -> str:
        return canonical_hash({
            "catalog": self.expected_catalog_lock(),
            "autonomy_sha256": sha256(self.autonomy_path),
        })

    def audit_catalog(self) -> list[str]:
        try:
            expected = self.expected_catalog_lock()
            actual = read_json(self.catalog_lock_path)
            validate_autonomy(read_json(self.autonomy_path), self.autonomy_path)
        except (ValueError, RuntimeError, OSError) as exc:
            return [str(exc)]
        return [] if actual == expected else ["计算工具目录与锁文件不匹配"]

    @staticmethod
    def _module_version(tool: dict) -> str | None:
        distribution = tool["detect"].get(
            "distribution", tool["detect"]["module"]
        )
        try:
            return importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            return None

    def probe(self, tool_id: str) -> dict:
        tool = self.get(tool_id)
        detect = tool["detect"]
        executable = None
        version = None
        if tool["kind"] == "python_module":
            available = importlib.util.find_spec(detect["module"]) is not None
            version = self._module_version(tool) if available else None
            executable = sys.executable if available else None
        else:
            executable = shutil.which(detect["command"])
            available = executable is not None
            version = self._command_version(tool, executable) if available else None
        record = {
            "id": tool_id,
            "available": available,
            "version": version,
            "executable": executable,
            "kind": tool["kind"],
            "risk": tool.get("risk", "local"),
            "capabilities": tool.get("capabilities", []),
            "warnings": [],
        }
        if available:
            record["warnings"].extend(self._compatibility_warnings(tool))
        return record

    @staticmethod
    def _command_version(tool: dict, executable: str | None) -> str | None:
        if not executable:
            return None
        raw = tool.get("version_argv")
        if not isinstance(raw, list) or not raw:
            return None
        argv = [executable if item == "{executable}" else item for item in raw]
        try:
            proc = subprocess.run(
                argv, text=True, capture_output=True, timeout=10, shell=False
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return output.splitlines()[0][:200] if output else None

    def _compatibility_warnings(self, tool: dict) -> list[str]:
        warnings = []
        for rule in tool.get("compatibility", []):
            other_id = rule.get("with")
            if not isinstance(other_id, str) or other_id not in self.catalog["tools"]:
                continue
            other = self.get(other_id)
            if other["kind"] == "python_module":
                module = other["detect"]["module"]
                if importlib.util.find_spec(module) is None:
                    continue
                version = self._module_version(other)
            else:
                executable = shutil.which(other["detect"]["command"])
                if not executable:
                    continue
                version = self._command_version(other, executable)
            operator = str(rule.get("operator", "=="))
            target = str(rule.get("version", ""))
            compatible = _compare_version(version, operator, target)
            if not compatible:
                warnings.append(str(
                    rule.get(
                        "message",
                        f"{other_id}={version} 不满足 {operator}{target}",
                    )
                ))
        return warnings

    def snapshot(self) -> dict:
        tools = {item["id"]: item for item in map(self.probe, self.catalog["tools"])}
        stable = {
            tool_id: {
                "available": item["available"],
                "version": item["version"],
                "executable": item["executable"],
                "warnings": item["warnings"],
            }
            for tool_id, item in tools.items()
        }
        return {
            "schema": 1,
            "time": now(),
            "python": {
                "version": sys.version.split()[0],
                "executable": sys.executable,
            },
            "catalog_sha256": sha256(self.catalog_path),
            "tools": tools,
            "fingerprint": canonical_hash(stable),
        }

    def lock_environment(self) -> dict:
        snapshot = self.snapshot()
        atomic_write_json(self.environment_lock_path, snapshot)
        return snapshot

    def environment_drift(self) -> list[str]:
        locked = read_json(self.environment_lock_path)
        if locked is None:
            return ["计算环境锁缺失"]
        current = self.snapshot()
        if locked.get("catalog_sha256") != current["catalog_sha256"]:
            return ["环境锁绑定的工具目录已变化"]
        if locked.get("fingerprint") != current["fingerprint"]:
            return ["当前计算环境与 environment.lock.json 不一致"]
        return []

    def environment_profiles(self) -> list[dict]:
        records = []
        if not self.environment_directory.is_dir():
            return records
        for path in sorted(self.environment_directory.glob("*.json")):
            data = validate_environment_profile(read_json(path), path)
            records.append({
                **data,
                "path": path.relative_to(self.root).as_posix(),
            })
        return records

    def environment_profile(self, name: str) -> dict:
        matches = [
            item for item in self.environment_profiles()
            if item["name"] == name
        ]
        if len(matches) != 1:
            raise ValueError(f"计算环境 Profile 不存在或重名: {name}")
        return matches[0]

    def doctor(self, profile: str = "general") -> dict:
        catalog_errors = self.audit_catalog()
        snapshot = self.snapshot()
        spec = self.environment_profile(profile)
        known = set(self.catalog["tools"])
        unknown = sorted(
            set(spec["required_tools"] + spec["optional_tools"]) - known
        )
        required_missing = [
            tool_id for tool_id in spec["required_tools"]
            if not snapshot["tools"].get(tool_id, {}).get("available")
        ]
        optional_missing = [
            tool_id for tool_id in spec["optional_tools"]
            if not snapshot["tools"].get(tool_id, {}).get("available")
        ]
        warnings = [
            {"tool": tool_id, "messages": item["warnings"]}
            for tool_id, item in snapshot["tools"].items()
            if item["warnings"]
        ]
        return {
            "ok": not catalog_errors and not required_missing and not unknown,
            "profile": profile,
            "catalog_errors": catalog_errors,
            "required_missing": required_missing,
            "optional_missing": optional_missing,
            "unknown_profile_tools": unknown,
            "compatibility_warnings": warnings,
            "environment_drift": self.environment_drift(),
            "snapshot": snapshot,
        }

    def candidates(
        self,
        capabilities: list[str],
        task_type: str,
        *,
        available_only: bool = True,
        allowed_risks: set[str] | None = None,
    ) -> dict[str, list[dict]]:
        allowed_risks = allowed_risks or TOOL_RISKS
        result = {}
        for capability in capabilities:
            options = []
            for tool in self.list():
                if capability not in tool.get("capabilities", []):
                    continue
                if (
                    tool.get("task_types")
                    and "*" not in tool["task_types"]
                    and task_type not in tool["task_types"]
                ):
                    continue
                if tool.get("risk", "local") not in allowed_risks:
                    continue
                probe = self.probe(tool["id"])
                if available_only and not probe["available"]:
                    continue
                options.append({
                    "id": tool["id"],
                    "priority": tool.get("priority", 0),
                    "cost": tool.get("cost", 1),
                    "risk": tool.get("risk", "local"),
                    "available": probe["available"],
                    "version": probe["version"],
                    "warnings": probe["warnings"],
                })
            result[capability] = sorted(
                options,
                key=lambda item: (
                    -float(item["priority"]),
                    float(item["cost"]),
                    item["id"],
                ),
            )
        return result
