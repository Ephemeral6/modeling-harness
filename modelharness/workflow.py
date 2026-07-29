"""Durable SQLite workflow engine for subagent task lifecycle."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from .util import now


def normalize_owner(path: str) -> str:
    value = Path(path.replace("\\", "/")).as_posix().strip("/")
    if not value or value.startswith("../") or "/../" in value:
        raise ValueError(f"非法写入范围: {path}")
    return value.casefold()


def owners_overlap(left: str, right: str) -> bool:
    a, b = normalize_owner(left), normalize_owner(right)
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


class WorkflowEngine:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.path = self.project / ".harness" / "workflow.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init(self) -> None:
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS events(
              seq INTEGER PRIMARY KEY AUTOINCREMENT,
              time TEXT NOT NULL, kind TEXT NOT NULL,
              correlation_id TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks(
              id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
              stage TEXT NOT NULL, role TEXT NOT NULL, description TEXT NOT NULL,
              owns TEXT NOT NULL, inputs TEXT NOT NULL, acceptance TEXT NOT NULL,
              budget TEXT NOT NULL, status TEXT NOT NULL,
              worker TEXT, attempt INTEGER NOT NULL DEFAULT 0,
              lease_until REAL, result TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """)

    def event(self, kind: str, payload: dict,
              correlation_id: str | None = None) -> int:
        correlation_id = correlation_id or uuid.uuid4().hex
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO events(time,kind,correlation_id,payload) VALUES(?,?,?,?)",
                (now(), kind, correlation_id,
                 json.dumps(payload, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def list_tasks(self, stage: str | None = None) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM tasks WHERE (? IS NULL OR stage=?) ORDER BY created_at",
                (stage, stage),
            ).fetchall()
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        data = dict(row)
        for field in ("owns", "inputs", "acceptance", "budget", "result"):
            if data[field] is not None:
                data[field] = json.loads(data[field])
        return data

    def ensure_task(
        self, key: str, stage: str, role: str, description: str,
        owns: list[str], inputs: list[str] | None = None,
        acceptance: list[str] | None = None, budget: dict | None = None,
    ) -> dict:
        normalized = [normalize_owner(x) for x in owns]
        if len(normalized) != len(set(normalized)) or not normalized:
            raise ValueError("任务写入范围为空或重复")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT * FROM tasks WHERE idempotency_key=?", (key,)
            ).fetchone()
            if existing:
                con.execute("COMMIT")
                return self._decode(existing)
            active = con.execute(
                "SELECT id,owns FROM tasks WHERE status IN "
                "('pending','claimed','running')"
            ).fetchall()
            conflicts = []
            for row in active:
                other = json.loads(row["owns"])
                if any(owners_overlap(a, b) for a in normalized for b in other):
                    conflicts.append(row["id"])
            if conflicts:
                con.execute("ROLLBACK")
                raise ValueError(f"写入范围与活动任务冲突: {conflicts}")
            task_id = uuid.uuid4().hex[:16]
            timestamp = now()
            con.execute(
                """INSERT INTO tasks
                (id,idempotency_key,stage,role,description,owns,inputs,
                 acceptance,budget,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?)""",
                (task_id, key, stage, role, description,
                 json.dumps(normalized), json.dumps(inputs or []),
                 json.dumps(acceptance or []), json.dumps(budget or {}),
                 timestamp, timestamp),
            )
            con.execute("COMMIT")
        self.event("task.created", {"task_id": task_id, "stage": stage, "role": role})
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict:
        with self.connect() as con:
            row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError(f"任务不存在: {task_id}")
        return self._decode(row)

    def claim(self, task_id: str, worker: str, lease_seconds: int = 900) -> dict:
        if not worker:
            raise ValueError("worker 不能为空")
        lease = time.time() + max(30, lease_seconds)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if row is None or row["status"] != "pending":
                con.execute("ROLLBACK")
                raise ValueError("任务不存在或不可 claim")
            con.execute(
                """UPDATE tasks SET status='claimed',worker=?,attempt=attempt+1,
                   lease_until=?,updated_at=? WHERE id=?""",
                (worker, lease, now(), task_id),
            )
            con.execute("COMMIT")
        self.event("task.claimed", {"task_id": task_id, "worker": worker})
        return self.get_task(task_id)

    def heartbeat(self, task_id: str, worker: str,
                  lease_seconds: int = 900) -> dict:
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status='running',lease_until=?,updated_at=?
                   WHERE id=? AND worker=? AND status IN ('claimed','running')""",
                (time.time() + max(30, lease_seconds), now(), task_id, worker),
            )
            if cur.rowcount != 1:
                raise ValueError("任务 lease 不属于该 worker")
        return self.get_task(task_id)

    def finish(self, task_id: str, worker: str, success: bool,
               result: dict) -> dict:
        status = "completed" if success else "failed"
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status=?,result=?,lease_until=NULL,updated_at=?
                   WHERE id=? AND worker=? AND status IN ('claimed','running')""",
                (status, json.dumps(result, ensure_ascii=False), now(),
                 task_id, worker),
            )
            if cur.rowcount != 1:
                raise ValueError("任务不可由该 worker 完成")
        self.event(f"task.{status}", {"task_id": task_id, "worker": worker})
        return self.get_task(task_id)

    def reconcile(self, max_attempts: int = 3) -> list[str]:
        recovered = []
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                """SELECT id,attempt FROM tasks
                   WHERE status IN ('claimed','running')
                   AND lease_until IS NOT NULL AND lease_until < ?""",
                (time.time(),),
            ).fetchall()
            for row in rows:
                status = "failed" if row["attempt"] >= max_attempts else "pending"
                con.execute(
                    """UPDATE tasks SET status=?,worker=NULL,lease_until=NULL,
                       updated_at=? WHERE id=?""", (status, now(), row["id"])
                )
                recovered.append(row["id"])
            con.execute("COMMIT")
        for task_id in recovered:
            self.event("task.lease_expired", {"task_id": task_id})
        return recovered
