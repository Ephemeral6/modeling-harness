from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

from modelharness.evidence import EvidenceGraph
from modelharness.problem_graph import ProblemGraph
from modelharness.scaffold import create
from modelharness.stages import StageService
from modelharness.toolchain import ToolchainService
from modelharness.toolchain_registry import ToolRegistry
from modelharness.workflow import WorkflowEngine


def test_tool_registry_is_locked_and_profile_doctor_is_structured(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "tools")
    registry = ToolRegistry(root)
    assert not registry.audit_catalog()
    assert not registry.environment_drift()
    report = registry.doctor("general")
    assert report["profile"] == "general"
    assert "required_missing" in report
    assert "compatibility_warnings" in report
    assert registry.probe("python")["available"]


def test_agent_may_skip_tool_use_with_auditable_reason(tmp_path: Path):
    root = create(tmp_path / "case", "skip")
    service = ToolchainService(root)
    node_id = "s1.model_formulation"
    decision = service.decide(
        node_id,
        "skip",
        "该局部步骤先用纸面反例筛选结构，计算留到下游节点",
    )
    assert decision["action"] == "skip"
    assert not service.audit_decision(node_id)

    contract = ProblemGraph(root).contract_hash(node_id)
    engine = WorkflowEngine(root)
    task = engine.ensure_task(
        "tool-skip-acceptance",
        "s1",
        "modeler",
        "record autonomous tool choice",
        ["docs/tool-choice.md"],
        acceptance=[{
            "kind": "tool_decision",
            "node_id": node_id,
            "contract_hash": contract,
        }],
        work_item_id=node_id,
        contract_hash=contract,
    )
    engine.claim(task["id"], "worker")
    finished = engine.finish(task["id"], "worker", True, {})
    assert finished["status"] == "completed"


def test_selected_tool_requires_verified_reproducible_run(tmp_path: Path):
    root = create(tmp_path / "case", "run")
    script = root / "src" / "tool_smoke.py"
    script.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "Path('results/tool.json').write_text("
        "json.dumps({'answer': 4.0}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    service = ToolchainService(root)
    node_id = "s0.problem_definition"
    service.decide(
        node_id, "use", "用本地 Python 做确定性结构化结果检查",
        tools=["python"],
    )
    record = service.run(
        node_id,
        argv=[sys.executable, "src/tool_smoke.py"],
        inputs=["src/tool_smoke.py"],
        outputs=["results/tool.json"],
        validators=[
            {"kind": "json_finite", "path": "results/tool.json"},
            {
                "kind": "numeric_assertion",
                "path": "results/tool.json",
                "field": "answer",
                "operator": "==",
                "value": 4,
                "tolerance": 0,
            },
        ],
        tool_ids=["python"],
        seed=7,
        timeout=30,
    )
    assert record["verification"]["status"] == "verified"
    assert record["inputs"][0]["sha256"]
    assert record["outputs"][0]["sha256"]
    assert not service.audit_decision(node_id)

    (root / "results" / "tool.json").write_text(
        json.dumps({"answer": 5}), encoding="utf-8"
    )
    rechecked = service.executor.verify(record["id"])
    assert rechecked["verification"]["status"] == "failed"
    assert service.audit_decision(node_id)


def test_nonlocal_tool_risk_cannot_be_self_authorized(tmp_path: Path):
    root = create(tmp_path / "case", "risk")
    registry = ToolRegistry(root)
    if not registry.probe("requests")["available"]:
        pytest.skip("requests is not installed in this environment")
    with pytest.raises(RuntimeError, match="需要新授权"):
        ToolchainService(root).decide(
            "s2.data_estimation",
            "use",
            "尝试访问外部数据",
            tools=["requests"],
        )


def test_gate_requires_decision_when_method_pack_marks_it_required(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "gate")
    graph = ProblemGraph(root)
    proposal = copy.deepcopy(graph.data)
    proposal["nodes"]["s0.problem_definition"]["method_pack"] = "generic"
    graph.replace(proposal, "exercise tool decision gate")
    evidence = EvidenceGraph(root)
    for node_id, artifact in (
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ):
        evidence.add(node_id, "problem", node_id, artifact)
        evidence.verify(node_id)
    with pytest.raises(RuntimeError, match="工具调用/跳过决策"):
        StageService(root).gate("s0")
    ToolchainService(root).decide(
        "s0.problem_definition",
        "skip",
        "该门禁测试不需要额外数值计算",
    )
    assert StageService(root).gate("s0")["toolchain_contract_sha256"]
