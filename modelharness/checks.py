"""Shared checks facade with directory and autonomous-tool acceptance."""
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
    tool_items = [
        item for item in acceptance
        if isinstance(item, dict) and item.get("kind") == "tool_decision"
    ]
    ordinary = [item for item in acceptance if item not in tool_items]
    record = _evaluate_acceptance(root, ordinary, result)
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
    if tool_items:
        # Lazy import avoids checks -> executor -> checks import cycle.
        from .toolchain import ToolchainService
        service = ToolchainService(root)
        for item in tool_items:
            record["records"].append(service.acceptance_record(
                str(item.get("node_id", "")),
                str(item.get("contract_hash", "")),
            ))
    passed = bool(record["records"]) and all(
        item.get("ok") is True for item in record["records"]
    )
    record.update({
        "execution_status": "completed" if record["records"] else "not_run",
        "verdict": (
            "pass" if passed else "fail"
        ) if record["records"] else "unassessed",
        "authority": "machine",
        "ok": passed,
    })
    return record
