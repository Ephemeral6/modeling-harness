"""硬不变量 5 的两处缺口：阶段 gate 与交付终审都可以自签自批。

端到端演练实测原话：「`config/stages.json` 里的阶段级 review 完全没有身份
绑定——任何 `verdict=APPROVE` 的 JSON 都能过 gate；`require_terminal_approval`
也只看哈希与 verdict，不看 reviewer。硬不变量 5 只在 evidence 层落地，在阶段
gate 与交付终审这两处是空的。」即交付终审这道门可以自签自批。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression

DEFECT = (
    "阶段 gate 与交付终审只校验 verdict/artifact_hashes，不校验 reviewer 身份："
    "产出工件的 worker 自己写一份 verdict=APPROVE 的 JSON 就能过阶段 gate、"
    "渲染成稿并冻结交付"
)


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _completed(
    workflow,
    key: str,
    worker: str,
    owns: list[str],
    task_type: str,
    before_finish=None,
) -> dict:
    """建一个已完成的任务；owns 的工件在 finish 前必须存在（任务验收）。"""
    task = workflow.ensure_task(
        key,
        "s0" if task_type != "evidence_delivery" else "s6",
        "builder" if task_type != "independent_review" else "reviewer",
        key,
        owns,
        acceptance=[
            {"kind": "artifact_exists", "path": item} for item in owns
        ],
        task_type=task_type,
    )
    workflow.claim(task["id"], worker)
    if before_finish is not None:
        before_finish(task)
    workflow.finish(task["id"], worker, True, {})
    return task


# ------------------------------------------------------------ 阶段 gate 层


def test_stage_gate_refuses_a_self_signed_approve(
    regression_project, regression_api
):
    stage_class = regression_api(
        "modelharness.stages", "StageService", DEFECT
    )
    graph_class = regression_api(
        "modelharness.evidence", "EvidenceGraph", DEFECT
    )
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    root = regression_project("terminal_review_self_approved")
    workflow = workflow_class(root)
    _completed(
        workflow, "produce-nominal", "builder-01",
        ["results/nominal.json"], "computation",
    )
    _completed(
        workflow, "referee-s0", "reviewer-01",
        ["reviews/s0_referee.json"], "independent_review",
    )
    graph = graph_class(root)
    graph.add("result.nominal", "result", "名义结果", "results/nominal.json")
    graph.verify("result.nominal", worker="reviewer-01")

    # 夹具里的 reviews/s0_referee.json 由 builder-01 自签 APPROVE。
    with pytest.raises(RuntimeError, match="不得自批"):
        stage_class(root).gate("s0")
    assert not (root / ".harness" / "stamps" / "s0.json").exists(), DEFECT

    # 换成真正的独立审核者，同一份 APPROVE 立刻放行。
    _write(root / "reviews" / "s0_referee_v2.json", {
        "schema": 1,
        "role": "referee",
        "reviewer": "reviewer-01",
        "verdict": "APPROVE",
        "findings": [],
        "required_fixes": [],
        "evidence_checked": ["result.nominal"],
    })
    record = stage_class(root).gate("s0")
    assert record["stage"] == "s0", DEFECT
    assert record["reviews"][0]["path"] == "reviews/s0_referee_v2.json"


def test_stage_gate_refuses_an_unsigned_approve(
    regression_project, regression_api
):
    """cumcm profile 下匿名 APPROVE 同样不算独立审核。"""
    stage_class = regression_api(
        "modelharness.stages", "StageService", DEFECT
    )
    graph_class = regression_api(
        "modelharness.evidence", "EvidenceGraph", DEFECT
    )
    root = regression_project("terminal_review_self_approved")
    graph = graph_class(root)
    graph.add("result.nominal", "result", "名义结果", "results/nominal.json")
    graph.verify("result.nominal", worker="reviewer-01")
    _write(root / "reviews" / "s0_referee_v2.json", {
        "schema": 1,
        "role": "referee",
        "verdict": "APPROVE",
        "findings": [],
    })

    with pytest.raises(RuntimeError, match="未署名 reviewer"):
        stage_class(root).gate("s0")
    assert not (root / ".harness" / "stamps" / "s0.json").exists(), DEFECT


# -------------------------------------------------------------- 交付终审层


def test_terminal_approval_refuses_the_producer_as_reviewer(
    regression_project, regression_api
):
    require = regression_api(
        "modelharness.delivery_core", "require_terminal_approval", DEFECT
    )
    render = regression_api("modelharness.sanitize", "render_final", DEFECT)
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    root = regression_project("terminal_review_self_approved")
    workflow = workflow_class(root)
    producer = _completed(
        workflow, "deliver-paper", "builder-01",
        ["paper/draft.md"], "evidence_delivery",
    )
    draft_hash = _sha256(root / "paper" / "draft.md")

    # 自签终审：reviewer 就是工作稿的生成者，task_id 指向它自己的交付任务。
    _write(root / "reviews" / "s6_paper_audit_v2.json", {
        "schema": 1,
        "role": "paper-verifier",
        "reviewer": "builder-01",
        "task_id": producer["id"],
        "verdict": "APPROVE",
        "findings": ["我自己看了一遍，没问题。"],
        "required_fixes": [],
        "artifact_hashes": {"paper/draft.md": draft_hash},
    })
    with pytest.raises(ValueError, match="不得自批"):
        require(root)
    with pytest.raises(ValueError, match="交付门禁"):
        render(root)
    assert not (root / "paper" / "final.md").exists(), DEFECT

    # 换一个真实的 independent_review 任务出具的终审：同一份稿子放行。
    def _sign(task: dict) -> None:
        _write(root / "reviews" / "s6_paper_audit_v3.json", {
            "schema": 1,
            "role": "paper-verifier",
            "reviewer": "reviewer-02",
            "task_id": task["id"],
            "verdict": "APPROVE",
            "findings": [],
            "required_fixes": [],
            "artifact_hashes": {"paper/draft.md": draft_hash},
        })

    _completed(
        workflow, "audit-paper", "reviewer-02",
        ["reviews/s6_paper_audit_v3.json"], "independent_review",
        before_finish=_sign,
    )
    resolved = require(root)
    assert resolved["path"] == "reviews/s6_paper_audit_v3.json", DEFECT
    final = render(root)
    assert final.is_file(), DEFECT
    assert "[[result.nominal]]" not in final.read_text(encoding="utf-8")


def test_terminal_approval_refuses_an_unbound_review_task(
    regression_project, regression_api
):
    """竞赛 profile + 已启用独立审核任务时，终审必须绑定真实审核任务。"""
    require = regression_api(
        "modelharness.delivery_core", "require_terminal_approval", DEFECT
    )
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    root = regression_project("terminal_review_self_approved")
    workflow = workflow_class(root)
    _completed(
        workflow, "referee-s0", "reviewer-01",
        ["reviews/s0_referee.json"], "independent_review",
    )
    draft_hash = _sha256(root / "paper" / "draft.md")
    _write(root / "reviews" / "s6_paper_audit_v2.json", {
        "schema": 1,
        "role": "paper-verifier",
        "reviewer": "cold-audit-99",
        "verdict": "APPROVE",
        "findings": [],
        "artifact_hashes": {"paper/draft.md": draft_hash},
    })

    with pytest.raises(ValueError, match="未绑定 independent_review 任务"):
        require(root)

    # 不存在的 task_id 同样不是逃逸口。
    _write(root / "reviews" / "s6_paper_audit_v3.json", {
        "schema": 1,
        "role": "paper-verifier",
        "reviewer": "cold-audit-99",
        "task_id": "0000000000000000",
        "verdict": "APPROVE",
        "findings": [],
        "artifact_hashes": {"paper/draft.md": draft_hash},
    })
    with pytest.raises(ValueError, match="审核任务不存在"):
        require(root)


# ------------------------------------------------------------------ 兼容路径


def test_pre_identity_projects_without_workflow_still_deliver(
    regression_project, regression_api
):
    """未启用工作流的历史项目：署名 APPROVE 照旧放行，印章不塌陷。"""
    require = regression_api(
        "modelharness.delivery_core", "require_terminal_approval", DEFECT
    )
    root = regression_project("terminal_review_self_approved")
    assert not (root / ".harness" / "workflow.sqlite3").exists()
    _write(root / "reviews" / "s6_paper_audit_v2.json", {
        "schema": 1,
        "role": "paper-verifier",
        "reviewer": "cold-audit-01",
        "verdict": "APPROVE",
        "findings": [],
        "artifact_hashes": {
            "paper/draft.md": _sha256(root / "paper" / "draft.md"),
        },
    })

    resolved = require(root)

    assert resolved["record"]["reviewer"] == "cold-audit-01"
    # 校验没有顺手把工作流库建出来。
    assert not (root / ".harness" / "workflow.sqlite3").exists(), (
        "身份校验不得为了查生成者而凭空创建工作流库"
    )
