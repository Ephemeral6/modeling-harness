"""4.7 交付 Profile 强制交付物：名单权威化 + 解开与问题图输出的互锁。

死锁 A 的两条根因各配一组测试：

1. ``profile_mandatory_outputs`` 挂在生产者 task 的验收上，却要求交付物已
   ``verified``；而这些交付物是问题图声明输出，``verify`` 又要求 producer
   task 已 ``completed`` —— builder 完不成 ⟺ 证据验不了。
2. ``mandatory_outputs`` 写语义名、overlay 声明 evidence id，验收只能靠“取
   id 最后一段”猜；猜中的进死锁，猜不中的逼 agent 凭空注册证据。

第 4 组是元测试：以后新增 profile 也不会重蹈覆辙。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelharness.checks_core import evaluate_acceptance
from modelharness.contracts import STAGES
from modelharness.evidence import EvidenceGraph
from modelharness.problem_graph import ProblemGraph
from modelharness.profiles import ProfileService
from modelharness.profiles_overlay_core import (
    MANDATORY_OUTPUT_TARGETS,
    PROFILE_OUTPUTS,
    profile_output_ids,
    resolve_mandatory_output,
    resolve_mandatory_outputs,
)
from modelharness.scaffold import create
from modelharness.stages import StageService
from modelharness.storage import read_json


ACCEPTANCE = [{"kind": "profile_mandatory_outputs"}]

TEMPLATE_PROFILES = sorted(
    (Path(__file__).resolve().parents[1] / "templates" / "config" / "profiles")
    .glob("*.json")
)


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _declared_output_ids(root: Path) -> set[str]:
    return {
        output["evidence_id"]
        for node in ProblemGraph(root).nodes.values()
        if not node.get("superseded", False)
        for output in node["outputs"]
    }


def _profile(root: Path, name: str, outputs: list[str]) -> None:
    _write(root / "config" / "delivery_profile.json", {
        "name": name,
        "version": "1",
        "renderer": "test",
        "mandatory_outputs": outputs,
        "quality_dimensions": [],
    })


# --- 1. 互锁的下游端：交付物存在即可通过生产者验收 --------------------------

def test_candidate_deliverable_satisfies_mandatory_outputs(tmp_path):
    root = create(tmp_path / "project", "candidate deliverable")
    _profile(root, "cumcm", ["result.subquestions"])
    _write(root / "results" / "subquestion_results.json", {"q1": 1})
    assert evaluate_acceptance(root, ACCEPTANCE)["ok"] is False

    evidence = EvidenceGraph(root)
    evidence.add(
        "result.subquestions",
        "result",
        "按题目子问组织的机器可读数值结果",
        "results/subquestion_results.json",
    )
    assert evidence.nodes["result.subquestions"]["status"] == "candidate"

    record = evaluate_acceptance(root, ACCEPTANCE)["records"][0]
    assert record["ok"] is True
    assert record["missing"] == []
    assert record["satisfied_by"] == {
        "result.subquestions": ["result.subquestions"]
    }


@pytest.mark.parametrize("status", ["revoked", "rejected", "invalidated"])
def test_unusable_deliverable_still_fails_mandatory_outputs(tmp_path, status):
    root = create(tmp_path / status, "unusable deliverable")
    _profile(root, "cumcm", ["result.subquestions"])
    _write(root / "results" / "subquestion_results.json", {"q1": 1})
    evidence = EvidenceGraph(root)
    evidence.add(
        "result.subquestions",
        "result",
        "按题目子问组织的机器可读数值结果",
        "results/subquestion_results.json",
    )
    graph = read_json(root / ".harness" / "evidence.json")
    graph["nodes"]["result.subquestions"]["status"] = status
    _write(root / ".harness" / "evidence.json", graph)

    record = evaluate_acceptance(root, ACCEPTANCE)["records"][0]
    assert record["ok"] is False
    assert record["missing"] == ["result.subquestions"]


def test_stale_deliverable_still_fails_mandatory_outputs(tmp_path):
    """产物不在位 ⇒ freshness=missing ⇒ 不算交付。"""
    root = create(tmp_path / "stale", "stale deliverable")
    _profile(root, "cumcm", ["result.subquestions"])
    evidence = EvidenceGraph(root)
    evidence.add(
        "result.subquestions",
        "result",
        "按题目子问组织的机器可读数值结果",
        "results/subquestion_results.json",
    )
    assert evidence.nodes["result.subquestions"]["freshness"] == "missing"

    record = evaluate_acceptance(root, ACCEPTANCE)["records"][0]
    assert record["ok"] is False
    assert record["missing"] == ["result.subquestions"]


def test_gate_refuses_candidate_evidence_that_task_acceptance_accepts(tmp_path):
    """同一份 candidate 证据：生产者验收放行，里程碑 gate 仍然拒签。"""
    root = create(tmp_path / "boundary", "gate boundary")
    ProfileService(root).use("general")
    _profile(root, "general", ["problem"])
    EvidenceGraph(root).add(
        "problem.statement", "problem", "经附件核对的完整题意", "problem/statement.md"
    )
    assert evaluate_acceptance(root, ACCEPTANCE)["records"][0]["ok"] is True

    stages = StageService(root)
    assert stages.current() == "s0"
    with pytest.raises(RuntimeError, match="缺少 verified 证据: problem.statement"):
        stages.gate("s0")


def test_gate_still_requires_verified_for_every_mandatory_output(tmp_path):
    """放宽只发生在生产者验收；里程碑边界的 verified 要求原样保留。"""
    root = create(tmp_path / "gate", "gate coverage")
    ProfileService(root).use("cumcm")
    stages = StageService(root)
    gated = {
        evidence_id
        for stage in STAGES
        for evidence_id, _contract, _enforce in stages._required_evidence(stage)
    }
    profile = read_json(root / "config" / "delivery_profile.json")
    for output in profile["mandatory_outputs"]:
        targets = resolve_mandatory_output(profile["name"], output)
        assert set(targets) <= gated, (output, targets)


# --- 2. 别名一致性：名单不再靠取 id 最后一段猜 ------------------------------

def test_shipped_cumcm_and_mcm_icm_lists_are_literal_evidence_ids():
    for name, expected in (
        ("cumcm", {
            "result.subquestions",
            "narrative.algorithm_process",
            "code.solver",
            "result.uq",
        }),
        ("mcm_icm", {
            "narrative.summary",
            "model.spec",
            "result.robustness",
            "decision.answer",
        }),
    ):
        path = next(
            item for item in TEMPLATE_PROFILES
            if json.loads(item.read_text(encoding="utf-8"))["name"] == name
        )
        profile = json.loads(path.read_text(encoding="utf-8"))
        assert set(profile["mandatory_outputs"]) == expected
        # 出厂名单直接就是 evidence id，解析即恒等。
        assert resolve_mandatory_outputs(name, profile["mandatory_outputs"]) == {
            output: [output] for output in profile["mandatory_outputs"]
        }


def test_legacy_semantic_names_resolve_to_declared_evidence_ids(tmp_path):
    """4.6 及更早落盘的项目名单必须继续解析到真实 evidence id。"""
    for name, table in MANDATORY_OUTPUT_TARGETS.items():
        root = create(tmp_path / f"legacy-{name}", "legacy names")
        ProfileService(root).use(name)
        declared = _declared_output_ids(root)
        for output, targets in table.items():
            assert targets, (name, output)
            assert set(targets) <= declared, (name, output, targets)
            assert resolve_mandatory_output(name, output) == list(targets)


def test_legacy_cumcm_project_no_longer_needs_invented_evidence(tmp_path):
    """演练里 agent 被迫凭空注册的三个 id，现在由声明输出直接满足。"""
    root = create(tmp_path / "legacy", "legacy cumcm")
    ProfileService(root).use("cumcm")
    _profile(root, "cumcm", [
        "subquestion_results",
        "algorithm_process",
        "reproducible_code",
        "error_analysis",
    ])
    evidence = EvidenceGraph(root)
    for evidence_id, kind, artifact in (
        ("code.solver", "code", "src/solver.py"),
        ("result.uq", "result", "results/uq.json"),
        ("result.subquestions", "result", "results/subquestion_results.json"),
        (
            "narrative.algorithm_process",
            "narrative",
            "results/algorithm_process.json",
        ),
    ):
        _write(root / artifact, {"stub": evidence_id})
        evidence.add(evidence_id, kind, evidence_id, artifact)

    record = evaluate_acceptance(root, ACCEPTANCE)["records"][0]
    assert record["ok"] is True
    assert record["satisfied_by"] == {
        "subquestion_results": ["result.subquestions"],
        "algorithm_process": ["narrative.algorithm_process"],
        "reproducible_code": ["code.solver"],
        "error_analysis": ["result.uq"],
    }


def test_unknown_profile_falls_back_to_legacy_alias_matching(tmp_path):
    """未登记的自定义 profile 保持 4.6 的宽松别名行为，不制造新的硬失败。"""
    root = create(tmp_path / "custom", "custom profile")
    _profile(root, "house-style", ["result"])
    _write(root / "results" / "answer.json", {"answer": 1})
    EvidenceGraph(root).add(
        "answer.result", "result", "answer", "results/answer.json"
    )

    record = evaluate_acceptance(root, ACCEPTANCE)["records"][0]
    assert record["ok"] is True
    assert record["satisfied_by"] == {"result": ["answer.result"]}


# --- 3. 元测试：新增 profile 也不会重蹈覆辙 --------------------------------

@pytest.mark.parametrize(
    "profile_path", TEMPLATE_PROFILES, ids=lambda path: path.stem
)
def test_every_profile_mandatory_output_is_declared_by_its_graph(
    tmp_path, profile_path
):
    """遍历所有出厂 profile：每一项强制交付物都必须由该 profile 生效后的
    问题图（seed + overlay）真实声明，否则 agent 只能凭空注册证据。"""
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    name = profile["name"]
    root = create(tmp_path / name, "profile meta test")
    ProfileService(root).use(name)
    declared = _declared_output_ids(root)

    assert set(profile_output_ids(name)) <= declared, name
    for output in profile["mandatory_outputs"]:
        targets = resolve_mandatory_output(name, output)
        assert targets, f"{name}.{output} 无法解析到任何 evidence id"
        missing = sorted(set(targets) - declared)
        assert not missing, (
            f"{name}.{output} 指向未被问题图声明的证据: {missing}"
        )


@pytest.mark.parametrize("name", sorted(PROFILE_OUTPUTS))
def test_every_profile_has_a_shipped_template(name):
    """PROFILE_OUTPUTS 与 templates/config/profiles 必须一一对应。"""
    names = {
        json.loads(path.read_text(encoding="utf-8"))["name"]
        for path in TEMPLATE_PROFILES
    }
    assert name in names
    assert set(MANDATORY_OUTPUT_TARGETS) == names
