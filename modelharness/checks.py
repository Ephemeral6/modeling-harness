"""Shared checks facade with V2 directory-acceptance compatibility."""
from __future__ import annotations

from pathlib import Path

from .checks_core import (
    normalize_check,
    run_check,
    run_checks,
)
from .checks_core import evaluate_acceptance as _evaluate_acceptance


def evaluate_acceptance(
    root: Path, acceptance: list, result: dict | None = None
) -> dict:
    record = _evaluate_acceptance(root, acceptance, result)
    root = root.resolve()
    for item in record["records"]:
        if (
            item.get("kind") == "artifact_exists"
            and not item.get("ok")
            and item.get("path")
        ):
            path = (root / item["path"]).resolve()
            if path.is_relative_to(root) and path.is_dir():
                item.update({"ok": True, "artifact_type": "directory"})
    record["ok"] = all(item.get("ok") is True for item in record["records"])
    return record
