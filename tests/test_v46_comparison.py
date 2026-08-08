from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modelharness import cli_v31, lifecycle
from modelharness.comparison import (
    ISOLATION_CLAUSE, TERMINAL_STATUSES, audit_comparison, load_manifest,
    protocol_root,
)
from modelharness.util import sha256

REPO = Path(cli_v31.__file__).resolve().parents[1]
SHIPPED = REPO / "benchmarks" / "protocols" / "2026-blind-codex-vs-harness.json"
TEMPLATE = REPO / "benchmarks" / "protocols" / "TEMPLATE_comparison.json"


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


def _edit(manifest: Path, mutate) -> Path:
    data = json.loads(manifest.read_text(encoding="utf-8"))
    mutate(data)
    _write(manifest, data)
    return manifest


def _protocol(tmp_path: Path) -> Path:
    """A fully compliant protocol: five checks all pass on this layout."""
    base = tmp_path / "protocol"
    _write(base / "arms" / "harness_a" / "modeling-project.json", {
        "schema": 4,
        "title": "arm a",
        "status": "completed",
        "status_history": [
            {"status": "interrupted", "at": "2026-08-02T10:00:00+08:00",
             "reason": "session died"},
            {"status": "completed", "at": "2026-08-02T12:00:00+08:00",
             "reason": "all subquestions closed"},
        ],
    })
    _write(base / "arms" / "harness_a" / "src" / "solver.py", "VALUE = 1\n")
    _write(base / "arms" / "external_b" / "optimize.py", "VALUE = 2\n")
    _write(base / "reports" / "comparison.json", {
        "schema": 1,
        "arms": [
            {"arm_id": "harness_a", "objective_value": 12.5, "verdict": "PASS"},
            {"arm_id": "external_b", "objective_value": 11.0, "verdict": "PASS"},
        ],
    })
    _write(base / "manifest.json", {
        "schema": 1,
        "id": "unit-protocol",
        "problem_ids": ["2023D"],
        "arms": [
            {"arm_id": "harness_a", "kind": "harness",
             "project_path": "arms/harness_a", "model": "m1", "seed": 7,
             "producer": "worker_a"},
            {"arm_id": "external_b", "kind": "external_baseline",
             "artifact_dir": "arms/external_b", "model": "m2",
             "producer": "worker_b"},
        ],
        "baseline_artifacts": [{
            "baseline_id": "external_b_run",
            "path": "optimize.py",
            "sha256": sha256(base / "arms" / "external_b" / "optimize.py"),
        }],
        "judge": {
            "command": "checks/judge.py",
            "frozen_params_sha256": "a" * 64,
            "frozen_at": "2026-08-02T09:00:00+08:00",
            "worker": "judge_c",
        },
        "report_path": "reports/comparison.json",
        "isolation": ISOLATION_CLAUSE,
    })
    return base / "manifest.json"


# ------------------------------------------------------------ 合法清单全绿


def test_valid_manifest_passes_every_check(tmp_path: Path):
    assert audit_comparison(_protocol(tmp_path)) == []


def test_load_manifest_rejects_missing_or_wrong_schema(tmp_path: Path):
    with pytest.raises(ValueError, match="不存在"):
        load_manifest(tmp_path / "absent.json")
    _write(tmp_path / "bad.json", {"schema": 2, "id": "x"})
    with pytest.raises(ValueError, match="schema"):
        load_manifest(tmp_path / "bad.json")
    _write(tmp_path / "list.json", [1, 2, 3])
    with pytest.raises(ValueError, match="schema"):
        audit_comparison(tmp_path / "list.json")


