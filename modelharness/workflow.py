"""Durable SQLite workflow engine for local-research task lifecycle."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .checks import evaluate_acceptance
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
            CREATE TABLE IF NOT EXISTS schema_migrations(
              version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            );
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
              work_item_id TEXT, task_type TEXT, contract_hash TEXT,
              generation INTEGER NOT NULL DEFAULT 1,
              superseded_at TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """)
            columns = {
                row["name"]
                for row in con.execute("PRAGMA table_info(tasks)").fetchall()
            }
            additions = {
                "work_item_id": "TEXT",
                "task_type": "TEXT",
                "contract_hash": "TEXT",
                "generation": "INTEGER NOT NULL DEFAULT 1",
                "superseded_at": "TEXT",
            }
            for name, declaration in additions.items():
                if name not in columns:
                    con.execute(
                        f"ALTER TABLE tasks ADD COLUMN {name} {declaration}"
                    )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_work_item "
                "ON tasks(work_item_id, contract_hash)"
            )
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
                "VALUES(1,?)", (now(),)
            )

    def event(
        self, kind: str, payload: dict, correlation_id: str | None = None
    ) -> int:
        correlation_id = correlation_id or uuid.uuid4().hex
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO events(time,kind,correlation_id,payload) VALUES(?,?,?,?)",
                (now(), kind, correlation_id,
                 json.dumps(payload, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def list_tasks(
        self, stage: str | None = None, work_item_id: str | None = None
    ) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                """SELECT * FROM tasks
                   WHERE (? IS NULL OR stage=?)
                   AND (? IS NULL OR work_item_id=?)
                   ORDER BY created_at""",
                (stage, stage, work_item_id, work_item_id),
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
        self,
        key: str,
        stage: str,
        role: str,
        description: str,
        owns: list[str],
        inputs: list[str] | None = None,
        acceptance: list | None = None,
        budget: dict | None = None,
        work_item_id: str | None = None,
        task_type: str | None = None,
        contract_hash: str | None = None,
        generation: int = 1,
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
                 acceptance,budget,status,work_item_id,task_type,contract_hash,
                 generation,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?,?,?)""",
                (
                    task_id, key, stage, role, description,
                    json.dumps(normalized), json.dumps(inputs or []),
                    json.dumps(acceptance or []), json.dumps(budget or {}),
                    work_item_id, task_type, contract_hash, max(1, generation),
                    timestamp, timestamp,
                ),
            )
            con.execute("COMMIT")
        self.event("task.created", {
            "task_id": task_id, "stage": stage, "role": role,
            "work_item_id": work_item_id, "contract_hash": contract_hash,
            "generation": max(1, generation),
        })
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"任务不存在: {task_id}")
        return self._decode(row)

    def claim(
        self, task_id: str, worker: str, lease_seconds: int = 900
    ) -> dict:
        if not worker:
            raise ValueError("worker 不能为空")
        lease = time.time() + max(30, lease_seconds)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
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

    def heartbeat(
        self, task_id: str, worker: str, lease_seconds: int = 900
    ) -> dict:
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status='running',lease_until=?,updated_at=?
                   WHERE id=? AND worker=? AND status IN ('claimed','running')""",
                (time.time() + max(30, lease_seconds), now(), task_id, worker),
            )
            if cur.rowcount != 1:
                raise ValueError("任务 lease 不属于该 worker")
        return self.get_task(task_id)

    def finish(
        self, task_id: str, worker: str, success: bool, result: dict
    ) -> dict:
        task = self.get_task(task_id)
        acceptance = (
            evaluate_acceptance(self.project, task["acceptance"], result)
            if success else {"ok": False, "records": []}
        )
        final_result = dict(result)
        final_result["acceptance"] = acceptance
        status = "completed" if success and acceptance["ok"] else "failed"
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status=?,result=?,lease_until=NULL,updated_at=?
                   WHERE id=? AND worker=? AND status IN ('claimed','running')""",
                (
                    status, json.dumps(final_result, ensure_ascii=False), now(),
                    task_id, worker,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("任务不可由该 worker 完成")
        self.event(f"task.{status}", {
            "task_id": task_id, "worker": worker,
            "acceptance_ok": acceptance["ok"],
        })
        finished = self.get_task(task_id)
        if success and not acceptance["ok"]:
            raise RuntimeError(
                f"任务验收失败: {task_id}: "
                f"{[x for x in acceptance['records'] if not x.get('ok')]}"
            )
        return finished

    def retry(self, task_id: str) -> dict:
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status='pending',worker=NULL,lease_until=NULL,
                   result=NULL,updated_at=?
                   WHERE id=? AND status='failed'""",
                (now(), task_id),
            )
            if cur.rowcount != 1:
                raise ValueError("只有 failed 任务可以重试")
        self.event("task.retried", {"task_id": task_id})
        return self.get_task(task_id)

    def supersede(
        self, work_item_id: str, contract_hash: str | None = None
    ) -> list[str]:
        timestamp = now()
        with self.connect() as con:
            rows = con.execute(
                """SELECT id FROM tasks
                   WHERE work_item_id=?
                   AND (? IS NULL OR contract_hash<>? OR contract_hash IS NULL)
                   AND status IN ('pending','claimed','running')""",
                (work_item_id, contract_hash, contract_hash),
            ).fetchall()
            ids = [row["id"] for row in rows]
            for task_id in ids:
                con.execute(
                    """UPDATE tasks SET status='superseded',worker=NULL,
                       lease_until=NULL,superseded_at=?,updated_at=?
                       WHERE id=?""",
                    (timestamp, timestamp, task_id),
                )
        for task_id in ids:
            self.event("task.superseded", {
                "task_id": task_id, "work_item_id": work_item_id,
            })
        return ids

    def reconcile(self, max_attempts: int = 3) -> list[str]:
        recovered = []
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                """SELECT id,attempt,budget FROM tasks
                   WHERE status IN ('claimed','running')
                   AND lease_until IS NOT NULL AND lease_until < ?""",
                (time.time(),),
            ).fetchall()
            for row in rows:
                budget = json.loads(row["budget"] or "{}")
                task_max = int(budget.get("max_attempts", max_attempts))
                status = "failed" if row["attempt"] >= task_max else "pending"
                con.execute(
                    """UPDATE tasks SET status=?,worker=NULL,lease_until=NULL,
                       updated_at=? WHERE id=?""",
                    (status, now(), row["id"]),
                )
                recovered.append(row["id"])
            con.execute("COMMIT")
        for task_id in recovered:
            self.event("task.lease_expired", {"task_id": task_id})
        return recovered
