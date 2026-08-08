"""4.6 机制 3：interrupted 自动判定 + project resume 续跑。"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from modelharness import cli_v31
from modelharness.lifecycle import (
    audit_projects,
    describe,
    get_status,
    reconcile_status,
    resume,
    set_status,
)
from modelharness.scaffold import create
from modelharness.workflow import WorkflowEngine


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def _expire_lease(engine: WorkflowEngine, task_id: str) -> None:
    con = engine.connect()
    try:
        con.execute(
            "UPDATE tasks SET lease_until=? WHERE id=?",
            (time.time() - 3600, task_id),
        )
    finally:
        con.close()


def _backdate_events(engine: WorkflowEngine, timestamp: str) -> None:
    con = engine.connect()
    try:
        con.execute("UPDATE events SET time=?", (timestamp,))
    finally:
        con.close()


def _claimed_expired_task(
    root: Path, *, idempotent: bool = True, key: str = "v46:demo"
) -> str:
    engine = WorkflowEngine(root)
    task = engine.ensure_task(
        key,
        "s1",
        "v46-modeler",
        "v46 lifecycle demo task",
        [f"results/{key.replace(':', '_')}.json"],
        budget={"max_attempts": 3},
        idempotent=idempotent,
    )
    engine.claim(task["id"], "w-v46", 30)
    _expire_lease(engine, task["id"])
    return task["id"]


def test_expired_lease_flips_active_to_interrupted(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    task_id = _claimed_expired_task(root)
    assert get_status(root) == "active"
    report = reconcile_status(root)
    assert report["flipped"] is True
    assert task_id in report["recovered_leases"]
    assert get_status(root) == "interrupted"
    history = describe(root)["history"]
    assert history[-1]["status"] == "interrupted"
    assert task_id in history[-1]["reason"]
    # 再次 reconcile：状态已非 active，不重复翻转。
    again = reconcile_status(root)
    assert again["flipped"] is False
    assert get_status(root) == "interrupted"


def test_resume_reactivates_and_lists_expired_task(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    task_id = _claimed_expired_task(root)
    reconcile_status(root)
    assert get_status(root) == "interrupted"
    packet = resume(root)
    assert get_status(root) == "active"
    history = describe(root)["history"]
    assert history[-1]["status"] == "active"
    assert history[-1]["reason"].startswith("resumed")
    entries = {item["id"]: item for item in packet["resume_tasks"]}
    assert task_id in entries
    entry = entries[task_id]
    assert entry["role"] == "v46-modeler"
    assert entry["stage"] == "s1"
    assert entry["owns"] == ["results/v46_demo.json"]
    assert entry["status"] in {"pending", "claimed"}
    assert packet["recovery_pending_tasks"] == []


def test_resume_isolates_recovery_pending_tasks(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    risky_id = _claimed_expired_task(
        root, idempotent=False, key="v46:external"
    )
    packet = resume(root)
    blocked = packet["recovery_pending_tasks"]
    assert [item["id"] for item in blocked] == [risky_id]
    assert blocked[0]["status"] == "recovery_pending"
    assert "tool recover" in blocked[0]["action_required"]
    # 铁律（AGENTS.md 不变量 4）：不得混入普通可执行清单。
    assert risky_id not in [item["id"] for item in packet["resume_tasks"]]
    scheduler_tasks = packet["scheduler_packet"].get("tasks", [])
    assert all(
        item.get("status") != "recovery_pending" for item in scheduler_tasks
    )
    assert get_status(root) == "active"


def test_resume_rejects_abandoned_project(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    set_status(root, "abandoned", "superseded by rerun")
    with pytest.raises(ValueError, match="abandoned"):
        resume(root)
    assert get_status(root) == "abandoned"


def test_resume_rejects_terminal_states(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    for terminal in ("completed", "delivered"):
        set_status(root, terminal, f"force {terminal}")
        with pytest.raises(ValueError, match=terminal):
            resume(root)
        assert get_status(root) == terminal


def test_project_resume_cli(tmp_path: Path, monkeypatch, capsys):
    root = create(tmp_path / "case", "demo")
    task_id = _claimed_expired_task(root)
    code = run_cli(
        monkeypatch, "project", "resume", "--project", str(root)
    )
    assert code == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["status"] == "active"
    assert task_id in [item["id"] for item in packet["resume_tasks"]]
    assert get_status(root) == "active"


def test_project_resume_cli_rejects_abandoned(
    tmp_path: Path, monkeypatch, capsys
):
    root = create(tmp_path / "case", "demo")
    set_status(root, "abandoned", "superseded by rerun")
    code = run_cli(
        monkeypatch, "project", "resume", "--project", str(root)
    )
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert "abandoned" in report["message"]
    assert get_status(root) == "abandoned"


def test_project_audit_flags_stale_active(
    tmp_path: Path, monkeypatch, capsys
):
    projects_root = tmp_path / "projects"
    stale = create(projects_root / "stale_case", "stale demo")
    _claimed_expired_task(stale)
    old = (
        datetime.now().astimezone() - timedelta(hours=48)
    ).isoformat(timespec="seconds")
    _backdate_events(WorkflowEngine(stale), old)
    done = create(projects_root / "abandoned_case", "done demo")
    set_status(done, "abandoned", "superseded by rerun")

    code = run_cli(
        monkeypatch,
        "project", "audit", "--root", str(projects_root),
    )
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert [item["project"] for item in report["stale_active"]] == [
        "stale_case",
    ]
    summary = {
        item["project"]: item["status"] for item in report["projects"]
    }
    assert summary == {
        "stale_case": "active", "abandoned_case": "abandoned",
    }

    # 阈值可调：放宽到 96 小时后不再 stale。
    code = run_cli(
        monkeypatch,
        "project", "audit", "--root", str(projects_root),
        "--hours", "96",
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["stale_active"] == []


def test_audit_projects_fresh_project_not_stale(tmp_path: Path):
    projects_root = tmp_path / "projects"
    create(projects_root / "fresh_case", "fresh demo")
    report = audit_projects(projects_root, hours=24)
    assert report["ok"] is True
    assert report["stale_active"] == []
    assert [item["project"] for item in report["projects"]] == [
        "fresh_case",
    ]
