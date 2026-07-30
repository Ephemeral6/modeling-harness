import copy
import json
from pathlib import Path

import pytest

from modelharness.evidence import EvidenceGraph
from modelharness.problem_graph import ProblemGraph
from modelharness.scaffold import create
from modelharness.scheduler import AdaptiveScheduler
from modelharness.util import sha256
from modelharness.workflow import WorkflowEngine


def test_stale_approve_cannot_verify_revised_artifact(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    graph = ProblemGraph(root)
    proposal = copy.deepcopy(graph.data)
    proposal["nodes"]["s0.problem_definition"]["reviews"] = [{
        "role": "problem-reviewer",
        "path": "reviews/s0_problem.json",
    }]
    graph.replace(proposal, "require review")
    contract = ProblemGraph(root).contract_hash("s0.problem_definition")
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        "review-v1",
        "s0",
        "problem-reviewer",
        "review",
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
        "reviewer": "problem-reviewer",
        "task_id": task["id"],
        "contract_hash": contract,
        "verdict": "APPROVE",
        "scope": ["statement"],
        "findings": [],
        "required_fixes": [],
        "evidence_checked": ["problem.statement"],
        "artifact_hashes": {
            "problem/statement.md": sha256(root / "problem" / "statement.md")
        },
    }), encoding="utf-8")
    workflow.claim(task["id"], "reviewer")
    workflow.finish(task["id"], "reviewer", True, {})
    evidence = EvidenceGraph(root)
    evidence.add(
        "problem.statement",
        "problem",
        "statement",
        "problem/statement.md",
    )
    evidence.verify("problem.statement")
    (root / "problem" / "statement.md").write_text(
        "revised", encoding="utf-8"
    )
    evidence.revise("problem.statement", reason="new statement")
    evidence.add(
        "problem.success",
        "problem",
        "success",
        "docs/success_criteria.md",
    )
    packet = AdaptiveScheduler(root).next_packet()
    assert packet["phase"] == "review"
    assert any(
        item.get("task_type") == "independent_review"
        and item.get("id") != task["id"]
        for item in packet["tasks"]
    )
    with pytest.raises(RuntimeError, match="证据验证失败"):
        evidence.verify("problem.statement")
