"""Durable SQLite workflow engine with explicit recovery semantics."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .checks import evaluate_acceptance
from .runtime_state import AUTHORITIES
from .util import now


def normalize_owner(path: str) -> str:
    value = Path(path.replace("\\", "/")).as_posix().strip("/")
    if not value or value.startswith("../") or "/../" in value:
        raise ValueError(f"invalid write scope: {path}")
    return value.casefold()


def owners_overlap(left: str, right: str) -> bool:
    a, b = normalize_owner(left), normalize_owner(right)
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


class WorkflowEngine:
    ACTIVE_STATES = ("pending", "claimed", "running", "recovery_pending")
    FINISHABLE_STATES = ("claimed", "running")
    # Next step to unblock `finish`, keyed by the status the task is really
    # in. Rendered with task_id/worker so the hint is copy-pasteable.
    FINISH_BLOCKED_ACTIONS = {
        "pending": (
            "需先 `modelharness task claim {task_id} --worker {worker}` "
            "再 finish"
        ),
        "failed": (
            "需先 `modelharness task retry {task_id}`，"
            "再 `modelharness task claim {task_id} --worker {worker}`，"
            "然后重跑 finish"
        ),
        "completed": "任务已完成，不需要再 finish；如需重做请新建任务",
        "recovery_pending": (
            "需先 `modelharness task recover {task_id} --outcome <outcome> "
            "--note <note>` 结清恢复"
        ),
        "superseded": (
            "任务已被 supersede，请改做同 work_item 的当前代任务"
        ),
    }
    FINISH_BLOCKED_FALLBACK = "需先把任务恢复为 claimed/running 再 finish"

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
              execution_status TEXT NOT NULL DEFAULT 'not_run',
              verdict TEXT NOT NULL DEFAULT 'unassessed',
              authority TEXT NOT NULL DEFAULT 'machine',
              freshness TEXT NOT NULL DEFAULT 'valid',
              side_effect_class TEXT NOT NULL DEFAULT 'project_local',
              idempotent INTEGER NOT NULL DEFAULT 1,
              recovery_note TEXT,
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
                "execution_status": "TEXT NOT NULL DEFAULT 'not_run'",
                "verdict": "TEXT NOT NULL DEFAULT 'unassessed'",
                "authority": "TEXT NOT NULL DEFAULT 'machine'",
                "freshness": "TEXT NOT NULL DEFAULT 'valid'",
                "side_effect_class": (
                    "TEXT NOT NULL DEFAULT 'project_local'"
                ),
                "idempotent": "INTEGER NOT NULL DEFAULT 1",
                "recovery_note": "TEXT",
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
            con.execute(
                "INSERT OR IGNORE INTO schema_migrations(version,applied_at) "
                "VALUES(2,?)", (now(),)
            )

    def event(
        self, kind: str, payload: dict, correlation_id: str | None = None
    ) -> int:
        correlation_id = correlation_id or uuid.uuid4().hex
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO events(time,kind,correlation_id,payload) "
                "VALUES(?,?,?,?)",
                (
                    now(), kind, correlation_id,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            return int(cur.lastrowid)

    def list_events(self, limit: int = 100) -> list[dict]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM events ORDER BY seq DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        records = []
        for row in reversed(rows):
            record = dict(row)
            record["payload"] = json.loads(record["payload"])
            records.append(record)
        return records

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
            if data.get(field) is not None:
                data[field] = json.loads(data[field])
        data["idempotent"] = bool(data.get("idempotent", 1))
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
        side_effect_class: str = "project_local",
        idempotent: bool = True,
    ) -> dict:
        normalized = [normalize_owner(x) for x in owns]
        if len(normalized) != len(set(normalized)) or not normalized:
            raise ValueError("task write scope is empty or duplicated")
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
                "('pending','claimed','running','recovery_pending')"
            ).fetchall()
            conflicts = []
            for row in active:
                other = json.loads(row["owns"])
                if any(
                    owners_overlap(a, b) for a in normalized for b in other
                ):
                    conflicts.append(row["id"])
            if conflicts:
                con.execute("ROLLBACK")
                raise ValueError(f"write scope conflicts: {conflicts}")
            task_id = uuid.uuid4().hex[:16]
            timestamp = now()
            con.execute(
                """INSERT INTO tasks
                (id,idempotency_key,stage,role,description,owns,inputs,
                 acceptance,budget,status,execution_status,verdict,authority,
                 freshness,side_effect_class,idempotent,work_item_id,task_type,
                 contract_hash,generation,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'pending','not_run','unassessed',
                       'machine','valid',?,?,?,?,?,?,?,?)""",
                (
                    task_id, key, stage, role, description,
                    json.dumps(normalized), json.dumps(inputs or []),
                    json.dumps(acceptance or []), json.dumps(budget or {}),
                    side_effect_class, 1 if idempotent else 0,
                    work_item_id, task_type, contract_hash, max(1, generation),
                    timestamp, timestamp,
                ),
            )
            con.execute("COMMIT")
        self.event("task.created", {
            "task_id": task_id,
            "stage": stage,
            "role": role,
            "work_item_id": work_item_id,
            "contract_hash": contract_hash,
            "generation": max(1, generation),
            "side_effect_class": side_effect_class,
            "idempotent": bool(idempotent),
        })
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if row is None:
            raise ValueError(f"task does not exist: {task_id}")
        return self._decode(row)

    def claim(
        self, task_id: str, worker: str, lease_seconds: int = 900
    ) -> dict:
        if not worker:
            raise ValueError("worker cannot be empty")
        lease = time.time() + max(30, lease_seconds)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT status FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None or row["status"] != "pending":
                con.execute("ROLLBACK")
                raise ValueError("task does not exist or cannot be claimed")
            con.execute(
                """UPDATE tasks SET status='claimed',
                   execution_status='running',verdict='unassessed',
                   worker=?,attempt=attempt+1,lease_until=?,updated_at=?
                   WHERE id=?""",
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
                """UPDATE tasks SET status='running',
                   execution_status='running',lease_until=?,updated_at=?
                   WHERE id=? AND worker=?
                   AND status IN ('claimed','running')""",
                (
                    time.time() + max(30, lease_seconds),
                    now(), task_id, worker,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("task lease does not belong to worker")
        return self.get_task(task_id)

    def _finish_rejection(
        self, con: sqlite3.Connection, task_id: str, worker: str
    ) -> str:
        """Explain why finish matched no row: wrong worker vs wrong state."""
        row = con.execute(
            "SELECT status,worker FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        if row is None:
            return f"task does not exist: {task_id}"
        status = str(row["status"])
        if status not in self.FINISHABLE_STATES:
            action = self.FINISH_BLOCKED_ACTIONS.get(
                status, self.FINISH_BLOCKED_FALLBACK
            ).format(task_id=task_id, worker=worker)
            return (
                f"task {task_id} 处于 {status}，不可 finish：{action}"
            )
        return (
            "task cannot be finished by this worker: "
            f"{task_id} status={status}, "
            f"current holder={row['worker'] or '<none>'}, "
            f"requested worker={worker}"
        )

    def finish(
        self, task_id: str, worker: str, success: bool, result: dict
    ) -> dict:
        task = self.get_task(task_id)
        acceptance = (
            evaluate_acceptance(self.project, task["acceptance"], result)
            if success else {
                "execution_status": "not_run",
                "verdict": "unassessed",
                "authority": "machine",
                "freshness": "valid",
                "ok": False,
                "records": [],
            }
        )
        final_result = dict(result)
        final_result["acceptance"] = acceptance
        status = "completed" if success and acceptance["ok"] else "failed"
        verdict = "pass" if status == "completed" else "fail"
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status=?,execution_status='completed',
                   verdict=?,result=?,lease_until=NULL,updated_at=?
                   WHERE id=? AND worker=?
                   AND status IN ('claimed','running')""",
                (
                    status, verdict,
                    json.dumps(final_result, ensure_ascii=False), now(),
                    task_id, worker,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError(
                    self._finish_rejection(con, task_id, worker)
                )
        self.event(f"task.{status}", {
            "task_id": task_id,
            "worker": worker,
            "acceptance_ok": acceptance["ok"],
        })
        finished = self.get_task(task_id)
        if success and not acceptance["ok"]:
            failed = [
                item for item in acceptance["records"]
                if not item.get("ok")
            ]
            raise RuntimeError(
                f"任务验收失败: {task_id}: {failed}"
            )
        return finished

    def rebind_review_output(
        self, task_id: str, output_path: str
    ) -> dict:
        """Move a live legacy review task to an append-only output path."""
        target = normalize_owner(output_path)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if row is None:
                con.execute("ROLLBACK")
                raise ValueError("review task does not exist")
            task = self._decode(row)
            if task.get("task_type") != "independent_review":
                con.execute("ROLLBACK")
                raise ValueError("only independent-review tasks can be rebound")
            if task.get("status") not in self.ACTIVE_STATES:
                con.execute("ROLLBACK")
                raise ValueError("only live review tasks can be rebound")
            old_owners = task.get("owns", [])
            if len(old_owners) != 1:
                con.execute("ROLLBACK")
                raise ValueError("review task must own exactly one output")
            active = con.execute(
                "SELECT id,owns FROM tasks WHERE id<>? AND status IN "
                "('pending','claimed','running','recovery_pending')",
                (task_id,),
            ).fetchall()
            conflicts = [
                item["id"] for item in active
                if any(
                    owners_overlap(target, scope)
                    for scope in json.loads(item["owns"])
                )
            ]
            if conflicts:
                con.execute("ROLLBACK")
                raise ValueError(f"write scope conflicts: {conflicts}")
            acceptance = task.get("acceptance", [])
            replaced = False
            for item in acceptance:
                if (
                    isinstance(item, dict)
                    and item.get("kind") == "artifact_exists"
                    and normalize_owner(str(item.get("path", "")))
                    in old_owners
                ):
                    item["path"] = output_path
                    replaced = True
            if not replaced:
                acceptance.append({
                    "kind": "artifact_exists", "path": output_path,
                })
            con.execute(
                """UPDATE tasks SET owns=?,acceptance=?,updated_at=?
                   WHERE id=?""",
                (
                    json.dumps([target]),
                    json.dumps(acceptance),
                    now(),
                    task_id,
                ),
            )
            con.execute("COMMIT")
        self.event("task.review_output_rebound", {
            "task_id": task_id,
            "old_output": old_owners[0],
            "new_output": target,
        })
        return self.get_task(task_id)

    def mark_recovery_pending(
        self,
        task_id: str,
        worker: str,
        result: dict | None = None,
        note: str = "execution outcome is unknown",
    ) -> dict:
        payload = dict(result or {})
        payload["recovery_note"] = note
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status='recovery_pending',
                   execution_status='recovery_pending',
                   verdict='inconclusive',result=?,recovery_note=?,
                   lease_until=NULL,updated_at=?
                   WHERE id=? AND worker=?
                   AND status IN ('claimed','running')""",
                (
                    json.dumps(payload, ensure_ascii=False),
                    note, now(), task_id, worker,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError(
                    "task cannot enter recovery for this worker"
                )
        self.event(
            "task.recovery_pending", {"task_id": task_id, "note": note}
        )
        return self.get_task(task_id)

    def resolve_recovery(
        self,
        task_id: str,
        outcome_name: str,
        note: str,
        authority: str = "human",
    ) -> dict:
        if authority not in AUTHORITIES:
            raise ValueError(f"unknown authority: {authority}")
        choices = {
            "recovered_success": ("completed", "completed", "pass"),
            "confirmed_failed": ("failed", "error", "fail"),
            "safe_to_retry": ("pending", "not_run", "unassessed"),
            "human_required": (
                "recovery_pending", "recovery_pending", "inconclusive"
            ),
        }
        if outcome_name not in choices:
            raise ValueError(f"unknown recovery outcome: {outcome_name}")
        status, execution, verdict = choices[outcome_name]
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status=?,execution_status=?,verdict=?,
                   authority=?,worker=NULL,lease_until=NULL,recovery_note=?,
                   updated_at=? WHERE id=? AND status='recovery_pending'""",
                (
                    status, execution, verdict, authority,
                    note, now(), task_id,
                ),
            )
            if cur.rowcount != 1:
                raise ValueError("task is not recovery_pending")
        self.event("task.recovery_resolved", {
            "task_id": task_id,
            "outcome": outcome_name,
            "authority": authority,
            "note": note,
        })
        return self.get_task(task_id)

    def retry(self, task_id: str) -> dict:
        with self.connect() as con:
            cur = con.execute(
                """UPDATE tasks SET status='pending',
                   execution_status='not_run',verdict='unassessed',
                   freshness='valid',worker=NULL,lease_until=NULL,
                   result=NULL,recovery_note=NULL,updated_at=?
                   WHERE id=? AND status='failed'""",
                (now(), task_id),
            )
            if cur.rowcount != 1:
                raise ValueError("only failed tasks can be retried")
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
                   AND (? IS NULL OR contract_hash<>?
                        OR contract_hash IS NULL)
                   AND status IN
                       ('pending','claimed','running','recovery_pending')""",
                (work_item_id, contract_hash, contract_hash),
            ).fetchall()
            ids = [row["id"] for row in rows]
            for task_id in ids:
                con.execute(
                    """UPDATE tasks SET status='superseded',
                       execution_status='cancelled',
                       verdict='inconclusive',freshness='stale',
                       worker=NULL,lease_until=NULL,superseded_at=?,
                       updated_at=? WHERE id=?""",
                    (timestamp, timestamp, task_id),
                )
        for task_id in ids:
            self.event("task.superseded", {
                "task_id": task_id, "work_item_id": work_item_id
            })
        return ids

    def reconcile(self, max_attempts: int = 3) -> list[str]:
        recovered = []
        events = []
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                """SELECT id,attempt,budget,idempotent FROM tasks
                   WHERE status IN ('claimed','running')
                   AND lease_until IS NOT NULL AND lease_until < ?""",
                (time.time(),),
            ).fetchall()
            for row in rows:
                budget = json.loads(row["budget"] or "{}")
                task_max = int(budget.get("max_attempts", max_attempts))
                if not bool(row["idempotent"]):
                    status = "recovery_pending"
                    execution = "recovery_pending"
                    verdict = "inconclusive"
                elif row["attempt"] >= task_max:
                    status = "failed"
                    execution = "error"
                    verdict = "inconclusive"
                else:
                    status = "pending"
                    execution = "not_run"
                    verdict = "unassessed"
                con.execute(
                    """UPDATE tasks SET status=?,execution_status=?,
                       verdict=?,worker=NULL,lease_until=NULL,
                       recovery_note=?,updated_at=? WHERE id=?""",
                    (
                        status, execution, verdict, "lease expired",
                        now(), row["id"],
                    ),
                )
                recovered.append(row["id"])
                events.append((row["id"], status))
            con.execute("COMMIT")
        for task_id, status in events:
            self.event("task.lease_expired", {
                "task_id": task_id, "new_status": status
            })
        return recovered

    def invalidate_for_evidence(
        self, evidence_ids: list[str], reason: str
    ) -> list[str]:
        targets = set(evidence_ids)
        from .problem_graph import ProblemGraph
        graph = ProblemGraph(self.project)
        nodes = graph.nodes if graph.exists else {}
        work_items = {
            node_id for node_id, node in nodes.items()
            if any(output["evidence_id"] in targets for output in node.get("outputs", []))
        }
        invalidated = []
        with self.connect() as con:
            rows = con.execute(
                "SELECT id,acceptance,work_item_id,task_type FROM tasks WHERE status='completed'"
            ).fetchall()
            for row in rows:
                acceptance = json.loads(row["acceptance"] or "[]")
                referenced = {
                    str(item.get("id"))
                    for item in acceptance
                    if isinstance(item, dict)
                    and item.get("kind") == "evidence_exists"
                }
                is_review = (
                    row["task_type"] == "independent_review"
                    and row["work_item_id"] in work_items
                )
                if not targets.intersection(referenced) and not is_review:
                    continue
                con.execute(
                    """UPDATE tasks SET status='invalidated',
                       freshness='stale',verdict='inconclusive',
                       recovery_note=?,updated_at=? WHERE id=?""",
                    (reason, now(), row["id"]),
                )
                invalidated.append(row["id"])
        for task_id in invalidated:
            self.event("task.invalidated", {
                "task_id": task_id,
                "evidence_ids": sorted(targets),
                "reason": reason,
            })
        return invalidated
