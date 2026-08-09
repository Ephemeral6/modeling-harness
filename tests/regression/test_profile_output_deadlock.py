"""死锁 A：profile 强制交付物与问题图输出互锁，任何 cumcm 项目都过不了 S6。

互锁两端（4.6 端到端演练实测原文）：

* ``任务验收失败: {'kind':'profile_mandatory_outputs',
  'missing':['algorithm_process']}``
* ``producer task is not completed: 76364900c3494830``

builder task 的验收要求强制交付物已 ``verified``；而这些交付物是问题图声明
输出，带 ``obligation_hash``，``evidence verify`` 又要求 producer task 已
``completed``。两边互为前置，没有合法出路。

fixture 是 4.6 出厂 cumcm 配置的最小闭包：``config/delivery_profile.json``
用语义名（``subquestion_results`` 等），overlay 在 ``s6.delivery`` 上声明的
却是 evidence id（``result.subquestions`` 等）；上游 s3/s4 的 ``code.solver``
与 ``result.uq`` 已 verified，代表里程碑已 gate 过。
"""
import pytest

pytestmark = pytest.mark.regression

DEFECT = (
    "profile_mandatory_outputs 在 builder task 验收里要求证据已 verified，"
    "而问题图声明输出的 verify 又要求 producer task 已 completed："
    "S6 builder 完不成 ⟺ 证据验不了，cumcm 项目无合法出路"
)

ALIAS_DEFECT = (
    "profile.mandatory_outputs 与 overlay 声明的 evidence_id 对不上，"
    "agent 必须凭空注册 harness 从不索要的证据 id"
)

OWNS = [
    "results/subquestion_results.json",
    "results/algorithm_process.json",
]

DELIVERABLES = ("result.subquestions", "narrative.algorithm_process")


def _delivery_task(workflow_class, root, acceptance):
    from modelharness.problem_graph import ProblemGraph

    workflow = workflow_class(root)
    task = workflow.ensure_task(
        "s6-delivery",
        "s6",
        "delivery-team",
        "从 verified evidence 生成交付物",
        OWNS,
        acceptance=acceptance,
        work_item_id="s6.delivery",
        task_type="evidence_delivery",
        contract_hash=ProblemGraph(root).contract_hash("s6.delivery"),
    )
    workflow.claim(task["id"], "builder-01")
    return workflow, task


def _register_declared_outputs(graph_class, root, task_id):
    from modelharness.problem_graph import ProblemGraph

    graph = graph_class(root)
    for output in ProblemGraph(root).nodes["s6.delivery"]["outputs"]:
        graph.add(
            output["evidence_id"],
            output["kind"],
            output["statement"],
            output["artifact"],
            producer_task_id=task_id,
        )
    return graph


def test_declared_output_cannot_be_verified_before_producer_finishes(
    regression_project, regression_api
):
    """互锁的上游端：这一侧是硬不变量，修复后必须原样保留。"""
    graph_class = regression_api(
        "modelharness.evidence", "EvidenceGraph", DEFECT
    )
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    root = regression_project("profile_output_deadlock")
    _workflow, task = _delivery_task(
        workflow_class, root, [{"kind": "profile_mandatory_outputs"}]
    )
    graph = _register_declared_outputs(graph_class, root, task["id"])

    assert graph.nodes["narrative.algorithm_process"]["obligation_hash"]
    with pytest.raises(RuntimeError, match="证据验证失败"):
        graph.verify("narrative.algorithm_process", worker="reviewer-01")
    verification = graph.nodes["narrative.algorithm_process"]["verification"]
    assert any(
        "producer task is not completed" in error
        for error in verification["review_errors"]
    ), DEFECT


def test_delivery_builder_can_finish_after_producing_declared_outputs(
    regression_project, regression_api
):
    """互锁的下游端：4.6 在这里抛 profile_mandatory_outputs 验收失败。"""
    graph_class = regression_api(
        "modelharness.evidence", "EvidenceGraph", DEFECT
    )
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    root = regression_project("profile_output_deadlock")
    workflow, task = _delivery_task(
        workflow_class, root, [{"kind": "profile_mandatory_outputs"}]
    )
    graph = _register_declared_outputs(graph_class, root, task["id"])
    assert all(
        graph.nodes[node_id]["status"] == "candidate"
        for node_id in DELIVERABLES
    ), DEFECT

    finished = workflow.finish(task["id"], "builder-01", True, {})
    assert finished["status"] == "completed", DEFECT
    record = next(
        item for item in finished["result"]["acceptance"]["records"]
        if item.get("kind") == "profile_mandatory_outputs"
    )
    assert record["missing"] == [], DEFECT
    assert record["satisfied_by"]["algorithm_process"] == [
        "narrative.algorithm_process"
    ], ALIAS_DEFECT
    assert record["satisfied_by"]["subquestion_results"] == [
        "result.subquestions"
    ], ALIAS_DEFECT

    # producer 完成之后，独立 worker 才能把交付物翻成 verified：互锁解开。
    for node_id in DELIVERABLES:
        assert graph.verify(
            node_id, worker="reviewer-01"
        )["status"] == "verified", DEFECT
    assert graph.audit() == [], DEFECT


def test_mandatory_outputs_resolve_to_declared_evidence_ids(
    regression_project, regression_api
):
    """名单必须显式指向真实 evidence id，不许再靠取 id 最后一段猜。"""
    resolve = regression_api(
        "modelharness.profiles_overlay_core",
        "resolve_mandatory_outputs",
        ALIAS_DEFECT,
    )
    from modelharness.storage import read_json

    root = regression_project("profile_output_deadlock")
    profile = read_json(root / "config" / "delivery_profile.json")
    resolved = resolve(profile["name"], profile["mandatory_outputs"])

    assert resolved == {
        "subquestion_results": ["result.subquestions"],
        "algorithm_process": ["narrative.algorithm_process"],
        "reproducible_code": ["code.solver"],
        "error_analysis": ["result.uq"],
    }, ALIAS_DEFECT


def test_missing_deliverable_still_fails_acceptance(
    regression_project, regression_api
):
    """放宽 verified 不等于放弃检查：交付物没产出仍然必须挂。"""
    graph_class = regression_api(
        "modelharness.evidence", "EvidenceGraph", DEFECT
    )
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    root = regression_project("profile_output_deadlock")
    (root / "results" / "algorithm_process.json").unlink()
    workflow, task = _delivery_task(
        workflow_class, root, [{"kind": "profile_mandatory_outputs"}]
    )
    graph = graph_class(root)
    graph.add(
        "result.subquestions",
        "result",
        "按题目子问组织的机器可读数值结果",
        "results/subquestion_results.json",
        producer_task_id=task["id"],
    )

    with pytest.raises(RuntimeError, match="任务验收失败") as failure:
        workflow.finish(task["id"], "builder-01", True, {})
    assert "algorithm_process" in str(failure.value), DEFECT
    assert workflow.get_task(task["id"])["status"] == "failed", DEFECT
