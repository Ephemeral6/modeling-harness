"""4.6 强度对齐：segment override 与 requirement 豁免走同一道评审校验。

4.5 的 segment override 只检查 `reason` 与 `override_review` 两个字段 strip 后非空，
所以“名为独立评审、实为自由文本占位”可以直接放行。requirement 层的
not_applicable 豁免同期已经要求 verdict=APPROVE 且 reviewer 非空。本文件锁住两层
使用同一份实现，并覆盖自由文本 / inline dict / 路径引用三种输入与兼容路径。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from modelharness.requirements import (
    audit_requirements,
    audit_segmentation,
    extract_sources,
)


STATEMENT = "多羔死亡率高于正常。"
APPROVED = {"verdict": "APPROVE", "reviewer": "independent-s0-auditor"}
# 仓库里两个历史 run（均已 abandoned）留下的自由文本 override 原文。
LEGACY_OVERRIDES = (
    "root-problem-architect/2026-08-04",
    "codex-architect 已与官方 PDF 逐字核对该提示。",
)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _segment_project(root: Path, override: Any) -> Path:
    """A marker-hit segment demoted to background with the given override."""
    _write(root / "problem" / "data_raw" / "problem.txt", STATEMENT)
    extract_sources(root)
    path = root / "problem" / "source_segmentation.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    segment = ledger["sources"][0]["segments"][0]
    segment["disposition"] = "background"
    segment.pop("requirement_ids", None)
    segment["reason"] = "该句是背景描述，死亡率约束由 Q2 单独承载"
    if override is not None:
        segment["override_review"] = override
    _write(path, ledger)
    return root


def _segment_override_accepted(root: Path, override: Any) -> bool:
    errors = audit_segmentation(_segment_project(root, override))
    return not any("override" in error for error in errors)


def _requirement_override_accepted(root: Path, override: Any) -> bool:
    requirement: dict[str, Any] = {
        "type": "answer",
        "mandatory": True,
        "status": "not_applicable",
        "expected": {"kind": "number"},
        "claim_ids": [],
        "reason": "该问在本届题面下不产生独立交付物",
    }
    if override is not None:
        requirement["independent_review"] = override
    _write(root / "problem" / "requirements.json", {
        "schema": 1,
        "requirements": {"Q1.answer": requirement},
    })
    return not any(
        "not_applicable" in error for error in audit_requirements(root)
    )


# --- 三种输入 -------------------------------------------------------------

def test_free_text_override_is_rejected(tmp_path: Path):
    """一句话不再是评审：写什么都放行的旧行为必须消失。"""
    root = _segment_project(tmp_path / "case", "已由独立审核者确认属背景。")
    errors = audit_segmentation(root)
    hit = [error for error in errors if "override_review" in error]
    assert len(hit) == 1
    assert "自由文本不构成独立评审" in hit[0]
    # reason 已经写了，报错只应指向 override_review 一个字段。
    assert "reason、override_review" not in hit[0]


def test_inline_signed_approval_is_accepted(tmp_path: Path):
    root = _segment_project(tmp_path / "case", dict(APPROVED))
    assert audit_segmentation(root) == []


def test_review_path_reference_is_accepted(tmp_path: Path):
    root = tmp_path / "case"
    _write(root / "reviews" / "s0_override.json", APPROVED)
    _segment_project(root, "reviews/s0_override.json")
    assert audit_segmentation(root) == []


# --- 强度细节 -------------------------------------------------------------

@pytest.mark.parametrize("override", [
    {"verdict": "REJECT", "reviewer": "independent-s0-auditor"},
    {"verdict": "APPROVE"},
    {"verdict": "APPROVE", "reviewer": "   "},
    {"reviewer": "independent-s0-auditor"},
    "reviews/does_not_exist.json",
    "reviews/s0_override.json 已 APPROVE",
    "",
    "   ",
    123,
    True,
    ["APPROVE"],
], ids=[
    "reject", "no-reviewer", "blank-reviewer", "no-verdict",
    "missing-file", "path-plus-prose", "empty", "spaces",
    "int", "bool", "list",
])
def test_weak_override_shapes_are_all_rejected(tmp_path: Path, override: Any):
    root = _segment_project(tmp_path / "case", override)
    assert any(
        "override_review" in error for error in audit_segmentation(root)
    )


def test_override_path_cannot_escape_the_project(tmp_path: Path):
    """越界路径只能被判为无效 override，不能读出项目外的 APPROVE。"""
    outside = tmp_path / "outside.json"
    _write(outside, APPROVED)
    root = _segment_project(tmp_path / "case", "../outside.json")
    assert any(
        "override_review" in error for error in audit_segmentation(root)
    )


def test_override_path_to_non_object_json_is_rejected(tmp_path: Path):
    root = tmp_path / "case"
    _write(root / "reviews" / "s0_override.json", ["APPROVE"])
    _segment_project(root, "reviews/s0_override.json")
    assert any(
        "override_review" in error for error in audit_segmentation(root)
    )


def test_hostile_override_string_does_not_crash_the_audit(tmp_path: Path):
    """自由文本会被当路径解析，非法路径必须降级为拒绝而不是抛异常。"""
    for override in ("a\x00b", "C:/Windows/win.ini", "?" * 300):
        root = _segment_project(tmp_path / f"case-{len(override)}", override)
        assert any(
            "override_review" in error for error in audit_segmentation(root)
        )


def test_error_names_the_actual_defect_of_a_linked_review(tmp_path: Path):
    """指向真实评审文件但该文件不合规时，不能报成“自由文本”。"""
    root = tmp_path / "case"
    _write(root / "reviews" / "s0_override.json", {"verdict": "APPROVE"})
    _segment_project(root, "reviews/s0_override.json")
    hit = next(
        error for error in audit_segmentation(root)
        if "override_review" in error
    )
    assert "reviews/s0_override.json 存在" in hit
    assert "自由文本" not in hit


def test_error_distinguishes_a_dangling_review_path(tmp_path: Path):
    root = _segment_project(tmp_path / "case", "reviews/s0_override.json")
    hit = next(
        error for error in audit_segmentation(root)
        if "override_review" in error
    )
    assert "指向的评审文件不存在: reviews/s0_override.json" in hit


def test_error_names_the_failing_field_of_an_inline_record(tmp_path: Path):
    root = _segment_project(
        tmp_path / "case", {"verdict": "APPROVE", "reviewer": ""}
    )
    hit = next(
        error for error in audit_segmentation(root)
        if "override_review" in error
    )
    detail = hit.split("当前 inline 评审记录 ")[1].split("；")[0]
    assert detail == "reviewer 为空"


def test_reason_still_required_alongside_the_review(tmp_path: Path):
    root = tmp_path / "case"
    _segment_project(root, dict(APPROVED))
    path = root / "problem" / "source_segmentation.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    ledger["sources"][0]["segments"][0]["reason"] = "  "
    _write(path, ledger)
    hit = [error for error in audit_segmentation(root) if "override" in error]
    assert len(hit) == 1
    assert "补齐 reason 字段" in hit[0]


# --- 两层一致（共用实现而非复制） ----------------------------------------

@pytest.mark.parametrize("override", [
    None,
    "已由独立审核者确认属背景。",
    "reviews/does_not_exist.json",
    {"verdict": "REJECT", "reviewer": "independent-s0-auditor"},
    {"verdict": "APPROVE"},
    {"verdict": "approve", "reviewer": "independent-s0-auditor"},
    dict(APPROVED),
], ids=[
    "absent", "prose", "missing-file", "reject", "no-reviewer",
    "lowercase-approve", "approved",
])
def test_segment_and_requirement_layers_agree(tmp_path: Path, override: Any):
    """同一份 override 在两层必须同判，否则说明实现被复制成了两份。"""
    segment_ok = _segment_override_accepted(tmp_path / "segment", override)
    requirement_ok = _requirement_override_accepted(
        tmp_path / "requirement", override
    )
    assert segment_ok == requirement_ok


def test_requirement_layer_path_reference_still_works(tmp_path: Path):
    """重构后 requirement 层的路径分支行为不变。"""
    root = tmp_path / "case"
    _write(root / "reviews" / "q1_na.json", APPROVED)
    assert _requirement_override_accepted(root, "reviews/q1_na.json")


# --- 兼容路径 -------------------------------------------------------------

@pytest.mark.parametrize("legacy", LEGACY_OVERRIDES)
def test_legacy_free_text_override_is_rejected_and_named(
    tmp_path: Path, legacy: str
):
    """4.6 前留下的自由文本 override 不 grandfather，但报错要说清它是什么。

    仓库内命中此形状的只有两个 abandoned 历史 run；任何写在项目内的
    grandfathering 标记都由 agent 自己书写，等于把刚堵上的洞换个字段重开。
    """
    root = _segment_project(tmp_path / f"case-{abs(hash(legacy))}", legacy)
    hit = [
        error for error in audit_segmentation(root)
        if "override_review" in error
    ]
    assert len(hit) == 1
    assert "自由文本不构成独立评审" in hit[0]
    assert legacy[:12] in hit[0]
    assert "APPROVE" in hit[0] and "reviewer" in hit[0]


def test_legacy_project_upgrades_by_pointing_at_a_real_review(tmp_path: Path):
    """迁移动作是把自由文本换成评审记录，不需要重跑分句。"""
    root = tmp_path / "case"
    _segment_project(root, LEGACY_OVERRIDES[0])
    assert audit_segmentation(root)

    _write(root / "reviews" / "s0_segment_override.json", {
        "reviewer": "independent-s0-source-auditor",
        "verdict": "APPROVE",
        "scope": ["s0001 disposition override"],
    })
    path = root / "problem" / "source_segmentation.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    ledger["sources"][0]["segments"][0]["override_review"] = (
        "reviews/s0_segment_override.json"
    )
    _write(path, ledger)
    assert audit_segmentation(root) == []
