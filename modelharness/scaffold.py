from __future__ import annotations

import shutil
from pathlib import Path

from .method_packs import MethodPackRegistry
from .storage import atomic_write_json, read_json
from .toolchain_registry import ToolRegistry
from .util import now

DIRS = [
    ".harness/stamps", ".harness/archive", ".harness/tool_plans",
    ".harness/tool_decisions", ".harness/tool_runs",
    "problem/data_raw", "data/processed", "docs/candidates", "src",
    "checks", "results", "reviews", "predictions", "paper",
    "paper/figures", "logs", "logs/tool_runs", "config/method_packs",
    "config/profiles", "config/tools", "config/tool_environments",
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
    seed = destination / "config" / "problem_graph.seed.json"
    graph = read_json(seed)
    if graph is None:
        raise RuntimeError("Problem Graph 种子缺失")
    atomic_write_json(destination / ".harness" / "problem_graph.json", graph)
    seed.unlink()
    atomic_write_json(destination / "modeling-project.json", {
        "schema": 3,
        "title": title.strip(),
        "created_at": now(),
        "harness": "modeling-harness/3.1",
        "status": "active",
    })
    atomic_write_json(destination / ".harness" / "evidence.json", {
        "schema": 3, "revision": 0, "nodes": {},
    })
    # Kept for V1/V2 readers. V3 task truth lives in workflow.sqlite3.
    atomic_write_json(destination / ".harness" / "tasks.json", {
        "schema": 1, "tasks": [],
    })
    atomic_write_json(destination / ".harness" / "events.json", {
        "schema": 1, "events": [],
    })
    MethodPackRegistry(destination).refresh_lock()
    tools = ToolRegistry(destination)
    tools.refresh_catalog_lock()
    tools.lock_environment()
    return destination
