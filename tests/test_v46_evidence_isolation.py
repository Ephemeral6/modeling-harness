"""4.6 机制：evidence verify 的验证者身份隔离（硬不变量 5 的机械落点）。

覆盖三条路径：库层 EvidenceGraph.verify、CLI `evidence verify --worker`，
以及不带身份的旧调用（保持可用，只打 isolation=unverified 标记）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modelharness import cli_v31
from modelharness.evidence import EvidenceGraph
from modelharness.profiles import ProfileService
from modelharness.scaffold import create
from modelharness.stages import StageService
from modelharness.storage import atomic_write_json, read_json
from modelharness.workflow import WorkflowEngine


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def _project(tmp_path: Path, name: str = "case") -> Path:
    return create(tmp_path / name, "demo")


def _producer_task(
    root: Path, worker: str = "solver-agent", key: str = "produce-statement"
) -> dict:
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        key,
        "s0",
        "problem-architect",
        "写题面",
        ["problem/statement.md"],
        acceptance=[{
            "kind": "artifact_exists", "path": "problem/statement.md",
        }],
        work_item_id="s0.problem_definition",
        task_type="problem_formulation",
    )
    workflow.claim(task["id"], worker)
    workflow.finish(task["id"], worker, True, {})
    return task


def _register(
    root: Path, producer_task_id: str | None = None
) -> EvidenceGraph:
    graph = EvidenceGraph(root)
    graph.add(
        "problem.statement",
        "problem",
        "题面",
        "problem/statement.md",
        producer_task_id=producer_task_id,
    )
    return graph


# ----------------------------------------------------------------- 库层路径


def test_library_rejects_producer_worker_and_producer_task(tmp_path: Path):
    root = _project(tmp_path)
    task = _producer_task(root)
    graph = _register(root, task["id"])

    with pytest.raises(ValueError, match="生成者不得自验"):
        graph.verify("problem.statement", worker="solver-agent")
    with pytest.raises(ValueError, match="生成者不得自验"):
        graph.verify("problem.statement", verifier_task_id=task["id"])
    assert graph.nodes["problem.statement"]["status"] == "candidate"

    verified = graph.verify("problem.statement", worker="review-agent")
    binding = verified["verification"]["binding"]
    assert verified["status"] == "verified"
    assert binding["isolation"] == "isolated"
    assert binding["verifier_worker"] == "review-agent"
    assert binding["verifier_task_id"] is None
    assert binding["producer_worker"] == "solver-agent"
    assert binding["producer_task_id"] == task["id"]


def test_library_binds_verifier_task_identity(tmp_path: Path):
    root = _project(tmp_path)
    producer = _producer_task(root)
    workflow = WorkflowEngine(root)
    reviewer = workflow.ensure_task(
        "review-statement",
        "s0",
        "problem-reviewer",
        "冷启动复核",
        ["reviews/s0_problem.json"],
        acceptance=[{
            "kind": "artifact_exists", "path": "reviews/s0_problem.json",
        }],
        work_item_id="s0.problem_definition",
        task_type="independent_review",
    )
    workflow.claim(reviewer["id"], "review-agent")
    graph = _register(root, producer["id"])

    with pytest.raises(ValueError, match="verifier task 不存在"):
        graph.verify("problem.statement", verifier_task_id="does-not-exist")
    with pytest.raises(ValueError, match="worker 不一致"):
        graph.verify(
            "problem.statement",
            worker="solver-agent",
            verifier_task_id=reviewer["id"],
        )
    with pytest.raises(ValueError, match="不能为空"):
        graph.verify("problem.statement", worker="   ")

    verified = graph.verify(
        "problem.statement", verifier_task_id=reviewer["id"]
    )
    binding = verified["verification"]["binding"]
    assert binding["isolation"] == "isolated"
    # worker 未显式给出时从 verifier task 的租约身份推出。
    assert binding["verifier_worker"] == "review-agent"
    assert binding["verifier_task_id"] == reviewer["id"]


# --------------------------------------------------------------- 兼容性路径


def test_legacy_call_without_worker_still_verifies_but_is_marked(
    tmp_path: Path,
):
    root = _project(tmp_path)
    task = _producer_task(root)
    graph = _register(root, task["id"])

    verified = graph.verify("problem.statement")

    assert verified["status"] == "verified"
    assert verified["verification"]["binding"]["isolation"] == "unverified"
    assert verified["verification"]["binding"]["verifier_worker"] is None
    # general profile 下不因缺身份而判失败：旧项目与旧调用不破。
    assert graph.audit() == []


def test_legacy_flow_still_gates_s0_without_worker(tmp_path: Path):
    root = _project(tmp_path)
    graph = EvidenceGraph(root)
    for node_id, artifact in (
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ):
        graph.add(node_id, "problem", node_id, artifact)
        graph.verify(node_id)

    assert StageService(root).gate("s0")["stage"] == "s0"
    assert StageService(root).current() == "s1"


def test_pre_46_stamps_without_isolation_key_are_grandfathered(
    tmp_path: Path,
):
    root = _project(tmp_path)
    graph = _register(root)
    graph.verify("problem.statement")
    # 模拟 4.6.0 之前落盘的 verification.binding（没有 isolation 字段）。
    data = read_json(root / ".harness" / "evidence.json")
    binding = data["nodes"]["problem.statement"]["verification"]["binding"]
    for field in ("isolation", "verifier_worker", "verifier_task_id",
                  "producer_worker"):
        binding.pop(field, None)
    atomic_write_json(root / ".harness" / "evidence.json", data)
    ProfileService(root).use("cumcm")

    assert EvidenceGraph(root).audit() == []


# ------------------------------------------------------------- 竞赛 profile


def test_competition_profile_requires_a_declared_verifier(tmp_path: Path):
    root = _project(tmp_path)
    ProfileService(root).use("cumcm")
    task = _producer_task(root)
    graph = _register(root, task["id"])

    with pytest.raises(ValueError, match="要求验证者身份隔离"):
        graph.verify("problem.statement")
    assert graph.nodes["problem.statement"]["status"] == "candidate"

    verified = graph.verify("problem.statement", worker="review-agent")
    assert verified["verification"]["binding"]["isolation"] == "isolated"
    assert graph.audit() == []


@pytest.mark.parametrize("profile", ["cumcm", "mcm_icm"])
def test_competition_profile_audit_flags_self_verifiable_evidence(
    tmp_path: Path, profile: str
):
    root = _project(tmp_path, profile)
    graph = _register(root)
    graph.verify("problem.statement")  # general profile 下合法
    ProfileService(root).use(profile)

    errors = EvidenceGraph(root).audit()

    assert any(
        "isolation=unverified" in item and "生成者不得自验" in item
        for item in errors
    ), errors
    with pytest.raises(RuntimeError, match="isolation=unverified"):
        StageService(root).gate("s0")


# ------------------------------------------------------------------ CLI 路径


def test_cli_verify_exposes_worker_and_refuses_self_verification(
    tmp_path: Path, monkeypatch, capsys
):
    root = _project(tmp_path)
    task = _producer_task(root)
    _register(root, task["id"])

    code = run_cli(
        monkeypatch, "evidence", "verify", "problem.statement",
        "--project", str(root), "--worker", "solver-agent",
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    assert "生成者不得自验" in payload["message"]
    assert "--worker" in payload["message"]
    assert EvidenceGraph(root).nodes["problem.statement"]["status"] == (
        "candidate"
    )

    code = run_cli(
        monkeypatch, "evidence", "verify", "problem.statement",
        "--project", str(root), "--verifier-task", task["id"],
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1, "verifier task 等于 producer task 必须被拒"
    assert "producer task 不能自验" in payload["message"]

    code = run_cli(
        monkeypatch, "evidence", "verify", "problem.statement",
        "--project", str(root), "--worker", "review-agent",
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "verified"
    assert payload["verification"]["binding"]["isolation"] == "isolated"
    assert payload["verification"]["binding"]["verifier_worker"] == (
        "review-agent"
    )


def test_cli_verify_without_worker_keeps_working_and_marks_unverified(
    tmp_path: Path, monkeypatch, capsys
):
    root = _project(tmp_path)
    _register(root)

    code = run_cli(
        monkeypatch, "evidence", "verify", "problem.statement",
        "--project", str(root),
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["status"] == "verified"
    assert payload["verification"]["binding"]["isolation"] == "unverified"

    ProfileService(root).use("cumcm")
    code = run_cli(monkeypatch, "evidence", "audit", "--project", str(root))
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert any("isolation=unverified" in item for item in payload["errors"])
