"""4.7 机制：阶段 gate 与交付终审的审核者身份绑定（硬不变量 5 的另外两个落点）。

4.6.1 只把「生成者不得自验」落到 evidence verify 上；实测发现真正决定交付的
两道门——`config/stages.json` 的阶段级 review 与 `require_terminal_approval`
——完全不看 reviewer。本文件锁住这两条路径，以及旧格式的兼容口径。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelharness.delivery_core import require_terminal_approval
from modelharness.evidence import EvidenceGraph
from modelharness.profiles import ProfileService
from modelharness.review_store import (
    has_independent_review_task,
    identity_enforced,
    producer_workers,
    review_identity_errors,
)
from modelharness.scaffold import create
from modelharness.stages import StageService
from modelharness.storage import read_json
from modelharness.util import sha256
from modelharness.workflow import WorkflowEngine


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _project(tmp_path: Path, name: str = "case") -> Path:
    return create(tmp_path / name, "demo")


def _producer_task(root: Path, worker: str = "builder-01") -> dict:
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        "produce-statement",
        "s0",
        "problem-architect",
        "写题面",
        ["problem/statement.md"],
        acceptance=[{
            "kind": "artifact_exists", "path": "problem/statement.md",
        }],
        task_type="problem_formulation",
    )
    workflow.claim(task["id"], worker)
    workflow.finish(task["id"], worker, True, {})
    return task


def _review_task(
    root: Path, worker: str, owns: str, finish: bool = True
) -> dict:
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        f"review:{owns}",
        "s0",
        "problem-reviewer",
        "冷启动复核",
        [owns],
        acceptance=[{"kind": "artifact_exists", "path": owns}],
        task_type="independent_review",
    )
    workflow.claim(task["id"], worker)
    if finish:
        _finish_review_task(root, task, worker, owns)
    return task


def _finish_review_task(
    root: Path, task: dict, worker: str, owns: str
) -> dict:
    """任务验收要求 owns 的工件存在，落章前先把审核文件写出来。"""
    path = root / owns
    if not path.is_file():
        _write(path, {"verdict": "APPROVE", "reviewer": worker,
                      "findings": []})
    return WorkflowEngine(root).finish(task["id"], worker, True, {})


# ------------------------------------------------------------------ 共用判定


def test_producer_workers_reuses_the_evidence_layer_resolution(
    tmp_path: Path,
):
    root = _project(tmp_path)
    assert producer_workers(root, ["problem/statement.md"]) == []

    _producer_task(root, "builder-01")
    _review_task(root, "reviewer-01", "reviews/s0_referee.json", finish=False)

    # 占有该工件的非 independent_review 任务算生成者；审核任务不算。
    assert producer_workers(root, ["problem/statement.md"]) == ["builder-01"]
    assert producer_workers(root, ["reviews/s0_referee.json"]) == []
    # 目录级 owns 与子路径互相覆盖，和 evidence 层同一套 owners_overlap。
    assert producer_workers(root, ["problem"]) == ["builder-01"]
    assert producer_workers(root, [""]) == []


def test_has_independent_review_task_tracks_workflow_state(tmp_path: Path):
    root = _project(tmp_path)
    assert has_independent_review_task(root) is False
    _producer_task(root)
    assert has_independent_review_task(root) is False
    _review_task(root, "reviewer-01", "reviews/s0_referee.json", finish=False)
    assert has_independent_review_task(root) is True


def test_identity_enforced_follows_the_delivery_profile(tmp_path: Path):
    root = _project(tmp_path)
    assert identity_enforced(root) is False
    ProfileService(root).use("cumcm")
    assert identity_enforced(root) is True


@pytest.mark.parametrize(
    "reviewer", ["builder-01", "  Builder-01 ", "BUILDER-01"]
)
def test_clash_is_case_and_space_insensitive(tmp_path: Path, reviewer: str):
    root = _project(tmp_path)
    _producer_task(root, "builder-01")

    errors = review_identity_errors(
        root,
        {"verdict": "APPROVE", "reviewer": reviewer},
        "reviews/s0_referee.json",
        ["problem/statement.md"],
        label="s0 阶段独立审核",
        require_reviewer=True,
    )

    assert any("不得自批" in item for item in errors), errors


def test_blank_reviewer_is_rejected_even_outside_competition_profiles(
    tmp_path: Path,
):
    """写了 reviewer 键却是空白，是当代格式写坏了，不属于历史遗留。"""
    root = _project(tmp_path)

    errors = review_identity_errors(
        root,
        {"verdict": "APPROVE", "reviewer": "   "},
        "reviews/s0_referee.json",
        ["problem/statement.md"],
        label="s0 阶段独立审核",
        require_reviewer=False,
    )

    assert any("reviewer 为空" in item for item in errors), errors


def test_missing_reviewer_key_is_grandfathered_only_off_competition(
    tmp_path: Path,
):
    root = _project(tmp_path)
    record = {"verdict": "APPROVE"}

    assert review_identity_errors(
        root, record, "reviews/s0_referee.json", ["problem/statement.md"],
        label="s0 阶段独立审核", require_reviewer=False,
    ) == []
    strict = review_identity_errors(
        root, record, "reviews/s0_referee.json", ["problem/statement.md"],
        label="s0 阶段独立审核", require_reviewer=True,
    )
    assert any("未署名 reviewer" in item for item in strict), strict


def test_declared_review_task_must_be_a_completed_independent_review(
    tmp_path: Path,
):
    root = _project(tmp_path)
    producer = _producer_task(root)
    running = _review_task(
        root, "reviewer-01", "reviews/s0_referee.json", finish=False
    )

    def errors(record: dict) -> list[str]:
        return review_identity_errors(
            root, record, "reviews/s0_referee.json",
            ["problem/statement.md"],
            label="s0 阶段独立审核", require_reviewer=True,
        )

    missing = errors({
        "verdict": "APPROVE", "reviewer": "reviewer-01",
        "task_id": "0000000000000000",
    })
    assert any("审核任务不存在" in item for item in missing), missing

    wrong_type = errors({
        "verdict": "APPROVE", "reviewer": "builder-01",
        "task_id": producer["id"],
    })
    assert any("不是 independent_review" in item for item in wrong_type)

    unfinished = errors({
        "verdict": "APPROVE", "reviewer": "reviewer-01",
        "task_id": running["id"],
    })
    assert any("未 completed" in item for item in unfinished), unfinished

    mismatch = errors({
        "verdict": "APPROVE", "reviewer": "reviewer-99",
        "task_id": running["id"],
    })
    assert any("worker 不一致" in item for item in mismatch), mismatch


def test_reviewer_identity_falls_back_to_the_review_task_worker(
    tmp_path: Path,
):
    """审核文件没署名但绑定了任务时，用任务租约身份判定冲突。"""
    root = _project(tmp_path)
    _producer_task(root, "builder-01")
    task = _review_task(root, "builder-01", "reviews/s0_referee.json")

    errors = review_identity_errors(
        root,
        {"verdict": "APPROVE", "task_id": task["id"]},
        "reviews/s0_referee.json",
        ["problem/statement.md"],
        label="s0 阶段独立审核",
        require_reviewer=False,
    )

    assert any("不得自批: builder-01" in item for item in errors), errors


# -------------------------------------------------------------- 阶段 gate 层


def _stage_ready(tmp_path: Path, review: str = "reviews/s0_referee.json"):
    root = _project(tmp_path)
    config_path = root / "config" / "stages.json"
    config = read_json(config_path)
    config["s0"]["reviews"] = [review]
    _write(config_path, config)
    graph = EvidenceGraph(root)
    for node_id, artifact in (
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ):
        graph.add(node_id, "problem", node_id, artifact)
        graph.verify(node_id, worker="reviewer-01")
    return root


def test_gate_refuses_a_review_signed_by_the_stage_producer(tmp_path: Path):
    root = _stage_ready(tmp_path)
    _producer_task(root, "builder-01")
    _write(root / "reviews" / "s0_referee.json", {
        "verdict": "APPROVE", "reviewer": "builder-01", "findings": [],
    })

    with pytest.raises(RuntimeError, match="不得自批"):
        StageService(root).gate("s0")
    assert not (root / ".harness" / "stamps" / "s0.json").exists()

    _write(root / "reviews" / "s0_referee_v2.json", {
        "verdict": "APPROVE", "reviewer": "reviewer-01", "findings": [],
    })
    record = StageService(root).gate("s0")
    assert record["stage"] == "s0"
    assert record["reviews"][0]["path"] == "reviews/s0_referee_v2.json"


def test_gate_grandfathers_unsigned_reviews_off_competition_profiles(
    tmp_path: Path,
):
    root = _stage_ready(tmp_path)
    _producer_task(root, "builder-01")
    _write(root / "reviews" / "s0_referee.json", {
        "verdict": "APPROVE", "findings": [],
    })

    # general profile：旧格式（无 reviewer 键）照旧放行。
    assert StageService(root).gate("s0")["stage"] == "s0"


def test_gate_requires_a_signature_under_competition_profiles(
    tmp_path: Path,
):
    root = _stage_ready(tmp_path)
    ProfileService(root).use("cumcm")
    _write(root / "reviews" / "s0_referee.json", {
        "verdict": "APPROVE", "findings": [],
    })

    with pytest.raises(RuntimeError, match="未署名 reviewer"):
        StageService(root).gate("s0")


def test_existing_stamps_do_not_collapse_when_identity_lands(
    tmp_path: Path,
):
    """已签发的印章不因为新增身份校验而失效——历史印章不塌陷。"""
    root = _stage_ready(tmp_path)
    _write(root / "reviews" / "s0_referee.json", {
        "verdict": "APPROVE", "findings": [],
    })
    StageService(root).gate("s0")
    # 落章之后才出现的生成者任务，不追溯推翻已有印章。
    _producer_task(root, "builder-01")

    assert StageService(root).validate_stamp("s0") == []
    assert "s0" in StageService(root).valid_prefix()


# -------------------------------------------------------------- 交付终审层


def _terminal_ready(tmp_path: Path, profile: str = "general") -> Path:
    root = _project(tmp_path)
    if profile != "general":
        ProfileService(root).use(profile)
    _write(
        root / "paper" / "draft.md",
        "# 决策\n\n最优配比为 A 粉 0.42。[[claim.q1]]\n",
    )
    return root


def _terminal(root: Path, name: str, record: dict) -> None:
    record.setdefault("schema", 1)
    record.setdefault("role", "paper-verifier")
    record.setdefault("findings", [])
    record.setdefault("artifact_hashes", {
        "paper/draft.md": sha256(root / "paper" / "draft.md"),
    })
    _write(root / "reviews" / name, record)


def test_terminal_approval_refuses_the_draft_producer(tmp_path: Path):
    root = _terminal_ready(tmp_path)
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        "deliver-paper",
        "s6",
        "delivery",
        "写稿",
        ["paper/draft.md"],
        acceptance=[{"kind": "artifact_exists", "path": "paper/draft.md"}],
        task_type="evidence_delivery",
    )
    workflow.claim(task["id"], "builder-01")
    workflow.finish(task["id"], "builder-01", True, {})
    _terminal(root, "s6_paper_audit.json", {
        "reviewer": "builder-01", "verdict": "APPROVE",
    })

    with pytest.raises(ValueError, match="不得自批"):
        require_terminal_approval(root)

    _terminal(root, "s6_paper_audit_v2.json", {
        "reviewer": "cold-audit-01", "verdict": "APPROVE",
    })
    resolved = require_terminal_approval(root)
    assert resolved["path"] == "reviews/s6_paper_audit_v2.json"


def test_terminal_approval_covers_every_artifact_the_review_claims(
    tmp_path: Path,
):
    """reviewer 只要生产过 artifact_hashes 里的任何一件，就是自批。"""
    root = _terminal_ready(tmp_path)
    _write(root / "results" / "nominal.json", {"objective": 18.7})
    workflow = WorkflowEngine(root)
    task = workflow.ensure_task(
        "solve",
        "s3",
        "solver",
        "求解",
        ["results/nominal.json"],
        acceptance=[{
            "kind": "artifact_exists", "path": "results/nominal.json",
        }],
        task_type="computation",
    )
    workflow.claim(task["id"], "solver-agent")
    workflow.finish(task["id"], "solver-agent", True, {})
    _terminal(root, "s6_paper_audit.json", {
        "reviewer": "solver-agent",
        "verdict": "APPROVE",
        "artifact_hashes": {
            "paper/draft.md": sha256(root / "paper" / "draft.md"),
            "results/nominal.json": sha256(root / "results" / "nominal.json"),
        },
    })

    with pytest.raises(ValueError, match="不得自批: solver-agent"):
        require_terminal_approval(root)


def test_terminal_approval_requires_a_bound_task_under_competition(
    tmp_path: Path,
):
    root = _terminal_ready(tmp_path, "cumcm")
    _terminal(root, "s6_paper_audit.json", {
        "reviewer": "cold-audit-01", "verdict": "APPROVE",
    })
    # 项目还没登记任何 independent_review 任务：不强制绑定，兼容旧项目。
    assert require_terminal_approval(root)["record"]["reviewer"] == (
        "cold-audit-01"
    )

    reviewer = _review_task(
        root, "reviewer-01", "reviews/s0_referee.json", finish=False
    )
    with pytest.raises(ValueError, match="未绑定 independent_review 任务"):
        require_terminal_approval(root)

    _finish_review_task(
        root, reviewer, "reviewer-01", "reviews/s0_referee.json"
    )
    _terminal(root, "s6_paper_audit_v2.json", {
        "reviewer": "reviewer-01",
        "task_id": reviewer["id"],
        "verdict": "APPROVE",
    })
    assert require_terminal_approval(root)["path"] == (
        "reviews/s6_paper_audit_v2.json"
    )


def test_terminal_approval_keeps_legacy_projects_deliverable(
    tmp_path: Path,
):
    """无工作流的历史项目：署名 APPROVE 照旧放行，且不凭空建工作流库。"""
    root = _terminal_ready(tmp_path)
    _terminal(root, "s6_paper_audit.json", {
        "reviewer": "cold-audit-01", "verdict": "APPROVE",
    })

    assert require_terminal_approval(root)["version"] == 1
    assert not (root / ".harness" / "workflow.sqlite3").exists()


def test_terminal_approval_grandfathers_unsigned_reviews_off_competition(
    tmp_path: Path,
):
    root = _terminal_ready(tmp_path)
    _terminal(root, "s6_paper_audit.json", {"verdict": "APPROVE"})

    assert require_terminal_approval(root)["version"] == 1

    ProfileService(root).use("cumcm")
    with pytest.raises(ValueError, match="未署名 reviewer"):
        require_terminal_approval(root)
