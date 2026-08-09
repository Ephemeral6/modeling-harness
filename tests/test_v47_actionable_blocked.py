"""4.7 可操作性：阻断信息必须自带真实原因和下一步动作。

端到端演练实测两处：

* 一枚 verified 证据变 tampered，就让 ``validate_stamp`` 的全局证据审计失败，
  七枚印章同时失效、``valid_prefix`` 塌成 ``[]``、调度阶段被回退，前沿变空 →
  ``phase=blocked``，而 instruction 只说「当前无可执行前沿」，agent 因此误判
  「任务永远无法创建」。
* ``evidence verify`` 失败时 CLI 只吐一句「证据验证失败: <id>」，真正的
  ``review_errors`` 只写进 ``.harness/evidence.json``，agent 只能自己去读 JSON。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modelharness import cli
from modelharness.evidence import EvidenceGraph
from modelharness.problem_graph import ProblemGraph
from modelharness.scaffold import create
from modelharness.scheduler import AdaptiveScheduler
from modelharness.stages import StageService


OLD_BLOCKED_INSTRUCTION = "当前无可执行前沿；检查缺失输入、冲突任务或问题图依赖。"


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _stamped_s0(tmp_path: Path) -> Path:
    root = create(tmp_path / "case", "demo")
    contract = ProblemGraph(root).contract_hash("s0.problem_definition")
    evidence = EvidenceGraph(root)
    for node_id, artifact in (
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ):
        evidence.add(
            node_id, "problem", node_id, artifact, obligation_hash=contract
        )
        evidence.verify(node_id, worker="verifier-1")
    StageService(root).gate("s0")
    return root


def test_blocked_instruction_names_the_tampered_evidence(tmp_path: Path):
    """印章链塌陷导致前沿变空时，提示必须点名篡改的证据和印章失效原因。"""
    root = _stamped_s0(tmp_path)
    # 题面附件到位后 s0 还欠两份产出，节点重新进入前沿并被派了任务。
    _write(root / "problem" / "data_raw" / "attachment.csv", "a,b\n1,2\n")
    first = AdaptiveScheduler(root).next_packet()
    assert first["phase"] == "work"
    assert first.get("blocking_reasons", []) == []

    # 一枚 verified 证据被改写：全局证据审计失败 -> 印章链塌陷。
    success = root / "docs" / "success_criteria.md"
    success.write_text(
        success.read_text(encoding="utf-8") + "\n手工补充一句。\n",
        encoding="utf-8",
    )
    assert EvidenceGraph(root).audit() == [
        "problem.success: freshness=tampered"
    ]
    assert StageService(root).valid_prefix() == []

    packet = AdaptiveScheduler(root).next_packet()
    assert packet["phase"] == "blocked"
    assert packet["frontier"] == []
    instruction = packet["instruction"]
    assert instruction != OLD_BLOCKED_INSTRUCTION
    assert "problem.success" in instruction
    assert "tampered" in instruction
    assert "印章" in instruction
    assert "evidence revise problem.success" in instruction
    assert "evidence verify problem.success" in instruction
    reasons = packet["blocking_reasons"]
    assert any("证据审计失败" in item for item in reasons)
    assert any("印章链塌陷" in item and "s0" in item for item in reasons)
    assert any("problem.requirements" in item for item in reasons)
    assert packet["blocking_actions"]


def test_healthy_frontier_carries_no_blocking_noise(tmp_path: Path):
    """前沿正常时不得往 instruction 里塞诊断噪音。"""
    root = create(tmp_path / "case", "demo")
    packet = AdaptiveScheduler(root).next_packet()
    assert packet["phase"] == "work"
    assert packet["blocking_reasons"] == []
    assert packet["blocking_actions"] == []
    assert "真实原因" not in packet["instruction"]


def _rejected_model_spec(tmp_path: Path) -> Path:
    root = create(tmp_path / "case", "demo")
    _write(root / "reviews" / "s1_referee.json", {
        "verdict": "REJECT",
        "reviewer": "independent-referee",
        "findings": ["模型未给出可证伪的判据"],
    })
    EvidenceGraph(root).add(
        "model.spec", "model", "正式模型", "docs/model_spec.md",
        reviews=["reviews/s1_referee.json"],
    )
    return root


def test_verify_failure_message_carries_review_errors(tmp_path: Path):
    """verify 抛出的异常必须含 review_errors 本身，而不是让人去读 JSON。"""
    root = _rejected_model_spec(tmp_path)
    with pytest.raises(RuntimeError) as excinfo:
        EvidenceGraph(root).verify("model.spec", worker="verifier-1")
    message = str(excinfo.value)
    stored = json.loads(
        (root / ".harness" / "evidence.json").read_text(encoding="utf-8")
    )
    review_errors = stored["nodes"]["model.spec"]["verification"][
        "review_errors"
    ]
    assert review_errors
    assert message != "证据验证失败: model.spec"
    assert message.startswith("证据验证失败: model.spec")
    for item in review_errors:
        assert item in message, f"review_errors 未透出到异常: {item}"
    assert "review rejected: reviews/s1_referee.json" in message
    assert "evidence revise model.spec" in message


def test_verify_failure_message_names_the_failing_check(tmp_path: Path):
    """机械检查失败时也要说清是哪条命令、什么返回码。"""
    root = create(tmp_path / "case", "demo")
    EvidenceGraph(root).add(
        "result.nominal", "result", "标称结果", "results/nominal.json",
        checks=[{"argv": [sys.executable, "-c", "raise SystemExit(3)"]}],
    )
    _write(root / "results" / "nominal.json", {"value": 1})
    with pytest.raises(RuntimeError) as excinfo:
        EvidenceGraph(root).verify("result.nominal", worker="verifier-1")
    message = str(excinfo.value)
    assert message.startswith("证据验证失败: result.nominal")
    assert "机械检查失败 1 项" in message
    assert "returncode=3" in message
    assert "evidence revise result.nominal" in message


def test_verify_failure_cli_prints_review_errors(
    tmp_path: Path, monkeypatch, capsys
):
    """CLI 只打印异常文本，所以 review_errors 必须随异常一起出现在 stdout。"""
    root = _rejected_model_spec(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "modelharness", "evidence", "verify", "model.spec",
        "--worker", "verifier-1", "--project", str(root),
    ])
    code = cli.main()
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    message = payload["message"]
    assert "review rejected: reviews/s1_referee.json" in message
    assert "independent review missing: reviews/s1_redteam.json" in message
    assert "modelharness evidence revise model.spec" in message
