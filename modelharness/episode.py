"""Portable, explicit episode packages for regression and failure analysis."""
from __future__ import annotations

import argparse
import platform
import shutil
import sys
import uuid
from pathlib import Path

from .evaluation import score_project
from .lifecycle import get_status
from .sanitize import INTERNAL_ARTIFACTS
from .storage import atomic_write_json
from .util import now, project_root, sha256
from .workflow import WorkflowEngine


CAPTURE_PATHS = (
    "modeling-project.json",
    "problem",
    "config",
    "docs",
    "checks",
    "results",
    "reviews",
    "predictions",
    "paper",
    "logs/tool_runs",
    ".harness/problem_graph.json",
    ".harness/evidence.json",
    ".harness/tool_plans",
    ".harness/tool_decisions",
    ".harness/tool_runs",
    ".harness/stamps",
)


def _copy_selected(root: Path, destination: Path) -> list[dict]:
    files = []
    for relative in CAPTURE_PATHS:
        source = root / relative
        target = destination / "project" / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for relative in INTERNAL_ARTIFACTS:
        (destination / "project" / relative).unlink(missing_ok=True)
    for path in sorted((destination / "project").rglob("*")):
        if path.is_file():
            files.append({
                "path": path.relative_to(destination / "project").as_posix(),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            })
    return files


def capture_episode(
    project: Path,
    destination: Path | None = None,
    *,
    benchmark_id: str | None = None,
    model: str | None = None,
    seed: int | None = None,
    allow_partial: bool = False,
) -> Path:
    root = project_root(project)
    status = get_status(root)
    if not allow_partial and status not in {
        "completed", "delivered", "abandoned"
    }:
        raise ValueError(
            f"项目状态为 {status}，"
            "只有 completed/delivered/abandoned 可捕获 episode；"
            "确需捕获请传 allow_partial=True"
        )
    episode_id = (
        f"{now().replace(':', '-')}-{uuid.uuid4().hex[:8]}"
    )
    destination = (
        destination.resolve()
        if destination else root / ".harness" / "episodes" / episode_id
    )
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"episode destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    workflow = WorkflowEngine(root)
    files = _copy_selected(root, destination)
    atomic_write_json(destination / "tasks.json", workflow.list_tasks())
    atomic_write_json(destination / "events.json", workflow.list_events(100000))
    try:
        evaluation = score_project(root)
    except (ValueError, RuntimeError, OSError) as exc:
        evaluation = {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }
    manifest = {
        "schema": 1,
        "id": episode_id,
        "captured_at": now(),
        "benchmark_id": benchmark_id,
        "model": model,
        "seed": seed,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "source_project": str(root),
        "status": status,
        "files": files,
        "evaluation": evaluation,
    }
    atomic_write_json(destination / "episode.json", manifest)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture a reproducible Modeling Harness episode"
    )
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path)
    parser.add_argument("--benchmark-id")
    parser.add_argument("--model")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    print(capture_episode(
        args.project,
        args.out,
        benchmark_id=args.benchmark_id,
        model=args.model,
        seed=args.seed,
        allow_partial=args.allow_partial,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
