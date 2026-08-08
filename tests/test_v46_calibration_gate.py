from __future__ import annotations

import json
from pathlib import Path

from modelharness.checks_core import evaluate_acceptance
from modelharness.scaffold import create

SEED = (
    Path(__file__).parents[1]
    / "templates" / "config" / "problem_graph.seed.json"
)


def _freeze(root: Path, value: float) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "calibration_freeze.json").write_text(
        json.dumps({
            "schema": 1,
            "entries": [{
                "id": "calib.min_planting_area_fraction",
                "kind": "management_constraint",
                "statement": "最小种植面积占地块面积的比例",
                "value": 0.2,
                "unit": "fraction",
                "origin": "self_imposed",
                "rationale": "题面未规定，为避免碎片化地块自设",
                "result_path": "model_calibration.min_planting_area_fraction",
                "frozen_at": "2026-08-08T10:00:00+08:00",
                "inherited": False,
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "results" / "model_calibration.json").write_text(
        json.dumps({"min_planting_area_fraction": value}),
        encoding="utf-8",
    )


def test_seed_graph_wires_calibration_freeze_gate():
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    acceptance = [
        check
        for node in seed["nodes"].values()
        for check in node.get("acceptance", [])
    ]
    kinds = [check.get("kind") for check in acceptance]

    assert "calibration_freeze" in kinds, (
        "自设口径冻结审计没有挂进种子图 acceptance，audit_freeze 只能靠人工调用"
    )


def test_acceptance_branch_passes_when_calibration_holds(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    _freeze(root, 0.2)

    result = evaluate_acceptance(root, [{"kind": "calibration_freeze"}])

    assert result["verdict"] == "pass"
    assert result["records"][0]["kind"] == "calibration_freeze"


def test_acceptance_branch_fails_on_silent_drift(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    _freeze(root, 0.1)

    result = evaluate_acceptance(root, [{"kind": "calibration_freeze"}])

    assert result["verdict"] != "pass"
    errors = result["records"][0]["errors"]
    assert any("min_planting_area_fraction" in error for error in errors)


def test_project_without_freeze_contract_is_clean(tmp_path: Path):
    root = create(tmp_path / "case", "demo")

    result = evaluate_acceptance(root, [{"kind": "calibration_freeze"}])

    assert result["verdict"] == "pass"
    assert result["records"][0]["errors"] == []
