from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from modelharness import cli_v31
from modelharness.provenance_core import audit_provenance, register_import
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


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    _write(root / "modeling-project.json", {"schema": 4, "title": "prov"})
    return root


def _import_pair(tmp_path: Path, root: Path, content: str) -> Path:
    """A source file outside the project plus its byte-identical copy inside."""
    source = tmp_path / "external" / "origin.py"
    _write(source, content)
    _write(root / "src" / "helper.py", content)
    return source


# ---------------------------------------------------------------- register


def test_register_appends_record_and_is_idempotent(tmp_path: Path):
    root = _project(tmp_path)
    source = _import_pair(tmp_path, root, "VALUE = 1\n")
    first = register_import(
        root, source, "src/helper.py", "copy", "reuse prior helper"
    )
    assert first["ok"] is True and first["skipped"] is False
    record = first["record"]
    assert record["index"] == 0
    assert record["stored_as"] == "src/helper.py"
    assert record["method"] == "copy"
    assert record["reason"] == "reuse prior helper"
    assert record["source_sha256"] == sha256(source)
    assert record["stored_sha256"] == sha256(root / "src" / "helper.py")
    assert datetime.fromisoformat(record["registered_at"]).tzinfo is not None

    second = register_import(
        root, source, "src/helper.py", "copy", "reuse prior helper"
    )
    assert second["skipped"] is True
    manifest = read_json(root / "problem" / "import_manifest.json")
    assert manifest["schema"] == 1
    assert len(manifest["imports"]) == 1

    # 内容漂移后重新登记：append-only，新记录追加而不覆盖旧记录。
    (root / "src" / "helper.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "external" / "origin.py").write_text(
        "VALUE = 2\n", encoding="utf-8"
    )
    third = register_import(
        root, source, "src/helper.py", "copy", "re-register after edit"
    )
    assert third["skipped"] is False and third["record"]["index"] == 1
    manifest = read_json(root / "problem" / "import_manifest.json")
    assert len(manifest["imports"]) == 2


def test_register_rejects_bad_inputs(tmp_path: Path):
    root = _project(tmp_path)
    source = _import_pair(tmp_path, root, "VALUE = 1\n")
    with pytest.raises(ValueError, match="target"):
        register_import(root, source, "src/missing.py", "copy", "r")
    with pytest.raises(ValueError, match="method"):
        register_import(root, source, "src/helper.py", "clone", "r")
    with pytest.raises(ValueError, match="source"):
        register_import(
            root, tmp_path / "external" / "absent.py",
            "src/helper.py", "copy", "r",
        )
    with pytest.raises(ValueError, match="reason"):
        register_import(root, source, "src/helper.py", "copy", "  ")
    with pytest.raises(ValueError, match="target"):
        register_import(root, source, "../outside.py", "copy", "r")


# ---------------------------------------------------------- audit (a) 完整性


def test_audit_flags_manifest_drift_until_reregistered(tmp_path: Path):
    root = _project(tmp_path)
    source = _import_pair(tmp_path, root, "VALUE = 1\n")
    register_import(root, source, "src/helper.py", "copy", "reuse")
    assert audit_provenance(root) == []

    (root / "src" / "helper.py").write_text("VALUE = 99\n", encoding="utf-8")
    errors = audit_provenance(root)
    assert any(
        "src/helper.py" in error and "重新登记" in error for error in errors
    )

    register_import(root, source, "src/helper.py", "copy", "edited then re-registered")
    assert audit_provenance(root) == []

    (root / "src" / "helper.py").unlink()
    errors = audit_provenance(root)
    assert any(
        "src/helper.py" in error and "文件缺失" in error for error in errors
    )


# ------------------------------------------------------ audit (b) 双层 warm start


def test_audit_warm_start_requires_policy_and_manifest(tmp_path: Path):
    root = _project(tmp_path)
    seed = "seed-artifact-from-prior-run\n"
    _write(tmp_path / "external" / "seed.json", seed)
    _write(root / "data" / "warm_seed.json", seed)
    declared = sha256(root / "data" / "warm_seed.json")
    _write(root / "results" / "q5_search.json", {"warm_start_sha256": declared})

    # 第一层缺失：没有 search_policy 声明。
    errors = audit_provenance(root)
    assert any("search_policy" in error for error in errors)
    assert any("import_manifest" in error for error in errors)

    # 补上第一层声明后，仍缺第二层 import_manifest 登记。
    _write(root / "config" / "search_policy.json", {
        "schema": 1,
        "warm_start": {"allowed": True, "source_sha256": [declared]},
    })
    errors = audit_provenance(root)
    assert errors and all("import_manifest" in error for error in errors)

    # 登记后两层齐备，审计归零。
    register_import(
        root, tmp_path / "external" / "seed.json",
        "data/warm_seed.json", "warm_start", "declared warm start seed",
    )
    assert audit_provenance(root) == []


# ------------------------------------------------- audit (c) 基线逐字节扫描


