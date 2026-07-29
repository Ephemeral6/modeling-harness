from __future__ import annotations

import shutil
from pathlib import Path

from .storage import atomic_write_json
from .util import now

DIRS = [
    ".harness/stamps", ".harness/archive", "problem/data_raw",
    "data/processed", "docs/candidates", "src", "checks", "results",
    "reviews", "predictions", "paper", "paper/figures", "logs",
]


def create(destination: Path, title: str) -> Path:
    destination = destination.resolve()
    if not title.strip():
        raise ValueError("项目标题不能为空")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"目标目录非空: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for item in DIRS:
        (destination / item).mkdir(parents=True, exist_ok=True)
    templates = Path(__file__).parent.parent / "templates"
    if not templates.is_dir():
        raise RuntimeError(f"模板目录缺失: {templates}")
    for source in templates.rglob("*"):
        if source.is_file():
            target = destination / source.relative_to(templates)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    atomic_write_json(destination / "modeling-project.json", {
        "schema": 2, "title": title.strip(), "created_at": now(),
        "harness": "modeling-harness/2.0", "status": "active",
    })
    atomic_write_json(destination / ".harness" / "evidence.json", {
        "schema": 2, "revision": 0, "nodes": {},
    })
    atomic_write_json(destination / ".harness" / "tasks.json", {
        "schema": 1, "tasks": [],
    })
    atomic_write_json(destination / ".harness" / "events.json", {
        "schema": 1, "events": [],
    })
    return destination
