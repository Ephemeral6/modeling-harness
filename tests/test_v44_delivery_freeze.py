from __future__ import annotations

import json
from pathlib import Path

import pytest

from modelharness import stages
from modelharness.contracts import STAGES
from modelharness.delivery_core import freeze_delivery, verify_freeze
from modelharness.episode import capture_episode
from modelharness.lifecycle import get_status, set_status
from modelharness.sanitize import INTERNAL_ARTIFACTS, render_final
from modelharness.scaffold import create
from modelharness.storage import read_json
from modelharness.util import sha256


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _delivery_ready(tmp_path: Path) -> Path:
    """终审 APPROVE、成稿已渲染，仅 s6 印章由各测试自行决定。"""
    root = create(tmp_path / "case", "delivery freeze")
    _write(root / "config" / "delivery_profile.json", {
        "name": "general",
        "version": "1.0.0",
        "renderer": "evidence_report",
    })
    _write(
        root / "paper" / "draft.md",
        "# 决策\n\n年出栏上界为 1387 只。[[claim.q1]]\n",
    )
    _write(root / "reviews" / "s6_paper_audit.json", {
        "schema": 1,
        "role": "paper-audit",
        "reviewer": "cold-audit-01",
        "verdict": "APPROVE",
        "findings": [],
        "artifact_hashes": {
            "paper/draft.md": sha256(root / "paper" / "draft.md"),
        },
    })
    _write(root / ".harness" / "stamps" / "s6.json", {
        "schema": 3, "stage": "s6",
    })
    render_final(root)
    return root


def test_freeze_refuses_without_s6_stamp_in_valid_prefix(tmp_path: Path):
    root = _delivery_ready(tmp_path)
    with pytest.raises(ValueError, match="s6"):
        freeze_delivery(root, "premature freeze")
    assert get_status(root) == "active"
    assert not (root / "paper" / "delivery_freeze.json").exists()


def test_freeze_refuses_without_terminal_approve(tmp_path: Path, monkeypatch):
    root = _delivery_ready(tmp_path)
    monkeypatch.setattr(
        stages._StageService, "valid_prefix", lambda self: list(STAGES)
    )
    review = root / "reviews" / "s6_paper_audit.json"
    record = read_json(review)
    record["verdict"] = "REJECT"
    _write(review, record)
    with pytest.raises(ValueError, match="交付门禁"):
        freeze_delivery(root, "audit rejected")
    assert get_status(root) == "active"


def test_freeze_writes_manifest_and_verify_reports_drift(
    tmp_path: Path, monkeypatch
):
    root = _delivery_ready(tmp_path)
    monkeypatch.setattr(
        stages._StageService, "valid_prefix", lambda self: list(STAGES)
    )
    manifest = freeze_delivery(root, "all preconditions satisfied")
    assert get_status(root) == "delivered"

    stored = read_json(root / "paper" / "delivery_freeze.json")
    assert stored["schema"] == 1
    for relative in ("paper/draft.md", "paper/final.md"):
        assert stored["files"][relative] == manifest["files"][relative]
    assert "paper/final_preview.md" not in stored["files"]
    assert stored["s6_stamp_sha256"] == sha256(
        root / ".harness" / "stamps" / "s6.json"
    )
    assert stored["terminal_review"]["path"] == "reviews/s6_paper_audit.json"
    assert stored["terminal_review"]["sha256"] == sha256(
        root / "reviews" / "s6_paper_audit.json"
    )
    assert verify_freeze(root)["ok"] is True

    final = root / "paper" / "final.md"
    final.write_text(
        final.read_text(encoding="utf-8") + "\n冻结后追加的一行。\n",
        encoding="utf-8",
    )
    report = verify_freeze(root)
    assert report["ok"] is False
    assert any("paper/final.md" in item for item in report["drift"])


def test_preview_escape_hatch_writes_internal_artifact_only(tmp_path: Path):
    # scaffold 默认模板：cumcm/mcm_icm 启用 paper_content_contract。
    root = create(tmp_path / "case", "preview escape hatch")
    profile = read_json(root / "config" / "delivery_profile.json")
    profile["paper_content_contract"] = "config/paper_content_contract.json"
    _write(root / "config" / "delivery_profile.json", profile)
    _write(
        root / "paper" / "draft.md",
        "# 决策\n\n年出栏上界为 1387 只。[[claim.q1]]\n",
    )

    with pytest.raises(ValueError, match="交付门禁"):
        render_final(root)

    target = render_final(root, preview=True)
    assert target == root / "paper" / "final_preview.md"
    assert "paper/final_preview.md" in INTERNAL_ARTIFACTS
    assert not (root / "paper" / "final.md").exists()
    assert "[[claim.q1]]" not in target.read_text(encoding="utf-8")
    # 预览不得伪装成交付物：claim_map 与交付报告都不产出。
    assert not (root / "paper" / "claim_map.json").exists()
    assert not (root / "results" / "delivery_check.json").exists()


def test_capture_episode_releases_delivered(tmp_path: Path):
    root = create(tmp_path / "case", "delivered episode")
    set_status(root, "delivered", "delivery freeze passed")
    destination = capture_episode(root, tmp_path / "ep-delivered")
    manifest = read_json(destination / "episode.json")
    assert manifest["status"] == "delivered"
