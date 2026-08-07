from __future__ import annotations

import json

from modelharness.checks_core import evaluate_acceptance
from modelharness.optimization import assess_optimization, optimization_relevant
from modelharness.search_quality import (
    audit_incumbent_dominance,
    audit_search_budget,
    audit_search_portfolio,
    audit_search_quality,
    search_quality_enabled,
)
from modelharness.util import sha256


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _pin_budget_run(root, run_id):
    _write(root / ".harness" / "tool_runs" / f"{run_id}.json", {
        "schema": 1,
        "id": run_id,
        "node_id": "s3.solver_validation",
        "returncode": 0,
        "outputs": [
            {
                "path": "results/research_diagnostics.json",
                "exists": True,
                "sha256": "recorded-at-run-time",
            }
        ],
    })


def _compliant_project(tmp_path):
    """A fully-instrumented optimization project with zero expected errors."""
    root = tmp_path / "project"
    _write(root / "config" / "optimization_assurance.json", {
        "schema": 1,
        "mode": "required",
        "search_quality": {
            "mode": "auto",
            "search_budget": {"wall_time_overrun_factor": 1.5},
        },
    })
    run_id = "1730000001-cafe00000001"
    _pin_budget_run(root, run_id)
    _write(root / "results" / "research_diagnostics.json", {
        "schema": 1,
        "searches": {
            "Q1.master_mip": {
                "objective": "total_profit_yuan",
                "sense": "max",
                "budget": {
                    "tool_run_id": run_id,
                    "time_limit_seconds": 180,
                    "wall_time_seconds": 180.4,
                    "nodes_explored": 5120,
                    "incumbent_updates": [
                        {"time_seconds": 12.5, "objective": 98.0},
                        {"time_seconds": 171.2, "objective": 100.0},
                    ],
                    "final_gap_fraction": 0.004,
                    "termination": "time_limit",
                },
            }
        },
    })
    _write(root / "problem" / "constraint_ledger.json", {
        "schema": 1,
        "constraints": {
            "constraint.Q1.assignment": {
                "statement": "每块地每季最多整数指派一种作物",
                "type": "constraint",
                "hard": True,
                "scope": "source",
                "source_requirement_ids": [],
                "status": "MODELED",
                "reason": "",
                "mathematical_form": "x[p,c,t] in {0,1} binary assignment",
                "implementation": {
                    "solver_artifact": "",
                    "checker_artifact": "",
                },
            }
        },
    })
    baseline = root / "results" / "baselines" / "greedy_plan.json"
    _write(baseline, {"plan": "repeat-2023", "objective": 97.5})
    _write(root / "results" / "incumbent_dominance_audit.json", {
        "schema": 1,
        "sense": "max",
        "incumbent_objective": 100.0,
        "baselines": [
            {
                "id": "greedy-2023-repeat",
                "kind": "greedy",
                "objective": 97.5,
                "artifact": "results/baselines/greedy_plan.json",
                "sha256": sha256(baseline),
                "checker_pass": True,
            }
        ],
    })
    _write(root / "config" / "search_policy.json", {
        "schema": 1,
        "warm_start": {
            "allowed": True,
            "source_sha256": ["0" * 64],
            "note": "prior incumbent used as warm start only",
        },
    })
    _write(root / "results" / "search_portfolio.json", {
        "schema": 1,
        "search_id": "Q1.master_mip",
        "arms": [
            {
                "id": "warm-main",
                "kind": "warm_start",
                "seed": 0,
                "method": "mip_highs",
                "incumbent_objective": 100.0,
                "tool_run_ids": [run_id],
            },
            {
                "id": "cold-seed1",
                "kind": "cold_start",
                "seed": 1,
                "method": "mip_highs",
                "incumbent_objective": 99.2,
                "tool_run_ids": [],
            },
        ],
        "selected_arm": "warm-main",
    })
    return root


def test_compliant_project_yields_zero_errors(tmp_path):
    root = _compliant_project(tmp_path)
    assert search_quality_enabled(root) is True
    assert audit_search_budget(root) == []
    assert audit_incumbent_dominance(root) == []
    assert audit_search_portfolio(root) == []
    assert audit_search_quality(root) == []


def test_assess_optimization_reports_search_quality_channel(tmp_path):
    root = _compliant_project(tmp_path)
    assessment = assess_optimization(root)
    assert assessment["relevant"] is True
    assert assessment["checks"]["search_quality"] is True
    assert not any(
        blocker.startswith("search_quality:")
        for blocker in assessment["machine_blockers"]
    )


def test_legacy_component_solver_telemetry_is_never_flagged(tmp_path):
    """P0 lesson: 2024C stores solver telemetry under cases[].component_solver,
    not searches[].budget; such legacy layouts must yield zero errors."""
    root = tmp_path / "legacy"
    _write(root / "config" / "optimization_assurance.json", {
        "schema": 1,
        "mode": "required",
    })
    _write(root / "results" / "research_diagnostics.json", {
        "schema": 1,
        "status": "pass",
        "cases": [
            {
                "name": "unsold_waste",
                "component_solver": [
                    {
                        "component": "open_grain",
                        "economic_status": 1,
                        "economic_mip_gap": 0.0283,
                        "economic_mip_node_count": 2,
                        "economic_seconds": 180.03,
                    }
                ],
            }
        ],
    })
    assert search_quality_enabled(root) is True
    assert audit_search_quality(root) == []


