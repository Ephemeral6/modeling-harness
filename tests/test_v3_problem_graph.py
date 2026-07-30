from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from modelharness.autopilot import next_packet
from modelharness.evidence import EvidenceGraph
from modelharness.method_packs import MethodPackRegistry
from modelharness.problem_graph import ProblemGraph, validate_problem_graph
from modelharness.profiles import ProfileService
from modelharness.scaffold import create
from modelharness.stages import StageService
from modelharness.workflow import WorkflowEngine


def test_problem_graph_rejects_cycles_and_computes_frontier(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    graph = ProblemGraph(root)
    data = copy.deepcopy(graph.data)
    data["nodes"]["s0.problem_definition"]["depends_on"] = ["s6.delivery"]
    with pytest.raises(ValueError, match="存在环"):
        validate_problem_graph(data)
    packet = next_packet(root)
    assert packet["frontier"][0]["id"] == "s0.problem_definition"
    assert packet["frontier"][0]["priority"] == 20


def test_task_acceptance_is_enforced_and_retryable(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    engine = WorkflowEngine(root)
    task = engine.ensure_task(
        "acceptance",
        "s0",
        "worker",
        "produce artifact",
        ["results/accepted.json"],
        acceptance=[
            {"kind": "artifact_exists", "path": "results/accepted.json"},
            {
                "kind": "json_fields",
                "path": "results/accepted.json",
                "fields": ["answer.value"],
            },
        ],
        budget={"max_attempts": 2},
        work_item_id="s0.problem_definition",
        task_type="test",
        contract_hash="abc",
    )
    engine.claim(task["id"], "w")
    with pytest.raises(RuntimeError, match="验收失败"):
        engine.finish(task["id"], "w", True, {})
    assert engine.get_task(task["id"])["status"] == "failed"
    (root / "results" / "accepted.json").write_text(
        json.dumps({"answer": {"value": 1}}), encoding="utf-8"
    )
    engine.retry(task["id"])
    engine.claim(task["id"], "w")
    finished = engine.finish(task["id"], "w", True, {"artifact": "ok"})
    assert finished["status"] == "completed"
    assert finished["result"]["acceptance"]["ok"]


def test_method_pack_lock_detects_tampering(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    registry = MethodPackRegistry(root)
    assert not registry.audit()
    path = root / "config" / "method_packs" / "generic.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["version"] = "tampered"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert registry.audit()
    with pytest.raises(RuntimeError, match="方法包内容与锁文件不匹配"):
        # S0 evidence is deliberately absent; the pack error must still surface.
        StageService(root).gate("s0")


def test_problem_contract_change_invalidates_stamp_without_global_state_reset(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "demo")
    evidence = EvidenceGraph(root)
    for node_id, artifact in (
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ):
        evidence.add(node_id, "problem", node_id, artifact)
        evidence.verify(node_id)
    StageService(root).gate("s0")
    assert StageService(root).current() == "s1"
    graph = ProblemGraph(root)
    proposal = copy.deepcopy(graph.data)
    proposal["nodes"]["s0.problem_definition"]["question"] += "（修订）"
    result = graph.replace(proposal, "tighten success criterion")
    assert result["changed_nodes"] == ["s0.problem_definition"]
    assert StageService(root).current() == "s0"
    assert any(
        "problem_closure_sha256" in item
        for item in StageService(root).validate_stamp("s0")
    )


def test_profile_change_is_signed_by_milestone(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    evidence = EvidenceGraph(root)
    for node_id, artifact in (
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ):
        evidence.add(node_id, "problem", node_id, artifact)
        evidence.verify(node_id)
    StageService(root).gate("s0")
    ProfileService(root).use("cumcm")
    errors = StageService(root).validate_stamp("s0")
    assert any("delivery_profile_sha256" in item for item in errors)


def test_v2_project_without_problem_graph_uses_legacy_scheduler(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    (root / ".harness" / "problem_graph.json").unlink()
    packet = next_packet(root)
    assert packet["scheduler"] == "legacy_v2"
    assert packet["phase"] == "build_evidence"
    assert {
        item["role"] for item in WorkflowEngine(root).list_tasks("s0")
    } == {"problem-architect", "data-scout"}
