"""Tool-registry facade with migration, probe cache and version profiles."""
from __future__ import annotations

import copy
import os
import re
import shutil
import sys
from pathlib import Path

from .problem_graph import canonical_hash
from .toolchain_registry_core import (
    TOOL_KINDS,
    TOOL_RISKS,
    ToolRegistry as _ToolRegistry,
    validate_autonomy,
    validate_catalog,
    validate_environment_profile,
)


_VERSION_PART = re.compile(r"\d+")


def _version_tuple(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    return tuple(int(item) for item in _VERSION_PART.findall(value))


def _satisfies(value: str | None, operator: str, target: str) -> bool:
    left, right = _version_tuple(value), _version_tuple(target)
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return {
        "<": left < right,
        "<=": left <= right,
        "==": left == right,
        "!=": left != right,
        ">=": left >= right,
        ">": left > right,
    }.get(operator, False)


class ToolRegistry(_ToolRegistry):
    _probe_cache: dict[str, dict] = {}

    def __init__(self, root: Path):
        root = root.resolve()
        directory = root / "config" / "tools"
        # Existing 3.0 projects have no tools directory. Seed exactly once.
        # If the directory exists but is incomplete, the core fails closed.
        if (
            not directory.exists()
            and (root / "modeling-project.json").is_file()
        ):
            templates = Path(__file__).parent.parent / "templates" / "config"
            source_tools = templates / "tools"
            source_environments = templates / "tool_environments"
            source_autonomy = templates / "tool_autonomy.json"
            if not (
                source_tools.is_dir()
                and source_environments.is_dir()
                and source_autonomy.is_file()
            ):
                raise RuntimeError("V3.1 计算工具模板缺失")
            shutil.copytree(source_tools, directory)
            shutil.copytree(
                source_environments, root / "config" / "tool_environments"
            )
            shutil.copy2(source_autonomy, root / "config" / "tool_autonomy.json")
        super().__init__(root)
        if self.catalog_path.is_file() and not self.catalog_lock_path.is_file():
            self.refresh_catalog_lock()

    def probe(self, tool_id: str) -> dict:
        key = canonical_hash({
            "tool": self.get(tool_id),
            "path": os.environ.get("PATH", ""),
            "python": sys.executable,
        })
        if key not in self._probe_cache:
            self._probe_cache[key] = super().probe(tool_id)
        return copy.deepcopy(self._probe_cache[key])

    def doctor(self, profile: str = "general") -> dict:
        report = super().doctor(profile)
        spec = self.environment_profile(profile)
        mismatches = []
        for rule in spec.get("constraints", []):
            if not isinstance(rule, dict):
                mismatches.append({
                    "rule": rule, "required": True,
                    "message": "版本约束不是对象",
                })
                continue
            tool_id = str(rule.get("tool", ""))
            operator = str(rule.get("operator", "=="))
            target = str(rule.get("version", ""))
            probe = report["snapshot"]["tools"].get(tool_id)
            if not probe or not probe.get("available"):
                continue
            actual = probe.get("version")
            if not _satisfies(actual, operator, target):
                mismatches.append({
                    "tool": tool_id,
                    "actual": actual,
                    "required": bool(rule.get("required", True)),
                    "constraint": f"{operator}{target}",
                    "message": rule.get(
                        "message",
                        f"{tool_id}={actual} 不满足 {operator}{target}",
                    ),
                })
        report["version_mismatches"] = mismatches
        if any(item["required"] for item in mismatches):
            report["ok"] = False
        return report


__all__ = [
    "TOOL_KINDS",
    "TOOL_RISKS",
    "ToolRegistry",
    "validate_autonomy",
    "validate_catalog",
    "validate_environment_profile",
]
