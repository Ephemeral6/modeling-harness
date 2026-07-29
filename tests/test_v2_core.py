from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from modelharness.autopilot import next_packet
from modelharness.evidence import EvidenceGraph
from modelharness.intake import intake
from modelharness.scaffold import create
from modelharness.stages import StageService
from modelharness.storage import CorruptStateError, read_json
from modelharness.workflow import WorkflowEngine


def add_s0_evidence(root: Path) -> EvidenceGraph:
    graph = EvidenceGraph(root)
    for node_id, artifact in [
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ]:
        graph.add(node_id, "problem", node_id, artifact)
        graph.verify(node_id)
    return graph


def test_gate_chain_rejects_forgery_and_tamper(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    add_s0_evidence(root)
    forged = root / ".harness" / "stamps" / "s0.json"
    forged.write_text('{"stage":"s0"}', encoding="utf-8")
    assert StageService(root).current() == "s0"
    forged.unlink()
    StageService(root).gate("s0")
    assert StageService(root).current() == "s1"
    (root / "problem" / "statement.md").write_text("tampered", encoding="utf-8")
    assert StageService(root).current() == "s0"
    assert StageService(root).validate_stamp("s0")


def test_concurrent_evidence_updates_do_not_lose_nodes(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    for index in range(8):
        (root / "results" / f"{index}.txt").write_text(str(index), encoding="utf-8")

    def add(index: int):
        EvidenceGraph(root).add(
            f"result.n{index}", "result", str(index), f"results/{index}.txt"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, range(8)))
    assert len(EvidenceGraph(root).nodes) == 8
    assert not list((root / ".harness").glob("*.lock"))


def test_corrupt_state_fails_closed(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    path = root / ".harness" / "evidence.json"
    path.write_text('{"nodes":', encoding="utf-8")
    with pytest.raises(CorruptStateError):
        EvidenceGraph(root).audit()


def test_intake_is_unique_and_transactional_enough_for_retries(tmp_path: Path):
    harness = tmp_path / "harness"
    harness.mkdir()
    source = tmp_path / "题面.txt"
    source.write_text("求最优调度方案", encoding="utf-8")
    first = intake(harness, "真实题目", "开始", [source])
    second = intake(harness, "真实题目", "重试", [source])
    assert first["project"] != second["project"]
    for result in (first, second):
        project = Path(result["project"])
        manifest = read_json(project / "problem" / "intake_manifest.json")
        stored = project / manifest["files"][0]["stored_as"]
        assert stored.is_file()
        assert manifest["files"][0]["sha256"]
    current = read_json(harness / "projects" / ".current.json")
    assert Path(second["project"]).name in current["project"]


def test_task_ownership_lease_and_recovery(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    engine = WorkflowEngine(root)
    task = engine.ensure_task(
        "s3:solver", "s3", "solver", "solve", ["src", "results/nominal.json"]
    )
    with pytest.raises(ValueError):
        engine.ensure_task(
            "s3:conflict", "s3", "other", "conflict", ["src/solver.py"]
        )
    claimed = engine.claim(task["id"], "worker-1", lease_seconds=30)
    assert claimed["attempt"] == 1
    with sqlite3.connect(engine.path) as con:
        con.execute("UPDATE tasks SET lease_until=0 WHERE id=?", (task["id"],))
    assert engine.reconcile(max_attempts=3) == [task["id"]]
    assert engine.get_task(task["id"])["status"] == "pending"


def test_autopilot_creates_durable_parallel_tasks(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    packet = next_packet(root)
    assert packet["stage"] == "s0"
    assert packet["phase"] == "build_evidence"
    tasks = WorkflowEngine(root).list_tasks("s0")
    assert {item["role"] for item in tasks} == {"problem-architect", "data-scout"}
    # Repeated ticks are idempotent.
    next_packet(root)
    assert len(WorkflowEngine(root).list_tasks("s0")) == 2
