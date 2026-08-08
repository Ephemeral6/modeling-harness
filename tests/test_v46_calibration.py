"""4.6 机制 2：自设口径冻结、跨 run 继承与论文披露审计。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modelharness import cli_v31
from modelharness.calibration import (
    audit_freeze,
    build_calibration_diff,
    inherit_baseline,
    self_imposed_records,
)
from modelharness.optimization import audit_constraint_ledger
from modelharness.paper_content import audit_paper_content
from modelharness.storage import read_json


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def _entry(**overrides) -> dict:
    entry = {
        "id": "calib.min_area_fraction",
        "kind": "management_constraint",
        "statement": "单作物最小种植面积不低于地块面积的 20%。",
        "value": 0.2,
        "unit": "fraction_of_plot_area",
        "origin": "self_imposed",
        "rationale": "题面只写“便于田间管理”，20% 是自设可管理性口径。",
        "result_path": "model_calibration.min_area_fraction",
        "disclosure_anchor": "自设口径与管理假设",
        "frozen_at": "2026-08-02T17:43:49+08:00",
        "inherited": False,
    }
    entry.update(overrides)
    return entry


def _project(tmp_path: Path, name: str = "project") -> Path:
    root = tmp_path / name
    _write(root / "modeling-project.json", {"schema": 4, "title": name})
    return root


def _frozen_project(tmp_path: Path, name: str = "project", value=0.2) -> Path:
    root = _project(tmp_path, name)
    _write(root / "config" / "calibration_freeze.json", {
        "schema": 1, "entries": [_entry()],
    })
    _write(root / "results" / "model_calibration.json", {
        "schema": 1, "min_area_fraction": value,
    })
    return root


# ------------------------------------------------------------ audit_freeze


def test_project_without_freeze_is_silent(tmp_path: Path):
    root = _project(tmp_path)
    _write(root / "results" / "model_calibration.json", {"min_area": 0.1})
    assert audit_freeze(root) == []
    assert self_imposed_records(root) == []


def test_freeze_matching_results_passes(tmp_path: Path):
    root = _frozen_project(tmp_path, value=0.2)
    assert audit_freeze(root) == []


def test_freeze_detects_value_drift(tmp_path: Path):
    root = _frozen_project(tmp_path, value=0.1)
    errors = audit_freeze(root)
    assert errors
    assert all("calib.min_area_fraction" in error for error in errors)
    assert any("0.2" in error and "0.1" in error for error in errors)


def test_freeze_detects_structured_value_drift(tmp_path: Path):
    root = _project(tmp_path)
    caps = {"A": 6, "D": 4}
    _write(root / "config" / "calibration_freeze.json", {
        "schema": 1,
        "entries": [_entry(
            id="calib.group_caps",
            value=caps,
            unit="plots",
            result_path="model_calibration.group_caps",
        )],
    })
    _write(root / "results" / "model_calibration.json", {
        "schema": 1, "group_caps": {},
    })
    assert any("calib.group_caps" in e for e in audit_freeze(root))

    _write(root / "results" / "model_calibration.json", {
        "schema": 1, "group_caps": caps,
    })
    assert audit_freeze(root) == []


def test_freeze_flags_unresolvable_result_path(tmp_path: Path):
    root = _project(tmp_path)
    _write(root / "config" / "calibration_freeze.json", {
        "schema": 1,
        "entries": [
            _entry(id="calib.absent_file", result_path="absent.min_area"),
            _entry(
                id="calib.absent_field",
                result_path="model_calibration.not_here",
            ),
        ],
    })
    _write(root / "results" / "model_calibration.json", {"schema": 1})
    errors = audit_freeze(root)
    assert any(
        "calib.absent_file" in error and "results/absent.json" in error
        for error in errors
    )
    assert any(
        "calib.absent_field" in error and "model_calibration.not_here" in error
        for error in errors
    )


def test_freeze_validates_entry_shape(tmp_path: Path):
    root = _project(tmp_path)
    _write(root / "config" / "calibration_freeze.json", {
        "schema": 1,
        "entries": [
            _entry(id="calib.a", rationale="   "),
            _entry(id="calib.b", origin="statement"),
            _entry(id="calib.c", inherited="yes"),
            _entry(id="calib.a"),
        ],
    })
    _write(root / "results" / "model_calibration.json", {
        "schema": 1, "min_area_fraction": 0.2,
    })
    errors = audit_freeze(root)
    assert any("calib.a" in e and "rationale" in e for e in errors)
    assert any("calib.b" in e and "self_imposed" in e for e in errors)
    assert any("calib.c" in e and "inherited" in e for e in errors)
    assert any("duplicate" in e and "calib.a" in e for e in errors)


def test_freeze_rejects_broken_container(tmp_path: Path):
    root = _project(tmp_path)
    _write(root / "config" / "calibration_freeze.json", {"schema": 2})
    assert audit_freeze(root) == [
        "calibration_freeze missing or schema is not 1"
    ]
    _write(root / "config" / "calibration_freeze.json", {"schema": 1})
    assert audit_freeze(root) == [
        "calibration_freeze.entries must be a list"
    ]


def test_superseded_reason_releases_the_entry(tmp_path: Path):
    root = _frozen_project(tmp_path, value=0.1)
    assert audit_freeze(root)
    freeze_path = root / "config" / "calibration_freeze.json"
    freeze = read_json(freeze_path)
    freeze["entries"][0]["superseded_reason"] = (
        "S4 扫描显示 20% 口径不可辨识，本 run 显式改为 10% 并在论文披露。"
    )
    _write(freeze_path, freeze)
    assert audit_freeze(root) == []

    freeze["entries"][0]["superseded_reason"] = "  "
    _write(freeze_path, freeze)
    assert any(
        "superseded_reason" in error for error in audit_freeze(root)
    )


# ------------------------------------------------- constraint ledger origin


def _ledger_project(tmp_path: Path, constraint: dict) -> Path:
    root = _project(tmp_path, "ledger")
    _write(root / "problem" / "constraint_ledger.json", {
        "schema": 1,
        "constraints": {"constraint.dispersion": constraint},
    })
    return root


def _modeled_constraint(**overrides) -> dict:
    item = {
        "statement": "每个管理分组内启用地块数不超过上限。",
        "type": "constraint",
        "hard": True,
        "scope": "model",
        "source_requirement_ids": [],
        "status": "MODELED",
        "mathematical_form": "sum z <= U",
        "implementation": {"solver_artifact": "", "checker_artifact": ""},
    }
    item.update(overrides)
    return item


def test_legacy_ledger_without_origin_is_not_flagged(tmp_path: Path):
    root = _ledger_project(tmp_path, _modeled_constraint())
    assert audit_constraint_ledger(root, "model") == []


def test_ledger_rejects_unknown_origin(tmp_path: Path):
    root = _ledger_project(tmp_path, _modeled_constraint(origin="invented"))
    errors = audit_constraint_ledger(root, "model")
    assert any(
        "origin" in error and "constraint.dispersion" in error
        for error in errors
    )


def test_self_imposed_ledger_entry_needs_rationale_and_anchor(tmp_path: Path):
    root = _ledger_project(
        tmp_path, _modeled_constraint(origin="self_imposed")
    )
    errors = audit_constraint_ledger(root, "model")
    assert any("rationale" in error for error in errors)
    assert any("disclosure_anchor" in error for error in errors)

    root = _ledger_project(tmp_path / "ok", _modeled_constraint(
        origin="self_imposed",
        rationale="题面无分散度数值，上限为自设管理口径。",
        disclosure_anchor="自设口径与管理假设",
    ))
    assert audit_constraint_ledger(root, "model") == []
    assert [item["id"] for item in self_imposed_records(root)] == [
        "constraint.dispersion"
    ]


def test_statement_origin_needs_no_rationale(tmp_path: Path):
    root = _ledger_project(tmp_path, _modeled_constraint(origin="statement"))
    assert audit_constraint_ledger(root, "model") == []
    assert self_imposed_records(root) == []


# ----------------------------------------------------------- 跨 run 继承


def _baseline_project(tmp_path: Path) -> Path:
    baseline = _project(tmp_path, "baseline")
    _write(baseline / "config" / "calibration_freeze.json", {
        "schema": 1,
        "entries": [
            _entry(),
            _entry(
                id="calib.group_caps",
                value={"A": 6, "D": 4},
                unit="plots",
                result_path="model_calibration.group_caps",
            ),
        ],
    })
    _write(baseline / "problem" / "constraint_ledger.json", {
        "schema": 1,
        "constraints": {
            "constraint.dispersion": _modeled_constraint(
                origin="self_imposed",
                rationale="题面无分散度数值。",
                disclosure_anchor="自设口径与管理假设",
            ),
            "constraint.rotation": _modeled_constraint(origin="statement"),
        },
    })
    return baseline


def test_inherit_copies_freeze_and_registers_reference(tmp_path: Path):
    baseline = _baseline_project(tmp_path)
    root = _project(tmp_path, "run2")
    report = inherit_baseline(root, baseline)

    freeze = read_json(root / "config" / "calibration_freeze.json")
    assert freeze["schema"] == 1
    assert [entry["id"] for entry in freeze["entries"]] == [
        "calib.min_area_fraction", "calib.group_caps"
    ]
    assert all(entry["inherited"] is True for entry in freeze["entries"])
    assert freeze["inherited_from"] == baseline.resolve().as_posix()

    ledger = read_json(root / "problem" / "constraint_ledger.json")
    assert set(ledger["constraints"]) == {"constraint.dispersion"}
    assert ledger["constraints"]["constraint.dispersion"]["inherited"] is True

    manifest = read_json(root / "problem" / "import_manifest.json")
    methods = {record["method"] for record in manifest["imports"]}
    stored = {record["stored_as"] for record in manifest["imports"]}
    assert methods == {"reference"}
    assert "config/calibration_freeze.json" in stored
    assert "problem/constraint_ledger.json" in stored

    diff = read_json(root / "results" / "calibration_diff.json")
    assert diff["counts"] == {
        "same": 2, "changed": 0, "dropped": 0, "added": 0
    }
    assert report["counts"] == diff["counts"]

    with pytest.raises(ValueError, match="已存在"):
        inherit_baseline(root, baseline)


def test_calibration_diff_reports_same_changed_dropped_added(tmp_path: Path):
    baseline = _baseline_project(tmp_path)
    root = _project(tmp_path, "run2")
    inherit_baseline(root, baseline)

    freeze_path = root / "config" / "calibration_freeze.json"
    freeze = read_json(freeze_path)
    entries = {entry["id"]: entry for entry in freeze["entries"]}
    entries["calib.min_area_fraction"]["value"] = 0.1
    freeze["entries"] = [
        entries["calib.min_area_fraction"],
        _entry(id="calib.risk_weight", value=0.35, unit="weight"),
    ]
    _write(freeze_path, freeze)

    diff = build_calibration_diff(root)
    states = {item["id"]: item["state"] for item in diff["entries"]}
    assert states == {
        "calib.min_area_fraction": "changed",
        "calib.group_caps": "dropped",
        "calib.risk_weight": "added",
    }
    assert diff["counts"] == {
        "same": 0, "changed": 1, "dropped": 1, "added": 1
    }
    changed = next(
        item for item in diff["entries"] if item["state"] == "changed"
    )
    assert changed["baseline_value"] == 0.2 and changed["value"] == 0.1
    assert diff["baseline"] == baseline.resolve().as_posix()


def test_changed_inherited_entry_is_blocked_until_superseded(tmp_path: Path):
    baseline = _baseline_project(tmp_path)
    root = _project(tmp_path, "run2")
    inherit_baseline(root, baseline)
    _write(root / "results" / "model_calibration.json", {
        "schema": 1, "min_area_fraction": 0.1, "group_caps": {"A": 6, "D": 4},
    })

    errors = audit_freeze(root)
    assert any("calib.min_area_fraction" in error for error in errors)

    freeze_path = root / "config" / "calibration_freeze.json"
    freeze = read_json(freeze_path)
    for entry in freeze["entries"]:
        if entry["id"] == "calib.min_area_fraction":
            entry["value"] = 0.1
            entry["superseded_reason"] = "本 run 显式把最小面积口径改为 10%。"
    _write(freeze_path, freeze)
    assert audit_freeze(root) == []


def test_inherit_rejects_missing_or_self_baseline(tmp_path: Path):
    root = _project(tmp_path, "run2")
    with pytest.raises(ValueError, match="baseline"):
        inherit_baseline(root, tmp_path / "absent")
    with pytest.raises(ValueError, match="baseline"):
        inherit_baseline(root, root)


def test_intake_baseline_cli_inherits_calibration(
    tmp_path: Path, monkeypatch, capsys
):
    baseline = _baseline_project(tmp_path)
    harness = tmp_path / "harness"
    harness.mkdir()
    code = run_cli(
        monkeypatch,
        "intake",
        "--title", "2024C 第二次 run",
        "--prompt", "沿用上一次的自设口径重跑同一题。",
        "--root", str(harness),
        "--baseline", str(baseline),
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    project = Path(report["project"])
    assert report["baseline"] == baseline.resolve().as_posix()
    freeze = read_json(project / "config" / "calibration_freeze.json")
    assert len(freeze["entries"]) == 2
    assert all(entry["inherited"] is True for entry in freeze["entries"])
    manifest = read_json(project / "problem" / "import_manifest.json")
    assert {record["method"] for record in manifest["imports"]} == {"reference"}
    assert (project / "results" / "calibration_diff.json").is_file()


def test_intake_without_baseline_writes_no_freeze(
    tmp_path: Path, monkeypatch, capsys
):
    harness = tmp_path / "harness"
    harness.mkdir()
    code = run_cli(
        monkeypatch,
        "intake",
        "--title", "全新题目",
        "--prompt", "第一次做这道题。",
        "--root", str(harness),
    )
    assert code == 0
    project = Path(json.loads(capsys.readouterr().out)["project"])
    assert not (project / "config" / "calibration_freeze.json").exists()
    assert audit_freeze(project) == []


# --------------------------------------------------------------- 论文披露


def _paper_project(tmp_path: Path, *, enabled: bool = True) -> Path:
    root = _frozen_project(tmp_path, "paper")
    _write(root / "config" / "delivery_profile.json", {
        "name": "cumcm",
        "paper_content_contract": "config/paper_content_contract.json",
    })
    contract = {
        "schema": 1,
        "body": "paper/final.md",
        "appendices": [],
        "obligation_defaults": {},
    }
    if enabled:
        contract["self_imposed_disclosure"] = {
            "enabled": True, "obligation": "self_imposed_assumptions",
        }
    _write(root / "config" / "paper_content_contract.json", contract)
    _write(root / "paper" / "final.md", "# 论文\n\n## 模型建立\n\n以整数规划刻画。\n")
    _write(root / "paper" / "content_coverage.json", {
        "schema": 1,
        "contract": "config/paper_content_contract.json",
        "contract_sha256": "stale-on-purpose",
        "body": "paper/final.md",
        "appendices": [],
        "requirements": {},
    })
    return root


def _disclosure_issues(root: Path) -> list[str]:
    return [
        issue for issue in audit_paper_content(root)
        if "self_imposed_assumptions" in issue
    ]


def test_paper_audit_requires_a_non_empty_disclosure_section(tmp_path: Path):
    root = _paper_project(tmp_path)
    issues = _disclosure_issues(root)
    assert any("自设口径与管理假设" in issue for issue in issues)

    final = root / "paper" / "final.md"
    final.write_text(
        final.read_text(encoding="utf-8") + "\n## 自设口径与管理假设\n",
        encoding="utf-8",
    )
    assert _disclosure_issues(root)

    final.write_text(
        final.read_text(encoding="utf-8")
        + "\n本文自定单作物最小种植面积为地块面积的 20%，题面并未给出该数值。\n",
        encoding="utf-8",
    )
    assert _disclosure_issues(root) == []


def test_paper_audit_is_silent_without_contract_section(tmp_path: Path):
    root = _paper_project(tmp_path, enabled=False)
    assert _disclosure_issues(root) == []


def test_paper_audit_is_silent_without_self_imposed_entries(tmp_path: Path):
    root = _paper_project(tmp_path)
    (root / "config" / "calibration_freeze.json").unlink()
    assert _disclosure_issues(root) == []