def test_manifest_structure_errors_are_reported(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _edit(manifest, lambda data: data.update({
        "id": "", "problem_ids": [], "isolation": "",
        "arms": [
            {"arm_id": "harness_a", "kind": "harness",
             "project_path": "arms/harness_a"},
            {"arm_id": "harness_a", "kind": "harness",
             "project_path": "arms/harness_a"},
            {"arm_id": "ghost", "kind": "vibes"},
            {"kind": "harness"},
        ],
    }))
    errors = audit_comparison(manifest)
    assert any("id" in error and "comparison manifest" in error for error in errors)
    assert any("problem_ids" in error for error in errors)
    assert any("isolation" in error for error in errors)
    assert any("arm_id 重复" in error for error in errors)
    assert any("kind" in error and "ghost" in error for error in errors)
    assert any("arms[3]" in error for error in errors)


# ------------------------------------------------------ (a) 各臂状态必须终结


def test_harness_arm_status_must_be_terminal(tmp_path: Path):
    manifest = _protocol(tmp_path)
    project = manifest.parent / "arms" / "harness_a" / "modeling-project.json"
    for status in ("delivered", "abandoned"):
        data = json.loads(project.read_text(encoding="utf-8"))
        data["status"] = status
        _write(project, data)
        assert not any(
            "项目状态为" in error for error in audit_comparison(manifest)
        )
    data = json.loads(project.read_text(encoding="utf-8"))
    data["status"] = "active"
    _write(project, data)
    errors = audit_comparison(manifest)
    assert any(
        "arm harness_a" in error and "项目状态为 active" in error
        for error in errors
    )


def test_missing_harness_project_path_is_an_error(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _edit(manifest, lambda data: data["arms"][0].update(
        {"project_path": "arms/absent"}
    ))
    errors = audit_comparison(manifest)
    assert any(
        "arm harness_a" in error and "项目路径不存在" in error
        for error in errors
    )

    _edit(manifest, lambda data: data["arms"][0].pop("project_path"))
    errors = audit_comparison(manifest)
    assert any(
        "arm harness_a" in error and "project_path" in error
        for error in errors
    )


def test_external_baseline_arm_is_not_status_checked(tmp_path: Path):
    manifest = _protocol(tmp_path)
    # 外部臂没有 modeling-project.json，也不该被 lifecycle 状态检查波及。
    assert not any(
        "external_b" in error and "项目状态" in error
        for error in audit_comparison(manifest)
    )


# --------------------------------------------------- (b) 判卷报告存在且完整


def test_report_must_exist_and_cover_all_arms(tmp_path: Path):
    manifest = _protocol(tmp_path)
    report = manifest.parent / "reports" / "comparison.json"
    report.unlink()
    errors = audit_comparison(manifest)
    assert any("comparison report missing" in error for error in errors)

    _write(report, {"schema": 1, "arms": [
        {"arm_id": "harness_a", "objective_value": 1.0, "verdict": "PASS"},
    ]})
    errors = audit_comparison(manifest)
    assert any(
        "comparison report incomplete" in error and "external_b" in error
        for error in errors
    )

    _write(report, {"schema": 1, "arms": {
        "harness_a": {"objective_value": 1.0},
        "external_b": {"verdict": "PASS"},
    }})
    errors = audit_comparison(manifest)
    assert any(
        "comparison report incomplete" in error
        and "harness_a" in error and "verdict" in error
        for error in errors
    )
    assert any(
        "comparison report incomplete" in error
        and "external_b" in error and "objective_value" in error
        for error in errors
    )

    # 报告体裁不对（没有 arms 字段）同样算 incomplete。
    _write(report, {"schema": 1, "winner": "harness_a"})
    assert any(
        "comparison report incomplete" in error
        for error in audit_comparison(manifest)
    )


def test_report_path_absent_from_manifest_is_missing(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _edit(manifest, lambda data: data.pop("report_path"))
    assert any(
        "comparison report missing" in error
        for error in audit_comparison(manifest)
    )


# ------------------------------------------------------------ (c) 基线独立性


def test_baseline_leak_detected_until_import_is_registered(tmp_path: Path):
    manifest = _protocol(tmp_path)
    base = manifest.parent
    leaked = base / "arms" / "harness_a" / "src" / "borrowed.py"
    _write(leaked, "VALUE = 2\n")
    errors = audit_comparison(manifest)
    assert any(
        "baseline leak" in error
        and "harness_a" in error
        and "src/borrowed.py" in error
        and "external_b_run/optimize.py" in error
        for error in errors
    )

    _write(base / "arms" / "harness_a" / "problem" / "import_manifest.json", {
        "schema": 1,
        "imports": [{
            "index": 0,
            "stored_as": "src/borrowed.py",
            "stored_sha256": sha256(leaked),
            "source_sha256": sha256(leaked),
            "method": "copy",
            "reason": "declared reuse",
        }],
    })
    assert audit_comparison(manifest) == []


def test_leak_scan_skipped_when_no_baseline_artifacts(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _write(
        manifest.parent / "arms" / "harness_a" / "src" / "borrowed.py",
        "VALUE = 2\n",
    )
    _edit(manifest, lambda data: data.update({"baseline_artifacts": []}))
    assert audit_comparison(manifest) == []


def test_malformed_baseline_artifacts_are_reported(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _edit(manifest, lambda data: data.update({
        "baseline_artifacts": [{"path": "optimize.py"}, "nope"],
    }))
    errors = audit_comparison(manifest)
    assert any("baseline_artifacts[0]" in error for error in errors)
    assert any("baseline_artifacts[1]" in error for error in errors)


# ------------------------------------------------- (d) judge 冻结早于各臂完成


def test_judge_freeze_must_precede_every_arm_completion(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _edit(manifest, lambda data: data["judge"].update(
        {"frozen_at": "2026-08-02T18:00:00+08:00"}
    ))
    errors = audit_comparison(manifest)
    assert any(
        "judge freeze too late" in error and "harness_a" in error
        for error in errors
    )


def test_missing_frozen_at_or_params_hash_is_unjudgeable(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _edit(manifest, lambda data: data.update({"judge": {
        "command": "checks/judge.py", "frozen_params_sha256": None,
        "frozen_at": None, "worker": None,
    }}))
    errors = audit_comparison(manifest)
    assert any(
        "judge freeze missing" in error and "frozen_at" in error
        for error in errors
    )
    assert any(
        "judge freeze missing" in error and "frozen_params_sha256" in error
        for error in errors
    )

    _edit(manifest, lambda data: data.pop("judge"))
    assert any(
        "judge freeze missing" in error
        for error in audit_comparison(manifest)
    )


def test_missing_status_history_downgrades_to_warning(tmp_path: Path):
    manifest = _protocol(tmp_path)
    project = manifest.parent / "arms" / "harness_a" / "modeling-project.json"
    data = json.loads(project.read_text(encoding="utf-8"))
    data.pop("status_history")
    _write(project, data)
    errors = audit_comparison(manifest)
    assert any(
        "judge freeze unverifiable" in error
        and "warning" in error
        and "harness_a" in error
        for error in errors
    )
    # 保守跳过：不因为缺 history 就反过来断言 judge 冻结及时。
    assert not any("judge freeze too late" in error for error in errors)


# ------------------------------------------------------ (e) judge 与生成者分离


def test_judge_worker_cannot_be_an_arm_producer(tmp_path: Path):
    manifest = _protocol(tmp_path)
    _edit(manifest, lambda data: data["judge"].update({"worker": "worker_a"}))
    errors = audit_comparison(manifest)
    assert any(
        "judge independence violated" in error
        and "worker_a" in error
        and "harness_a" in error
        for error in errors
    )


def test_arms_without_producer_skip_the_independence_check(tmp_path: Path):
    manifest = _protocol(tmp_path)

    def strip(data):
        for arm in data["arms"]:
            arm.pop("producer", None)
        data["judge"]["worker"] = "worker_a"

    _edit(manifest, strip)
    assert audit_comparison(manifest) == []


# ------------------------------------------------------------- 路径基准与 CLI


def test_protocol_root_resolves_against_repo_root(tmp_path: Path):
    nested = tmp_path / "repo" / "benchmarks" / "protocols" / "p.json"
    _write(nested, {"schema": 1})
    assert protocol_root(nested) == tmp_path / "repo"
    loose = tmp_path / "elsewhere" / "p.json"
    _write(loose, {"schema": 1})
    assert protocol_root(loose) == loose.parent


def test_cli_comparison_audit_exit_codes(tmp_path: Path, monkeypatch, capsys):
    manifest = _protocol(tmp_path)
    code = run_cli(monkeypatch, "comparison", "audit", "--manifest", str(manifest))
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and report["errors"] == []

    (manifest.parent / "reports" / "comparison.json").unlink()
    code = run_cli(monkeypatch, "comparison", "audit", "--manifest", str(manifest))
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert any("comparison report missing" in e for e in report["errors"])


def test_cli_reports_missing_manifest_as_error(tmp_path, monkeypatch, capsys):
    code = run_cli(
        monkeypatch, "comparison", "audit",
        "--manifest", str(tmp_path / "absent.json"),
    )
    assert code == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


# ------------------------------------------------------ 随仓库交付的真实协议


def test_shipped_template_is_a_valid_skeleton():
    manifest = load_manifest(TEMPLATE)
    assert manifest["schema"] == 1
    assert manifest["isolation"] == ISOLATION_CLAUSE
    assert {arm["kind"] for arm in manifest["arms"]} == {
        "harness", "external_baseline"
    }
    for key in ("command", "frozen_params_sha256", "frozen_at", "worker"):
        assert key in manifest["judge"]


def test_shipped_blind_comparison_manifest_records_the_open_verdict():
    manifest = load_manifest(SHIPPED)
    assert manifest["id"] == "2026-blind-codex-vs-harness"
    assert manifest["problem_ids"] == ["2023D", "2024C", "2025A"]
    harness_arms = [a for a in manifest["arms"] if a["kind"] == "harness"]
    external = [a for a in manifest["arms"] if a["kind"] == "external_baseline"]
    assert len(harness_arms) == 3 and len(external) == 1
    for arm in harness_arms:
        assert not Path(arm["project_path"]).is_absolute()
        assert (REPO / arm["project_path"]).is_dir()
    # 外部盲测臂在仓库外，按绝对路径如实登记。
    assert Path(external[0]["artifact_dir"]).is_absolute()
    # 判卷已于 2026-08-08 事后补做：报告存在、judge 已冻结，但冻结时刻必然晚于
    # 各臂完成（2026-07/08），因此本清单永远不满足预注册要求 —— 这是如实登记，
    # 不是缺陷。若日后重跑对照，必须新建清单并在开跑前冻结 judge。
    assert manifest["judge"]["frozen_at"] is not None
    assert manifest["judge"]["frozen_params_sha256"]
    assert (REPO / manifest["report_path"]).exists()
    # baseline_artifacts 逐条引用已有登记表，不重算哈希。
    registered = {
        entry["sha256"]
        for path in sorted(
            (REPO / "benchmarks" / "protocols" / "baselines").glob("*.json")
        )
        for entry in json.loads(path.read_text(encoding="utf-8"))["files"]
    }
    assert manifest["baseline_artifacts"]
    assert all(
        entry["sha256"] in registered
        for entry in manifest["baseline_artifacts"]
    )


def test_shipped_blind_comparison_audit_is_not_green():
    manifest = load_manifest(SHIPPED)
    errors = audit_comparison(SHIPPED)
    # 报告与 judge 冻结已于事后补齐，这两条历史欠账不再出现。
    assert not any("comparison report missing" in error for error in errors)
    assert not any("judge freeze missing" in error for error in errors)
    # 但事后冻结不具备预注册效力：judge 晚于各臂完成，审计必须继续拒绝放行。
    assert errors, "事后判卷不得让协议审计变绿"
    assert any(
        "judge freeze too late" in error or "judge freeze unverifiable" in error
        for error in errors
    )
    # (a) 在真实 run 上的判定必须与磁盘状态一致（状态被收口后本断言自动放行）。
    for arm in manifest["arms"]:
        if arm["kind"] != "harness":
            continue
        status = lifecycle.get_status(REPO / arm["project_path"])
        flagged = any(
            f"arm {arm['arm_id']}" in error and f"项目状态为 {status}" in error
            for error in errors
        )
        assert flagged is (status not in TERMINAL_STATUSES), arm["arm_id"]
