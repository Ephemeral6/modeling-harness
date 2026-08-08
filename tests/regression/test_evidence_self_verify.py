"""硬不变量 5：登记证据的 worker 不得把自己的产出翻成 verified。"""
import inspect

import pytest

pytestmark = pytest.mark.regression

DEFECT = (
    "evidence verify 允许生成者自审：登记证据的 agent 用同一个 worker "
    "一条命令就能把自己的产出翻成 verified，硬不变量 5 只靠自觉"
)


def _producer(workflow_class, root):
    workflow = workflow_class(root)
    task = workflow.ensure_task(
        "produce-nominal",
        "s3",
        "solver",
        "计算名义结果",
        ["results/nominal.json"],
        acceptance=[{
            "kind": "artifact_exists", "path": "results/nominal.json",
        }],
        work_item_id="s3.nominal",
        task_type="computation",
    )
    workflow.claim(task["id"], "solver-agent")
    workflow.finish(task["id"], "solver-agent", True, {})
    return task


def test_producer_worker_cannot_verify_its_own_evidence(
    regression_project, regression_api
):
    graph_class = regression_api(
        "modelharness.evidence", "EvidenceGraph", DEFECT
    )
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    assert "worker" in inspect.signature(graph_class.verify).parameters, DEFECT

    root = regression_project("evidence_self_verify")
    task = _producer(workflow_class, root)
    graph = graph_class(root)
    graph.add(
        "result.nominal",
        "result",
        "名义结果",
        "results/nominal.json",
        producer_task_id=task["id"],
    )

    with pytest.raises(ValueError, match="生成者不得自验"):
        graph.verify("result.nominal", worker="solver-agent")
    with pytest.raises(ValueError, match="生成者不得自验"):
        # 大小写与空白不是逃逸口。
        graph.verify("result.nominal", worker="  Solver-Agent ")
    with pytest.raises(ValueError, match="生成者不得自验"):
        graph.verify("result.nominal", verifier_task_id=task["id"])
    assert graph.nodes["result.nominal"]["status"] == "candidate", DEFECT

    verified = graph.verify("result.nominal", worker="checker-agent")
    binding = verified["verification"]["binding"]
    assert verified["status"] == "verified", DEFECT
    assert binding["isolation"] == "isolated", DEFECT
    assert binding["verifier_worker"] == "checker-agent", DEFECT
    assert binding["producer_worker"] == "solver-agent", DEFECT
    assert graph.audit() == [], DEFECT


def test_unregistered_producer_task_is_not_an_escape_hatch(
    regression_project, regression_api
):
    graph_class = regression_api(
        "modelharness.evidence", "EvidenceGraph", DEFECT
    )
    workflow_class = regression_api(
        "modelharness.workflow", "WorkflowEngine", DEFECT
    )
    root = regression_project("evidence_self_verify")
    _producer(workflow_class, root)
    graph = graph_class(root)
    # 故意不传 producer_task_id：占有该工件的任务 worker 仍然算生成者。
    graph.add("result.nominal", "result", "名义结果", "results/nominal.json")

    with pytest.raises(ValueError, match="生成者不得自验"):
        graph.verify("result.nominal", worker="solver-agent")
    assert graph.verify(
        "result.nominal", worker="checker-agent"
    )["status"] == "verified", DEFECT
