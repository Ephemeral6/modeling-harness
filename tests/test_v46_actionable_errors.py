"""4.6 冒烟修复：两条错误信息必须自带可执行的下一步动作。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelharness.requirements import audit_segmentation, extract_sources
from modelharness.scaffold import create
from modelharness.workflow import WorkflowEngine


ACCEPTANCE = [{"kind": "artifact_exists", "path": "results/nominal.json"}]


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _solver_task(root: Path) -> tuple[WorkflowEngine, str]:
    engine = WorkflowEngine(root)
    task = engine.ensure_task(
        "s3:solver",
        "s3",
        "solver",
        "solve",
        ["results/nominal.json"],
        acceptance=ACCEPTANCE,
    )
    return engine, task["id"]


def test_finish_on_failed_task_names_state_and_retry_action(tmp_path: Path):
    """验收失败后再 finish，必须指向 retry 而不是 worker 名字。"""
    root = create(tmp_path / "case", "demo")
    engine, task_id = _solver_task(root)
    engine.claim(task_id, "worker-1")
    with pytest.raises(RuntimeError):
        engine.finish(task_id, "worker-1", True, {})
    assert engine.get_task(task_id)["status"] == "failed"

    with pytest.raises(ValueError) as excinfo:
        engine.finish(task_id, "worker-1", True, {})
    message = str(excinfo.value)
    assert "cannot be finished by this worker" not in message
    assert "failed" in message
    assert "task retry" in message
    assert "task claim" in message
    assert task_id in message

    engine.retry(task_id)
    engine.claim(task_id, "worker-1")
    _write(root / "results" / "nominal.json", {"ok": True})
    finished = engine.finish(task_id, "worker-1", True, {})
    assert finished["status"] == "completed"


def test_finish_by_wrong_worker_keeps_wording_and_names_holder(tmp_path: Path):
    """worker 确实不匹配时保留原文，并补上当前持有者。"""
    root = create(tmp_path / "case", "demo")
    engine, task_id = _solver_task(root)
    engine.claim(task_id, "worker-1")

    with pytest.raises(ValueError) as excinfo:
        engine.finish(task_id, "worker-2", True, {})
    message = str(excinfo.value)
    assert "task cannot be finished by this worker" in message
    assert "worker-1" in message
    assert "worker-2" in message
    assert engine.get_task(task_id)["status"] == "claimed"


def test_finish_on_pending_task_points_at_claim(tmp_path: Path):
    """未 claim 的任务 finish 失败时也要报状态而不是 worker。"""
    root = create(tmp_path / "case", "demo")
    engine, task_id = _solver_task(root)

    with pytest.raises(ValueError) as excinfo:
        engine.finish(task_id, "worker-1", True, {})
    message = str(excinfo.value)
    assert "cannot be finished by this worker" not in message
    assert "pending" in message
    assert "task claim" in message


def test_marker_segment_error_names_override_file_and_fields(tmp_path: Path):
    """override 提示必须给出真实落点文件与字段名。"""
    root = tmp_path / "case"
    _write(root / "problem" / "data_raw" / "problem.txt", "多羔死亡率高于正常。")
    extract_sources(root)
    path = root / "problem" / "source_segmentation.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    segment = ledger["sources"][0]["segments"][0]
    segment["disposition"] = "background"
    segment.pop("requirement_ids", None)
    segment["reason"] = "属于背景描述"
    _write(path, ledger)

    errors = audit_segmentation(root)
    hit = [item for item in errors if "多羔死亡率高于正常" in item]
    assert len(hit) == 1
    message = hit[0]
    assert message != (
        "命中建模标记但无 requirement/独立 override: 多羔死亡率高于正常。"
    )
    assert "problem/source_segmentation.json" in message
    assert "override_review" in message
    assert segment["id"] in message

    # 4.6 起 override_review 不再接受自由文本：必须是 APPROVE 且署名的评审记录，
    # 或指向这样一份 reviews/*.json 的路径（见 test_v46_override_strength.py）。
    _write(root / "reviews" / "s0_override.json", {
        "verdict": "APPROVE",
        "reviewer": "independent-s0-auditor",
    })
    segment["override_review"] = "reviews/s0_override.json"
    _write(path, ledger)
    assert audit_segmentation(root) == []


def test_marker_segment_error_lists_only_missing_field(tmp_path: Path):
    """只缺 reason 时提示不应要求补 override_review 以外的无关字段。"""
    root = tmp_path / "case"
    _write(root / "problem" / "data_raw" / "problem.txt", "多羔死亡率高于正常。")
    extract_sources(root)
    path = root / "problem" / "source_segmentation.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    segment = ledger["sources"][0]["segments"][0]
    segment["disposition"] = "background"
    segment.pop("requirement_ids", None)
    segment["override_review"] = "reviews/s0_override.json 已 APPROVE"
    _write(path, ledger)

    message = next(
        item for item in audit_segmentation(root)
        if "多羔死亡率高于正常" in item
    )
    assert "reason" in message
    assert "problem/source_segmentation.json" in message
