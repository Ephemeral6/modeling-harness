from __future__ import annotations

import copy
import json
from pathlib import Path

from modelharness.evidence import EvidenceGraph
from modelharness.problem_graph import ProblemGraph
from modelharness.review_store import resolve_review
from modelharness.scaffold import create
from modelharness.scheduler import AdaptiveScheduler
from modelharness.util import sha256
from modelharness.workflow import WorkflowEngine


def _require_s0_review(root: Path) -> str:
    graph = ProblemGraph(root)
    proposal = copy.deepcopy(graph.data)
    proposal["nodes"]["s0.problem_definition"]["reviews"] = [{
        "role": "problem-reviewer",
        "path": "reviews/s0_problem.json",
    }]
    graph.replace(proposal, "require append-only S0 review")
    return ProblemGraph(root).contract_hash("s0.problem_definition")


def _review(
    task_id: str,
    contract: str,
    verdict: str,
    artifact_hash: str,
    success_hash: str,
) -> dict:
    artifact_hashes = {
        "problem/statement.md": artifact_hash,
        "docs/success_criteria.md": success_hash,
    }
    return {
        "reviewer": "problem-reviewer",
        "task_id": task_id,
        "contract_hash": contract,
        "verdict": verdict,
        "scope": ["problem statement"],
        "findings": [] if verdict == "APPROVE" else ["repair required"],
        "required_fixes": [] if verdict == "APPROVE" else ["repair"],
        "evidence_checked": ["problem.statement"],
        "artifact_hashes": artifact_hashes,
    }


def test_versioned_approve_supersedes_reject_without_overwrite(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "demo")
    contract = _require_s0_review(root)
    statement = root / "problem" / "statement.md"
    old_hash = sha256(statement)
    canonical = root / "reviews" / "s0_problem.json"
    canonical.write_text(
        json.dumps(_review(
            "old-review",
            contract,
            "REJECT",
            old_hash,
            sha256(root / "docs" / "success_criteria.md"),
        )),
        encoding="utf-8",
    )
    canonical_before = canonical.read_bytes()
    statement.write_text("repaired statement", encoding="utf-8")

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
    workflow.claim(producer["id"], "producer-worker")
    workflow.finish(producer["id"], "producer-worker", True, {})
    reviewer = workflow.ensure_task(
        "review-v2",
        "s0",
        "problem-reviewer",
        "review repaired statement",
        ["reviews/s0_problem_v2.json"],
        acceptance=[{
            "kind": "artifact_exists",
            "path": "reviews/s0_problem_v2.json",
        }],
        work_item_id="s0.problem_definition",
        task_type="independent_review",
        contract_hash=contract,
    )
    versioned = root / "reviews" / "s0_problem_v2.json"
    versioned.write_text(
        json.dumps(_review(
            reviewer["id"],
            contract,
            "APPROVE",
            sha256(statement),
            sha256(root / "docs" / "success_criteria.md"),
        )),
        encoding="utf-8",
    )
    workflow.claim(reviewer["id"], "reviewer-worker")
    workflow.finish(reviewer["id"], "reviewer-worker", True, {})

    evidence = EvidenceGraph(root)
    evidence.add(
        "problem.statement",
        "problem",
        "repaired statement",
        "problem/statement.md",
        producer_task_id=producer["id"],
    )
    verified = evidence.verify("problem.statement")
    resolution = resolve_review(
        root,
        "reviews/s0_problem.json",
        contract_hash=contract,
        artifact_hashes={"problem/statement.md": sha256(statement)},
    )

    assert verified["status"] == "verified"
    assert resolution is not None
    assert resolution["path"] == "reviews/s0_problem_v2.json"
    assert canonical.read_bytes() == canonical_before


def test_lineage_orders_double_digit_versions(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    reviews = root / "reviews"
    reviews.mkdir(exist_ok=True)
    for name in (
        "s6_paper_audit.json",
        "s6_paper_audit_v2.json",
        "s6_paper_audit_v9.json",
        "s6_paper_audit_v10.json",
        "s6_paper_audit_v11.json",
    ):
        (reviews / name).write_text("{}", encoding="utf-8")

    from modelharness.review_store import latest_review_path, review_candidates

    versions = [
        version
        for version, _ in review_candidates(root, "reviews/s6_paper_audit.json")
    ]
    latest = latest_review_path(root, "reviews/s6_paper_audit.json")

    assert versions == [1, 2, 9, 10, 11], (
        "谱系漏掉了两位数版本号：_v10/_v11 若被正则排除，门禁会把 _v9 当最新裁决"
    )
    assert latest is not None and latest.name == "s6_paper_audit_v11.json"


def test_scheduler_allocates_new_review_version(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    contract = _require_s0_review(root)
    statement = root / "problem" / "statement.md"
    canonical = root / "reviews" / "s0_problem.json"
    canonical.write_text(
        json.dumps(_review(
            "old-review",
            contract,
            "REJECT",
            sha256(statement),
            sha256(root / "docs" / "success_criteria.md"),
        )),
        encoding="utf-8",
    )
    canonical_before = canonical.read_bytes()
    statement.write_text("repaired again", encoding="utf-8")
    EvidenceGraph(root).add(
        "problem.statement",
        "problem",
        "candidate",
        "problem/statement.md",
    )
    EvidenceGraph(root).add(
        "problem.success",
        "problem",
        "candidate success criteria",
        "docs/success_criteria.md",
    )

    packet = AdaptiveScheduler(root).next_packet()
    review_tasks = [
        task for task in packet["tasks"]
        if task.get("task_type") == "independent_review"
    ]

    assert packet["phase"] == "review"
    assert len(review_tasks) == 1
    assert review_tasks[0]["owns"] == ["reviews/s0_problem_v2.json"]
    assert canonical.read_bytes() == canonical_before


def test_existing_v2_draft_rebinds_live_legacy_task(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    contract = _require_s0_review(root)
    statement = root / "problem" / "statement.md"
    current_hash = sha256(statement)
    canonical = root / "reviews" / "s0_problem.json"
    canonical.write_text(
        json.dumps(_review(
            "old-review",
            contract,
            "REJECT",
            current_hash,
            sha256(root / "docs" / "success_criteria.md"),
        )),
        encoding="utf-8",
    )
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        "review:s0.problem_definition:reviews/s0_problem.json:legacy",
        "s0",
        "problem-reviewer",
        "legacy canonical review task",
        ["reviews/s0_problem.json"],
        acceptance=[{
            "kind": "artifact_exists",
            "path": "reviews/s0_problem.json",
        }],
        work_item_id="s0.problem_definition",
        task_type="independent_review",
        contract_hash=contract,
    )
    workflow.claim(task["id"], "reviewer-worker")
    versioned = root / "reviews" / "s0_problem_v2.json"
    versioned.write_text(
        json.dumps(_review(
            task["id"],
            contract,
            "APPROVE",
            current_hash,
            sha256(root / "docs" / "success_criteria.md"),
        )),
        encoding="utf-8",
    )

    AdaptiveScheduler(root).next_packet()
    migrated = WorkflowEngine(root).get_task(task["id"])

    assert migrated["owns"] == ["reviews/s0_problem_v2.json"]
    assert migrated["acceptance"] == [{
        "kind": "artifact_exists",
        "path": "reviews/s0_problem_v2.json",
    }]
