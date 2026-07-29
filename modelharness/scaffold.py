from __future__ import annotations

import shutil
from pathlib import Path

from .util import now, write_json


DIRS = [
    ".harness/stamps", "problem/data_raw", "data/processed", "docs/candidates",
    "src", "checks", "results", "reviews", "predictions", "paper", "logs",
]


def create(destination: Path, title: str) -> Path:
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f"目标目录非空: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    for item in DIRS:
        (destination / item).mkdir(parents=True, exist_ok=True)
    write_json(destination / "modeling-project.json", {
        "schema": 1, "title": title, "created_at": now(),
        "harness": "modeling-harness/1.0",
    })
    write_json(destination / ".harness" / "evidence.json",
               {"schema": 1, "nodes": {}})
    templates = Path(__file__).parent.parent / "templates"
    for src in templates.rglob("*"):
        if src.is_file():
            dst = destination / src.relative_to(templates)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    return destination