def test_pre_v45_ledger_project_owes_no_new_contracts(tmp_path):
    """A 4.2-instrumented legacy run (combinatorial ledger, but a config
    predating the search_quality section) must not be asked retroactively
    for the 4.5 contract files."""
    root = tmp_path / "prev45"
    _write(root / "config" / "optimization_assurance.json", {
        "schema": 1,
        "mode": "required",
        "human_policy": "risk_triggered",
    })
    _write(root / "problem" / "constraint_ledger.json", {
        "schema": 1,
        "constraints": {
            "constraint.Q1.pens": {
                "statement": "整数排程约束",
                "type": "constraint",
                "hard": True,
                "scope": "source",
                "source_requirement_ids": [],
                "status": "MODELED",
                "reason": "",
                "mathematical_form": "y[b,t] in {0,1} binary occupancy",
                "implementation": {
                    "solver_artifact": "",
                    "checker_artifact": "",
                },
            }
        },
    })
    _write(root / "config" / "search_policy.json", {
        "schema": 1,
        "warm_start": {"allowed": True, "source_sha256": ["0" * 64]},
    })
    assert search_quality_enabled(root) is True
    assert audit_incumbent_dominance(root) == []
    assert audit_search_portfolio(root) == []
    assert audit_search_quality(root) == []

    config_path = root / "config" / "optimization_assurance.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["search_quality"] = {"mode": "auto"}
    _write(config_path, config)
    errors = audit_search_quality(root)
    assert any("无廉价对照" in error for error in errors)
    assert any("cold_start" in error for error in errors)


def test_non_optimization_project_is_inactive(tmp_path):
    root = tmp_path / "plain"
    _write(root / "results" / "search_portfolio.json", {
        "schema": 1,
        "search_id": "Q1",
        "arms": [],
        "selected_arm": "missing",
    })
    assert optimization_relevant(root) is False
    assert search_quality_enabled(root) is False
    assert audit_search_quality(root) == []


def test_mode_disabled_is_an_escape_hatch(tmp_path):
    root = _compliant_project(tmp_path)
    diag_path = root / "results" / "research_diagnostics.json"
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    del diag["searches"]["Q1.master_mip"]["budget"]["tool_run_id"]
    _write(diag_path, diag)
    assert audit_search_budget(root) != []
    config_path = root / "config" / "optimization_assurance.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["search_quality"]["mode"] = "disabled"
    _write(config_path, config)
    assert search_quality_enabled(root) is False
    assert audit_search_quality(root) == []
    assert audit_search_budget(root) == []


def test_budget_field_and_wall_time_rules(tmp_path):
    root = _compliant_project(tmp_path)
    diag_path = root / "results" / "research_diagnostics.json"
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    budget = diag["searches"]["Q1.master_mip"]["budget"]
    budget["wall_time_seconds"] = 400.0
    budget["termination"] = "gave_up"
    del budget["nodes_explored"]
    _write(diag_path, diag)
    errors = audit_search_budget(root)
    assert any("wall_time" in error for error in errors)
    assert any("termination" in error for error in errors)
    assert any("nodes_explored" in error for error in errors)


def test_budget_run_must_cover_research_diagnostics(tmp_path):
    root = _compliant_project(tmp_path)
    run_path = (
        root / ".harness" / "tool_runs" / "1730000001-cafe00000001.json"
    )
    record = json.loads(run_path.read_text(encoding="utf-8"))
    record["outputs"] = [{"path": "results/other.json", "exists": True}]
    _write(run_path, record)
    errors = audit_search_budget(root)
    assert any("research_diagnostics" in error for error in errors)


def test_missing_cheap_baseline_on_hard_combinatorial(tmp_path):
    root = _compliant_project(tmp_path)
    (root / "results" / "incumbent_dominance_audit.json").unlink()
    errors = audit_incumbent_dominance(root)
    assert any("无廉价对照" in error for error in errors)


def test_stale_baseline_artifact_hash_is_flagged(tmp_path):
    root = _compliant_project(tmp_path)
    _write(
        root / "results" / "baselines" / "greedy_plan.json",
        {"plan": "tampered", "objective": 97.5},
    )
    errors = audit_incumbent_dominance(root)
    assert any("sha256" in error for error in errors)


def test_portfolio_best_arm_must_be_selected(tmp_path):
    root = _compliant_project(tmp_path)
    path = root / "results" / "search_portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["arms"][1]["incumbent_objective"] = 104.0
    _write(path, data)
    errors = audit_search_portfolio(root)
    assert any("最优未被采用" in error for error in errors)


def test_starved_search_needs_second_arm(tmp_path):
    root = _compliant_project(tmp_path)
    diag_path = root / "results" / "research_diagnostics.json"
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    budget = diag["searches"]["Q1.master_mip"]["budget"]
    budget["nodes_explored"] = 2
    _write(diag_path, diag)
    portfolio_path = root / "results" / "search_portfolio.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    portfolio["arms"] = [portfolio["arms"][0]]
    _write(portfolio_path, portfolio)
    errors = audit_search_portfolio(root)
    assert any("搜索饥饿" in error for error in errors)


def test_checks_core_gating_and_branch(tmp_path):
    root = _compliant_project(tmp_path)
    acceptance = [{"kind": "search_quality", "when": "optimization_relevant"}]
    result = evaluate_acceptance(root, acceptance)
    assert result["verdict"] == "pass"
    assert result["records"][0]["kind"] == "search_quality"

    path = root / "results" / "search_portfolio.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["selected_arm"] = "no-such-arm"
    _write(path, data)
    result = evaluate_acceptance(root, acceptance)
    assert result["verdict"] == "fail"
    assert result["records"][0]["errors"]

    plain = tmp_path / "plain"
    plain.mkdir()
    result = evaluate_acceptance(plain, acceptance)
    assert result["verdict"] == "pass"
    assert result["records"][0].get("skipped") is True
