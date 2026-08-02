from __future__ import annotations

import json

from modelharness.claims import audit_holdout
from modelharness.opportunities import (
    detect_infeasibility,
    detect_rank_flip,
    detect_unmaterialized_branches,
    render_assumptions,
)
from modelharness.sanitize import sanitize_report


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _holdout_project(tmp_path, *, acknowledged=False, unbiased=True):
    root = tmp_path
    _write(root / "config/claim_bindings.json", {
        "schema": 1,
        "claims": {
            "Q.loss": {
                "source": {
                    "artifact": "results/loss.json",
                    "pointer": "/loss",
                    "tool_run_id": "run-report",
                },
                "value_type": "number",
                "scenario_set": "report",
                "unbiased_final": unbiased,
            }
        },
    })
    hashes = {
        "screen": "hash-screen",
        "selection": "hash-selection",
        "report": "hash-report",
        "stress": "hash-stress",
    }
    if acknowledged:
        hashes["report"] = hashes["selection"]
    _write(root / "results/scenario_sets.json", {
        "schema": 1,
        "selection_bias_acknowledged": acknowledged,
        "sets": {
            name: {
                "n": 10,
                "scenario_sha256": value,
                **({"sealed": not acknowledged} if name == "report" else {}),
            }
            for name, value in hashes.items()
        },
        "usage": [
            {"set": "selection", "purpose": "policy_ranking"},
            {
                "set": "report",
                "purpose": (
                    "policy_ranking" if acknowledged else "headline_estimate"
                ),
            },
        ],
    })
    _write(root / ".harness/tool_runs/run-report.json", {
        "schema": 1,
        "id": "run-report",
        "inputs": [{"scenario_sha256": "hash-report" if not acknowledged else "hash-selection"}],
        "outputs": [{"path": "results/loss.json"}],
    })
    return root


def test_holdout_accepts_distinct_report_provenance(tmp_path):
    root = _holdout_project(tmp_path)

    assert audit_holdout(root) == []


def test_acknowledged_selection_bias_requires_downgrade_and_qualification(tmp_path):
    root = _holdout_project(tmp_path, acknowledged=True, unbiased=True)
    errors = audit_holdout(root)
    assert any("unbiased_final" in error for error in errors)
    assert any("限定语" in error for error in errors)
    binding_path = root / "config/claim_bindings.json"
    bindings = json.loads(binding_path.read_text(encoding="utf-8"))
    bindings["claims"]["Q.loss"]["unbiased_final"] = False
    _write(binding_path, bindings)
    _write(root / "paper/final.md", "该终评存在选择偏差，结果可能偏乐观。\n")

    assert audit_holdout(root) == []
    assert not any(
        item["kind"] == "selection_bias_qualification_missing"
        for item in sanitize_report(root)["violations"]
    )


def test_only_load_bearing_affordable_assumption_branches_trigger():
    assumptions = {
        "trigger": {
            "forced_by_source": False,
            "estimated_impact": "medium",
            "cost_acceptable": True,
            "dominance_proof": None,
            "alternative_branches": [
                {"id": "alternative", "materialized": False, "results": None}
            ],
        },
        "forced": {
            "forced_by_source": True,
            "estimated_impact": "high",
            "cost_acceptable": True,
            "alternative_branches": [
                {"id": "alternative", "materialized": False, "results": None}
            ],
        },
        "proved": {
            "forced_by_source": False,
            "estimated_impact": "high",
            "cost_acceptable": True,
            "dominance_proof": "evidence.bound",
            "alternative_branches": [
                {"id": "alternative", "materialized": False, "results": None}
            ],
        },
    }

    found = detect_unmaterialized_branches(assumptions)

    assert [item["assumption_id"] for item in found] == ["trigger"]
    assert found[0]["severity"] == "high"


def test_parameter_free_stress_triggers_and_assumption_render(tmp_path):
    diag = {
        "searches": {
            "policy": {
                "baseline_ranking": ["A", "B", "C"],
                "stress_rankings": [["C", "B", "A"]],
                "stress_scenarios": [
                    {
                        "id": "capacity",
                        "hard_constraints": {
                            "capacity": {"satisfied": False}
                        },
                    }
                ],
            }
        }
    }
    assert detect_rank_flip(diag)[0]["trigger"] == "rank_flip"
    assert detect_infeasibility(diag)[0]["severity"] == "high"
    _write(tmp_path / "docs/assumptions.json", {
        "pooling": {
            "statement": "允许合栏",
            "forced_by_source": False,
            "estimated_impact": "high",
            "direction_of_bias": "conservative",
            "alternative_branches": [
                {"id": "separate", "materialized": False}
            ],
            "resolution": "unresolved",
        }
    })

    output = render_assumptions(tmp_path)

    assert "pooling" in output.read_text(encoding="utf-8")
    assert "未物化" in output.read_text(encoding="utf-8")
