"""Run lifecycle status recorded in modeling-project.json."""
from __future__ import annotations

from pathlib import Path

from .storage import atomic_write_json, file_lock, read_json
from .util import now

STATUSES = ("active", "interrupted", "completed", "delivered", "abandoned")


def _manifest_path(root: Path) -> Path:
    return Path(root).resolve() / "modeling-project.json"


def get_status(root: Path) -> str:
    """Current run status; manifests without the field count as active."""
    meta = read_json(_manifest_path(root))
    if not isinstance(meta, dict):
        return "active"
    return meta.get("status", "active")


def describe(root: Path) -> dict:
    meta = read_json(_manifest_path(root))
    meta = meta if isinstance(meta, dict) else {}
    return {
        "project": str(Path(root).resolve()),
        "status": meta.get("status", "active"),
        "history": meta.get("status_history", []),
    }


def set_status(root: Path, status: str, reason: str) -> dict:
    """Flip run status and append an audit record to status_history."""
    if status not in STATUSES:
        raise ValueError(f"非法项目状态: {status}；合法值为 {STATUSES}")
    if not reason or not reason.strip():
        raise ValueError("状态变更必须提供 reason")
    path = _manifest_path(root)
    with file_lock(path, timeout=10):
        meta = read_json(path)
        if not isinstance(meta, dict):
            raise ValueError(f"项目清单缺失或损坏: {path}")
        meta["status"] = status
        meta.setdefault("status_history", []).append({
            "status": status,
            "at": now(),
            "reason": reason.strip(),
        })
        atomic_write_json(path, meta)
    return meta