def test_audit_scans_only_code_dirs_against_baselines(tmp_path: Path):
    root = _project(tmp_path)
    content = "def reused():\n    return 42\n"
    _write(root / "src" / "copied.py", content)
    _write(root / "support" / "copied_too.py", content)
    _write(tmp_path / "prior" / "reused.py", content)
    baselines = tmp_path / "baselines"
    _write(baselines / "prior.json", {
        "schema": 1,
        "baseline_id": "prior_run",
        "files": [{
            "path": "reused.py",
            "sha256": sha256(tmp_path / "prior" / "reused.py"),
        }],
    })
    errors = audit_provenance(root, baselines)
    assert errors == [
        "unregistered cross-run copy: src/copied.py matches prior_run/reused.py"
    ]

    # 基线目录不存在时跳过该项检查，而不是报错。
    assert audit_provenance(root, tmp_path / "no_baselines") == []

    register_import(
        root, tmp_path / "prior" / "reused.py",
        "src/copied.py", "copy", "registered reuse",
    )
    assert audit_provenance(root, baselines) == []


# --------------------------------------------- audit (d) checker 独立性判定


def _independence_project(tmp_path: Path) -> tuple[Path, Path]:
    root = _project(tmp_path)
    solver = "def solve():\n    return [1, 2, 3]\n"
    _write(root / "src" / "solver.py", solver)
    _write(root / "checks" / "checker.py", solver)
    origin = tmp_path / "external" / "blind_solver.py"
    _write(origin, solver)
    _write(root / "results" / "feasibility_audit.json", {
        "schema": 1,
        "candidate": {"artifact": "results/candidate.json", "producer_id": "w1"},
        "solver": {"artifact": "src/solver.py", "identity": "w1"},
        "checker": {
            "artifact": "checks/checker.py",
            "identity": "w2",
            "implementation_reuse": False,
        },
        "execution_status": "completed",
        "verdict": "PASS",
    })
    return root, origin


def test_audit_flags_checker_copied_from_checked_source(tmp_path: Path):
    root, origin = _independence_project(tmp_path)
    register_import(
        root, origin, "checks/checker.py", "copy", "checker taken from solver origin"
    )
    errors = audit_provenance(root)
    assert any(
        "checker provenance violates independence" in error
        and "checks/checker.py" in error
        for error in errors
    )

    # 最新记录改为 method=reference（引用而非拷贝）后不再判违规。
    other = tmp_path / "external" / "reference_note.py"
    _write(other, "def solve():\n    return [1, 2, 3]\n")
    register_import(
        root, other, "checks/checker.py", "reference", "checker consulted as reference"
    )
    errors = audit_provenance(root)
    assert not any(
        "checker provenance violates independence" in error for error in errors
    )


def test_audit_ignores_checker_not_claiming_independence(tmp_path: Path):
    root, origin = _independence_project(tmp_path)
    audit_file = root / "results" / "feasibility_audit.json"
    data = read_json(audit_file)
    data["checker"]["implementation_reuse"] = True
    _write(audit_file, data)
    register_import(
        root, origin, "checks/checker.py", "copy", "checker taken from solver origin"
    )
    errors = audit_provenance(root)
    assert not any(
        "checker provenance violates independence" in error for error in errors
    )


# ------------------------------------------------------- 旧项目零破坏与 CLI


def test_legacy_project_without_manifest_is_clean(tmp_path: Path, monkeypatch, capsys):
    root = _project(tmp_path)
    _write(root / "src" / "solve.py", "print('legacy solver')\n")
    _write(root / "results" / "summary.json", {"objective": 12.5})
    assert audit_provenance(root) == []
    code = run_cli(monkeypatch, "provenance", "audit", "--project", str(root))
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {"ok": True, "errors": []}


def test_cli_register_then_audit_roundtrip(tmp_path: Path, monkeypatch, capsys):
    root = _project(tmp_path)
    source = _import_pair(tmp_path, root, "VALUE = 7\n")
    code = run_cli(
        monkeypatch,
        "provenance", "register",
        "--project", str(root),
        "--source", str(source),
        "--target", "src/helper.py",
        "--method", "copy",
        "--reason", "harness run reused a prior artifact",
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and report["skipped"] is False
    assert report["record"]["stored_as"] == "src/helper.py"

    code = run_cli(
        monkeypatch,
        "provenance", "register",
        "--project", str(root),
        "--source", str(source),
        "--target", "src/helper.py",
        "--method", "copy",
        "--reason", "harness run reused a prior artifact",
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["skipped"] is True

    code = run_cli(monkeypatch, "provenance", "audit", "--project", str(root))
    assert code == 0
    capsys.readouterr()

    (root / "src" / "helper.py").write_text("VALUE = 8\n", encoding="utf-8")
    code = run_cli(monkeypatch, "provenance", "audit", "--project", str(root))
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["errors"]


def test_cli_register_missing_target_fails(tmp_path: Path, monkeypatch, capsys):
    root = _project(tmp_path)
    source = tmp_path / "external" / "origin.py"
    _write(source, "VALUE = 1\n")
    code = run_cli(
        monkeypatch,
        "provenance", "register",
        "--project", str(root),
        "--source", str(source),
        "--target", "src/absent.py",
        "--method", "copy",
        "--reason", "r",
    )
    assert code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False


def test_stage_template_wires_provenance_audit_into_s6():
    template = (
        Path(cli_v31.__file__).resolve().parents[1]
        / "templates" / "config" / "stages.json"
    )
    spec = json.loads(template.read_text(encoding="utf-8"))
    assert ["python", "-m", "modelharness", "provenance", "audit"] in (
        spec["s6"]["checks"]
    )
