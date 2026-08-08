"""Run lifecycle status recorded in modeling-project.json."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .storage import StateError, atomic_write_json, file_lock, read_json
from .util import now

STATUSES = ("active", "interrupted", "completed", "delivered", "abandoned")
RESUMABLE = ("active", "interrupted")
CLOSABLE = ("active", "interrupted")
CLOSE_REASON = "s6 印章已存在，回填终态 completed"
RECOVERY_ACTION = (
    "需先 tool recover 对账（workflow 任务经 modelharness task recover "
    "裁决）；非幂等任务的未知结果不得当普通失败自动重跑"
    "（AGENTS.md 硬不变量 4）"
)


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


def close(root: Path, reason: str = CLOSE_REASON) -> dict:
    """Backfill ``completed`` on a run whose s6 gate already stamped.

    ``completed`` is normally written only by the s6 gate success path
    (``StageService.gate``), and this command must not weaken that: it never
    invents the milestone, it only reconciles the manifest of a historical run
    whose gate ran before lifecycle status was wired in. An s6 stamp on disk is
    therefore a hard precondition, and only non-terminal runs are closable so a
    ``delivered``/``abandoned`` verdict can never be silently downgraded.
    """
    root = Path(root).resolve()
    stamp = root / ".harness" / "stamps" / "s6.json"
    if not stamp.is_file():
        raise ValueError(
            f"拒绝收口：s6 印章不存在 ({stamp})；"
            "completed 只能出自 s6 gate 成功路径，close 只回填已盖章的历史 run"
        )
    record = read_json(stamp)
    if not isinstance(record, dict) or record.get("stage") != "s6":
        raise ValueError(f"拒绝收口：s6 印章损坏或 stage 字段不符: {stamp}")
    status = get_status(root)
    if status not in CLOSABLE:
        raise ValueError(
            f"拒绝收口：项目状态为 {status}；仅 {CLOSABLE} 可收口为 completed"
        )
    set_status(root, status="completed", reason=reason)
    return describe(root)


def reconcile_status(root: Path) -> dict:
    """Expire stale leases; flip an active run to interrupted on findings.

    Only flips when actually invoked (no timer): expired leases recovered by
    WorkflowEngine.reconcile() or tasks parked in recovery_pending are proof
    that a previous session died mid-flight.
    """
    # 局部导入避免 stages -> lifecycle -> workflow 链上的潜在环。
    from .workflow import WorkflowEngine

    root = Path(root).resolve()
    engine = WorkflowEngine(root)
    recovered = engine.reconcile()
    recovery_pending = sorted(
        task["id"] for task in engine.list_tasks()
        if task.get("status") == "recovery_pending"
    )
    status = get_status(root)
    flipped = False
    if (recovered or recovery_pending) and status == "active":
        details = []
        if recovered:
            details.append("过期租约=" + ",".join(sorted(recovered)))
        if recovery_pending:
            details.append("recovery_pending=" + ",".join(recovery_pending))
        set_status(root, "interrupted", "reconcile: " + "; ".join(details))
        status = "interrupted"
        flipped = True
    return {
        "project": str(root),
        "status": status,
        "flipped": flipped,
        "recovered_leases": recovered,
        "recovery_pending": recovery_pending,
    }


def _task_brief(task: dict) -> dict:
    return {
        "id": task["id"],
        "stage": task["stage"],
        "role": task["role"],
        "status": task["status"],
        "owns": task["owns"],
    }


def resume(root: Path) -> dict:
    """Rebuild a resume packet from disk state and reactivate the run.

    recovery_pending tasks are listed separately with an explicit
    reconciliation instruction and never mixed into the runnable list
    (AGENTS.md invariant 4).
    """
    from .workflow import WorkflowEngine

    root = Path(root).resolve()
    status = get_status(root)
    if status not in RESUMABLE:
        raise ValueError(
            f"项目状态为 {status}，拒绝 resume：仅 {RESUMABLE} 可续跑；"
            "completed/delivered 已终结，abandoned 需先人工把状态改回 "
            "active 再续。"
        )
    reconciled = reconcile_status(root)
    # 局部导入：autopilot -> stages -> lifecycle，顶层导入会成环。
    from .autopilot import next_packet

    packet = next_packet(root)
    open_tasks = [
        task for task in WorkflowEngine(root).list_tasks()
        if task.get("status") in {"pending", "claimed", "recovery_pending"}
    ]
    runnable = [
        _task_brief(task) for task in open_tasks
        if task["status"] != "recovery_pending"
    ]
    blocked = [
        {
            **_task_brief(task),
            "recovery_note": task.get("recovery_note"),
            "action_required": RECOVERY_ACTION,
        }
        for task in open_tasks if task["status"] == "recovery_pending"
    ]
    # 铁律：调度包中的 recovery_pending 一律移出普通可执行清单。
    packet["tasks"] = [
        task for task in packet.get("tasks", [])
        if task.get("status") != "recovery_pending"
    ]
    set_status(
        root, "active",
        f"resumed: {len(runnable)} 个可续跑任务，"
        f"{len(blocked)} 个 recovery_pending 待对账",
    )
    return {
        "project": str(root),
        "status": get_status(root),
        "reconcile": reconciled,
        "resume_tasks": runnable,
        "recovery_pending_tasks": blocked,
        "scheduler_packet": packet,
    }


def _latest_event_time(db_path: Path) -> str | None:
    if not db_path.is_file():
        return None
    try:
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT time FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def _is_stale(timestamp: str | None, hours: float) -> bool:
    if not timestamp:
        return True
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.astimezone()
    return datetime.now().astimezone() - moment > timedelta(hours=hours)


def audit_projects(root: Path, hours: float = 24.0) -> dict:
    """Scan a projects directory for runs stuck in a stale active state.

    stale_active: status == "active" while the latest workflow event (or the
    manifest created_at when no event exists) is older than ``hours`` and the
    s6 milestone has not been stamped.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"projects 目录不存在: {root}")
    projects: list[dict] = []
    stale: list[dict] = []
    for manifest in sorted(root.glob("*/modeling-project.json")):
        project = manifest.parent
        row: dict = {"project": project.name, "path": str(project)}
        try:
            meta = read_json(manifest)
        except StateError as exc:
            row.update({"status": "corrupt", "error": str(exc)})
            projects.append(row)
            continue
        meta = meta if isinstance(meta, dict) else {}
        row["status"] = meta.get("status", "active")
        latest_event = _latest_event_time(
            project / ".harness" / "workflow.sqlite3"
        )
        row["latest_event"] = latest_event
        row["s6_stamped"] = (
            project / ".harness" / "stamps" / "s6.json"
        ).is_file()
        latest_activity = latest_event or meta.get("created_at")
        row["stale_active"] = (
            row["status"] == "active"
            and not row["s6_stamped"]
            and _is_stale(latest_activity, hours)
        )
        if row["stale_active"]:
            stale.append(row)
        projects.append(row)
    return {
        "ok": not stale,
        "root": str(root),
        "hours": hours,
        "projects": projects,
        "stale_active": stale,
    }
