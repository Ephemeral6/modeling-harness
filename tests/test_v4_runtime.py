from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from modelharness.checks import evaluate_acceptance
from modelharness.evidence import EvidenceGraph
from modelharness.problem_graph import ProblemGraph
from modelharness.proposals import ProposalService
from modelharness.scaffold import create
from modelharness.supervisor import StateCapsule
from modelharness.toolchain_execution import ToolRunExecutor
from modelharness.util import sha256
from modelharness.workflow import WorkflowEngine


def test_not_run_is_not_pass(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    result = evaluate_acceptance(root, [])
    assert result["execution_status"] == "not_run"
    assert result["verdict"] == "unassessed"
    assert result["authority"] == "machine"
    assert result["ok"] is False


def test_non_idempotent_expired_lease_requires_recovery(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    engine = WorkflowEngine(root)
    task = engine.ensure_task(
        "external",
        "s0",
        "worker",
        "external side effect",
        ["results/external.json"],
        acceptance=[{
            "kind": "artifact_exists",
            "path": "results/external.json",
        }],
        side_effect_class="external_write",
        idempotent=False,
    )
    engine.claim(task["id"], "worker")
    with engine.connect() as con:
        con.execute(
            "UPDATE tasks SET lease_until=0 WHERE id=?", (task["id"],)
        )
    assert engine.reconcile() == [task["id"]]
    pending = engine.get_task(task["id"])
    assert pending["status"] == "recovery_pending"
    assert pending["execution_status"] == "recovery_pending"
    assert pending["verdict"] == "inconclusive"
    with pytest.raises(ValueError):
        engine.retry(task["id"])
    recovered = engine.resolve_recovery(
        task["id"], "safe_to_retry", "remote transaction absent"
    )
    assert recovered["status"] == "pending"
    assert recovered["authority"] == "human"


def test_evidence_freshness_and_cascade_invalidation(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    (root / "results" / "root.txt").write_text("root", encoding="utf-8")
    (root / "results" / "child.txt").write_text("child", encoding="utf-8")
    graph = EvidenceGraph(root)
    graph.add("result.root", "result", "root", "results/root.txt")
    graph.verify("result.root")
    graph.add(
        "result.child",
        "result",
        "child",
        "results/child.txt",
        depends=["result.root"],
    )
    graph.verify("result.child")
    (root / "results" / "child.txt").write_text(
        "changed", encoding="utf-8"
    )
    assert graph.freshness("result.child") == "tampered"
    affected = graph.revoke("result.root", "source computation was wrong")
    nodes = EvidenceGraph(root).nodes
    assert affected == ["result.root", "result.child"]
    assert nodes["result.root"]["status"] == "revoked"
    assert nodes["result.child"]["status"] == "invalidated"


def test_producer_worker_cannot_approve_own_evidence(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    problem = ProblemGraph(root)
    proposal = copy.deepcopy(problem.data)
    proposal["nodes"]["s0.problem_definition"]["reviews"] = [{
        "role": "reviewer",
        "path": "reviews/s0_problem.json",
    }]
    problem.replace(proposal, "require independent review")
    contract = ProblemGraph(root).contract_hash("s0.problem_definition")
    workflow = WorkflowEngine(root)
    producer = workflow.ensure_task(
        "producer",
        "s0",
        "builder",
        "produce statement",
        ["problem/statement.md"],
        acceptance=[{
            "kind": "artifact_exists",
            "path": "problem/statement.md",
        }],
        work_item_id="s0.problem_definition",
        task_type="problem_formulation",
        contract_hash=contract,
    )
    workflow.claim(producer["id"], "same-worker")
    workflow.finish(producer["id"], "same-worker", True, {})
    reviewer = workflow.ensure_task(
        "reviewer",
        "s0",
        "reviewer",
        "review statement",
        ["reviews/s0_problem.json"],
        acceptance=[{
            "kind": "artifact_exists",
            "path": "reviews/s0_problem.json",
        }],
        work_item_id="s0.problem_definition",
        task_type="independent_review",
        contract_hash=contract,
    )
    review_path = root / "reviews" / "s0_problem.json"
    review_path.write_text(json.dumps({
        "reviewer": "reviewer",
        "task_id": reviewer["id"],
        "contract_hash": contract,
        "verdict": "APPROVE",
        "scope": ["statement"],
        "findings": [],
        "required_fixes": [],
        "evidence_checked": ["problem.statement"],
        "artifact_hashes": {
            "problem/statement.md": sha256(
                root / "problem" / "statement.md"
            )
        },
    }), encoding="utf-8")
    workflow.claim(reviewer["id"], "same-worker")
    workflow.finish(reviewer["id"], "same-worker", True, {})
    evidence = EvidenceGraph(root)
    evidence.add(
        "problem.statement",
        "problem",
        "statement",
        "problem/statement.md",
        producer_task_id=producer["id"],
    )
    with pytest.raises(RuntimeError, match="证据验证失败"):
        evidence.verify("problem.statement")


def test_policy_is_permissive_locally_and_escalates_new_authority(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "demo")
    proposals = ProposalService(root)
    local = proposals.submit(
        action="experiment.run",
        actor="agent",
        reason="falsify the leading model",
    )
    assert local["policy"]["decision"] == "ALLOW"
    network = proposals.submit(
        action="data.download",
        actor="agent",
        reason="obtain a missing external series",
        risk="network",
    )
    assert network["policy"]["decision"] == "ESCALATE_TO_USER"


def test_state_capsule_derives_five_ledgers(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    capsule = StateCapsule(root).build()
    assert len(capsule["state_hash"]) == 64
    assert set(capsule["ledgers"]) == {
        "task", "progress", "failure", "resource", "opportunity",
    }


def test_tool_timeout_is_recovery_pending(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    executor = ToolRunExecutor(root)
    record = executor.run(
        node_id="s0.problem_definition",
        contract_hash="contract",
        decision_hash="decision",
        tool_ids=["python"],
        argv=[
            "python", "-c", "import time; time.sleep(2)",
        ],
        inputs=["problem/statement.md"],
        outputs=["problem/statement.md"],
        validators=[{
            "kind": "file_nonempty",
            "path": "problem/statement.md",
        }],
        timeout=1,
    )
    assert record["execution_status"] == "recovery_pending"
    assert record["verification"]["status"] == "recovery_pending"
    resolved = executor.recover(
        record["id"], "safe_to_retry", "process no longer exists"
    )
    assert resolved["verification"]["status"] == "safe_to_retry"
