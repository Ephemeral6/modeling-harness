from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from modelharness import cli_v31, stages
from modelharness.episode import capture_episode
from modelharness.lifecycle import describe, get_status, set_status
from modelharness.scaffold import create
from modelharness.storage import atomic_write_json, read_json


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def test_new_project_defaults_to_active(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    assert get_status(root) == "active"
    meta = read_json(root / "modeling-project.json")
    meta.pop("status")
    atomic_write_json(root / "modeling-project.json", meta)
    assert get_status(root) == "active"


def test_set_status_flips_and_appends_history(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    before = read_json(root / "modeling-project.json")
    set_status(root, "interrupted", "harness restart")
    set_status(root, "abandoned", "superseded by rerun")
    meta = read_json(root / "modeling-project.json")
    assert meta["status"] == "abandoned"
    history = meta["status_history"]
    assert [item["status"] for item in history] == [
        "interrupted", "abandoned",
    ]
    assert history[1]["reason"] == "superseded by rerun"
    for item in history:
        assert datetime.fromisoformat(item["at"]).tzinfo is not None
    for field in ("schema", "title", "created_at", "harness", "engine"):
        assert meta[field] == before[field]


def test_set_status_rejects_bad_input(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    with pytest.raises(ValueError):
        set_status(root, "paused", "not a valid status")
    with pytest.raises(ValueError):
        set_status(root, "abandoned", "  ")
    assert get_status(root) == "active"


def test_project_abandon_cli(tmp_path: Path, monkeypatch, capsys):
    root = create(tmp_path / "case", "demo")
    code = run_cli(
        monkeypatch,
        "project", "abandon",
        "--project", str(root),
        "--reason", "superseded by rerun",
    )
    assert code == 0
    assert get_status(root) == "abandoned"
    capsys.readouterr()
    code = run_cli(
        monkeypatch, "project", "status", "--project", str(root)
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "abandoned"
    assert report["history"][-1]["reason"] == "superseded by rerun"


def test_capture_episode_gated_by_status(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    with pytest.raises(ValueError):
        capture_episode(root, tmp_path / "ep-active")
    set_status(root, "abandoned", "superseded by rerun")
    destination = capture_episode(root, tmp_path / "ep-abandoned")
    manifest = read_json(destination / "episode.json")
    assert manifest["status"] == "abandoned"


def test_capture_episode_allow_partial(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    destination = capture_episode(
        root, tmp_path / "ep-partial", allow_partial=True
    )
    manifest = read_json(destination / "episode.json")
    assert manifest["status"] == "active"


def test_s6_gate_success_marks_completed(tmp_path: Path, monkeypatch):
    root = create(tmp_path / "case", "demo")
    # 摘除问题图以绕过 facade 的工具链审计，专注生命周期钩子接线。
    (root / ".harness" / "problem_graph.json").unlink()
    monkeypatch.setattr(
        stages._StageService, "gate", lambda self, stage: {"stage": stage}
    )
    service = stages.StageService(root)
    service.gate("s0")
    assert get_status(root) == "active"
    record = service.gate("s6")
    assert record == {"stage": "s6"}
    assert get_status(root) == "completed"
    history = describe(root)["history"]
    assert history[-1] == {
        "status": "completed",
        "at": history[-1]["at"],
        "reason": "s6 gate passed",
    }


def test_s6_gate_failure_keeps_status(tmp_path: Path, monkeypatch):
    root = create(tmp_path / "case", "demo")
    (root / ".harness" / "problem_graph.json").unlink()

    def failing_gate(self, stage):
        raise RuntimeError("缺少 verified 证据")

    monkeypatch.setattr(stages._StageService, "gate", failing_gate)
    with pytest.raises(RuntimeError):
        stages.StageService(root).gate("s6")
    assert get_status(root) == "active"
