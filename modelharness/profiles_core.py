"""Delivery profiles for CUMCM, MCM/ICM and real-world modeling."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .problem_graph import canonical_hash
from .storage import atomic_write_json, read_json


def validate_profile(data: Any, path: Path | None = None) -> dict:
    where = f": {path}" if path else ""
    if not isinstance(data, dict):
        raise ValueError(f"delivery profile 必须是对象{where}")
    for field in ("name", "version", "renderer"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise ValueError(f"delivery profile.{field} 缺失{where}")
    if not isinstance(data.get("max_parallel_agents", 1), int):
        raise ValueError(f"delivery profile.max_parallel_agents 非法{where}")
    for field in (
        "mandatory_outputs", "human_checkpoints", "report_sections",
        "quality_dimensions",
    ):
        if not isinstance(data.get(field, []), list):
            raise ValueError(f"delivery profile.{field} 必须是数组{where}")
    return data


class ProfileService:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "config" / "profiles"
        self.active_path = self.root / "config" / "delivery_profile.json"

    def list(self) -> list[dict]:
        records = []
        if not self.directory.is_dir():
            return records
        for path in sorted(self.directory.glob("*.json")):
            data = validate_profile(read_json(path), path)
            records.append({
                "name": data["name"],
                "version": data["version"],
                "renderer": data["renderer"],
                "path": path.relative_to(self.root).as_posix(),
            })
        return records

    @property
    def active(self) -> dict:
        data = read_json(self.active_path)
        if data is None:
            raise ValueError("delivery_profile.json 缺失")
        return validate_profile(data, self.active_path)

    def use(self, name: str) -> dict:
        candidates = [
            path for path in self.directory.glob("*.json")
            if read_json(path, {}).get("name") == name
        ]
        if len(candidates) != 1:
            raise ValueError(f"交付 Profile 不存在或重名: {name}")
        profile = validate_profile(read_json(candidates[0]), candidates[0])
        atomic_write_json(self.active_path, profile)
        return profile

    def content_hash(self) -> str:
        return canonical_hash(self.active)

