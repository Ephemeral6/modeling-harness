from __future__ import annotations

import json
from pathlib import Path

from modelharness.checks_core import evaluate_acceptance
from modelharness.evaluation import _answer_quality
from modelharness.paper import _pdf_page_count
from modelharness.paper_content import (
    audit_paper_content,
    initialize_content_coverage,
)
from modelharness.profiles import ProfileService
from modelharness.scaffold import create
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


def _contest_project(tmp_path: Path) -> Path:
    root = create(tmp_path / "project", "paper content contract")
    ProfileService(root).use("cumcm")
    _write(root / "problem/requirements.json", {
        "schema": 1,
        "requirements": {
            "Q2.answer": {
                "type": "answer",
                "mandatory": True,
                "question": "给出最大养殖规模和 229 日生产计划。",
                "claim_ids": ["claim.ewes"],
                "status": "satisfied",
                "paper_obligations": [
                    {
                        "kind": "production_plan",
                        "placement": "appendix",
                        "formats": ["table"],
                        "requires_body_reference": True,
                        "min_evidence": 1,
                        "artifact_contract": {
                            "kind": "csv_table",
                            "min_rows": 229,
                            "required_fields": ["day", "mating_ewes", "pens"],
                        },
                    }
                ],
            }
        },
    })
    _write(root / ".harness/evidence.json", {
        "schema": 4,
        "revision": 1,
        "nodes": {
            "claim.ewes": {
                "kind": "claim",
                "status": "verified",
                "freshness": "valid",
                "depends_on": [],
            }
        },
    })
    return root


def test_thin_but_verified_paper_is_rejected(tmp_path: Path):
    root = _contest_project(tmp_path)
    initialize_content_coverage(root)
    _write(root / "paper/final.md", "# 问题二\n\n最大规模为 427 只。\n")
    _write(root / "paper/technical_appendix.md", "# 技术附件\n")

    errors = audit_paper_content(root)

    assert errors
    assert any("direct_answer" in error for error in errors)
    report = json.loads(
        (root / "results/paper_coverage.json").read_text(encoding="utf-8")
    )
    assert report["verdict"] == "FAIL"
    assert report["summary"]["passed"] < report["summary"]["required"]


def test_detailed_body_and_executable_appendix_pass(tmp_path: Path):
    root = _contest_project(tmp_path)
    matrix = initialize_content_coverage(root)
    body = """# 问题二

## 直接答案

最大规模为 427 只，229 日计划见技术附件表 A1。

## 模型定义

令 $x_t$ 为第 $t$ 日开配母羊数，目标为 $\\max \\sum_t x_t$。

## 推导过程

由循环卷积 $o_t=\\sum_k x_{t-k}d_k$，并结合向上取整栏位约束得到上界 434。

## 求解算法

```text
for day in planning_horizon:
    add occupancy and ram-concurrency constraints
solve CP-SAT model and export the incumbent schedule
```

## 独立验证

独立检查器逐日重算栏位、种公羊并发和边界，实际最大违反量为 0，故验证通过。

## 管理解释

427 只是在既定栏位共享解释下的有界间隙解，管理者应按表 A1 每日执行并保留缓冲。
"""
    appendix = """# 技术附件

## 表 A1：229 日生产计划

| day | mating_ewes | pens |
|---:|---:|---:|
| 1 | 2 | 14 |

## 权威数值锁定表

机器字段、哈希和逐字段审计记录仅在本附件保存。
"""
    _write(root / "paper/final.md", body)
    _write(root / "paper/technical_appendix.md", appendix)
    schedule = root / "results/production_plan.csv"
    rows = ["day,mating_ewes,pens"] + [
        f"{day},{2 if day <= 198 else 1},{14 + day % 3}"
        for day in range(1, 230)
    ]
    _write(schedule, "\n".join(rows) + "\n")

    values = {
        "direct_answer": (
            "paper/final.md", "直接答案", "最大规模为 427 只"
        ),
        "model_definition": (
            "paper/final.md", "模型定义", "令 $x_t$ 为第 $t$ 日"
        ),
        "derivation": (
            "paper/final.md", "推导过程", "循环卷积"
        ),
        "algorithm": (
            "paper/final.md", "求解算法", "solve CP-SAT model"
        ),
        "validation": (
            "paper/final.md", "独立验证", "实际最大违反量为 0"
        ),
        "interpretation": (
            "paper/final.md", "管理解释", "既定栏位共享解释"
        ),
        "production_plan": (
            "paper/technical_appendix.md", "表 A1", "229 日生产计划"
        ),
    }
    disclosure = {
        "method": "independent day-by-day checker",
        "expected": "all violations <= 0",
        "actual": "maximum violation = 0",
        "conclusion": "pass",
    }
    for kind, (target, section, anchor) in values.items():
        item = matrix["requirements"]["Q2.answer"]["obligations"][kind]
        item.update({
            "target": target,
            "section": section,
            "anchors": [anchor],
            "evidence_ids": ["claim.ewes"],
        })
        if kind == "validation":
            item["disclosure"] = disclosure
        if kind == "production_plan":
            item["body_anchor"] = "技术附件表 A1"
            item["artifact"] = {
                "path": "results/production_plan.csv",
                "sha256": sha256(schedule),
            }
    _write(root / "paper/content_coverage.json", matrix)

    assert audit_paper_content(root) == []
    acceptance = evaluate_acceptance(root, [{
        "kind": "paper_content_contract",
        "when": "paper_content_contract_enabled",
    }])
    assert acceptance["ok"] is True
    quality = _answer_quality(root)
    assert quality["paper_content_coverage_rate"] == 1.0
    assert quality["quality_dimensions"]["exposition_completeness"]["score"] == 1.0


def test_machine_lock_table_is_appendix_only_and_page_target_is_advisory(
    tmp_path: Path,
):
    root = _contest_project(tmp_path)
    initialize_content_coverage(root)
    _write(
        root / "paper/final.md",
        "# 权威数值锁定表\n\n该表放在正文。\n",
    )
    _write(root / "paper/technical_appendix.md", "# 技术附件\n")
    _write(root / "paper/render_report.json", {
        "schema": 1,
        "execution_status": "COMPLETED",
        "verdict": "PASS",
        "page_count": 7,
    })

    errors = audit_paper_content(root)

    assert any("appendix-only" in error for error in errors)
    report = json.loads(
        (root / "results/paper_coverage.json").read_text(encoding="utf-8")
    )
    assert any("18–25" in warning for warning in report["warnings"])


def test_pdf_page_counter_feeds_advisory_target(tmp_path: Path):
    pdf = tmp_path / "fixture.pdf"
    _write(
        pdf,
        "%PDF-1.7\n/Type /Pages\n/Type /Page\n/Type /Page\n/Type /Page\n",
    )

    assert _pdf_page_count(pdf) == 3
