from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from modelharness.evidence import EvidenceGraph
from modelharness.narrative import build_brief
from modelharness.problem_graph import ProblemGraph
from modelharness.profiles import ProfileService
from modelharness.scaffold import create
from modelharness.storage import read_json
from modelharness.util import sha256
from modelharness.workflow import WorkflowEngine


@pytest.mark.parametrize(
    ("profile", "expected_ids"),
    [
        (
            "cumcm",
            {"result.subquestions", "narrative.algorithm_process"},
        ),
        ("mcm_icm", {"narrative.summary"}),
        ("real_world", {"decision.packet", "narrative.monitoring"}),
        ("general", set()),
    ],
)
def test_profile_overlays_change_machine_delivery_obligations(
    tmp_path: Path, profile: str, expected_ids: set[str]
):
    root = create(tmp_path / profile, "demo")
    before = ProblemGraph(root).data["revision"]
    ProfileService(root).use(profile)
    graph = ProblemGraph(root)
    delivery = graph.nodes["s6.delivery"]
    managed = {
        output["evidence_id"]
        for output in delivery["outputs"]
        if output.get("profile_managed")
    }
    assert managed == expected_ids
    assert graph.data["revision"] == before + 1
    owns = set(delivery["workstreams"][0]["owns"])
    assert {
        output["artifact"]
        for output in delivery["outputs"]
        if output.get("profile_managed")
    }.issubset(owns)


def test_v3_review_must_bind_contract_scope_task_and_artifact_hash(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "demo")
    graph = ProblemGraph(root)
    proposal = copy.deepcopy(graph.data)
    proposal["nodes"]["s0.problem_definition"]["reviews"] = [{
        "role": "problem-reviewer",
        "path": "reviews/s0_problem.json",
    }]
    graph.replace(proposal, "require cold review for S0")
    review_path = root / "reviews" / "s0_problem.json"
    review_path.write_text(
        json.dumps({"verdict": "APPROVE"}), encoding="utf-8"
    )
    evidence = EvidenceGraph(root)
    evidence.add(
        "problem.statement",
        "problem",
        "statement",
        "problem/statement.md",
    )
    with pytest.raises(RuntimeError, match="证据验证失败"):
        evidence.verify("problem.statement")
    contract = ProblemGraph(root).contract_hash("s0.problem_definition")
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        "cold-review",
        "s0",
        "problem-reviewer",
        "cold review",
        ["reviews/s0_problem.json"],
        acceptance=[{
            "kind": "artifact_exists",
            "path": "reviews/s0_problem.json",
        }],
        work_item_id="s0.problem_definition",
        task_type="independent_review",
        contract_hash=contract,
    )
    review_path.write_text(json.dumps({
        "reviewer": "problem-reviewer",
        "task_id": task["id"],
        "contract_hash": contract,
        "verdict": "APPROVE",
        "scope": ["problem statement"],
        "findings": [],
        "required_fixes": [],
        "evidence_checked": ["problem.statement"],
        "artifact_hashes": {
            "problem/statement.md": sha256(root / "problem" / "statement.md")
        },
    }), encoding="utf-8")
    workflow.claim(task["id"], "reviewer-worker")
    workflow.finish(task["id"], "reviewer-worker", True, {})
    evidence.revise(
        "problem.statement",
        reason="bind completed cold review",
    )
    verified = evidence.verify("problem.statement")
    assert verified["status"] == "verified"


def test_narrative_build_emits_profile_delivery_manifest(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    ProfileService(root).use("cumcm")
    build_brief(root)
    manifest = read_json(root / "paper" / "delivery_manifest.json")
    assert manifest["profile"] == "cumcm"
    outputs = {
        item["evidence_id"]
        for node in manifest["delivery_nodes"]
        for item in node["outputs"]
    }
    assert "result.subquestions" in outputs
    assert "narrative.algorithm_process" in outputs
