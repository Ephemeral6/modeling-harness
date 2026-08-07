import hashlib
import json

import pytest

pytestmark = pytest.mark.regression


def test_final_render_requires_terminal_approve(
    regression_project, regression_api
):
    defect = (
        "v12 事故：s6_paper_audit 谱系最新裁决为 REJECT（或缺失）时 "
        "render_final 仍渲染 paper/final.md，带病成稿绕过终审进入交付"
    )
    render = regression_api("modelharness.sanitize", "render_final", defect)
    root = regression_project("final_render_gate")

    with pytest.raises(ValueError, match="交付门禁"):
        render(root)
    assert not (root / "paper" / "final.md").exists(), defect

    draft_hash = hashlib.sha256(
        (root / "paper" / "draft.md").read_bytes()
    ).hexdigest()
    (root / "reviews" / "s6_paper_audit_v2.json").write_text(
        json.dumps({
            "schema": 1,
            "role": "paper-audit",
            "reviewer": "cold-audit-02",
            "verdict": "APPROVE",
            "findings": [],
            "artifact_hashes": {"paper/draft.md": draft_hash},
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    final = render(root)
    assert final.is_file(), defect
    # 门禁放行后仍是同一条净化路径：证据标记必须被剥离。
    assert "[[claim.q2.ewes]]" not in final.read_text(encoding="utf-8"), defect


def test_stale_approve_does_not_open_the_gate(
    regression_project, regression_api
):
    defect = (
        "终审 APPROVE 锁定的是旧 draft 哈希；draft 改动后门禁未复核 "
        "artifact_hashes，陈旧批准放行了未审内容"
    )
    render = regression_api("modelharness.sanitize", "render_final", defect)
    root = regression_project("final_render_gate")

    (root / "reviews" / "s6_paper_audit_v2.json").write_text(
        json.dumps({
            "schema": 1,
            "role": "paper-audit",
            "reviewer": "cold-audit-02",
            "verdict": "APPROVE",
            "findings": [],
            "artifact_hashes": {"paper/draft.md": "0" * 64},
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="交付门禁"):
        render(root)
    assert not (root / "paper" / "final.md").exists(), defect
