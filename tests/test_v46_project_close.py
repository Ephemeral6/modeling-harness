"""V4.6 project close: backfill completed on runs whose s6 gate already stamped."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from modelharness import cli_v31
from modelharness.lifecycle import (
    CLOSE_REASON, close, describe, get_status, resume, set_status,
)
from modelharness.scaffold import create
from modelharness.storage import atomic_write_json


def run_cli(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["modelharness", *argv])
    return cli_v31.main()


def stamp_s6(root: Path, stage: str = "s6") -> Path:
    path = root / ".harness" / "stamps" / "s6.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"schema": 3, "stage": stage, "at": "2026-08-07"})
    return path


def test_close_backfills_completed_when_s6_stamped(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    stamp_s6(root)
    report = close(root, "历史 run 收口")
    assert report["status"] == "completed"
    assert get_status(root) == "completed"
    assert report["history"][-1]["status"] == "completed"
    assert report["history"][-1]["reason"] == "历史 run 收口"


def test_close_default_reason_is_recorded(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    stamp_s6(root)
    close(root)
    assert describe(root)["history"][-1]["reason"] == CLOSE_REASON


def test_close_refuses_without_s6_stamp(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    with pytest.raises(ValueError, match="s6 印章不存在"):
        close(root, "没有印章也想收口")
    assert get_status(root) == "active"
    assert describe(root)["history"] == []


def test_close_refuses_stamp_of_another_stage(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    stamp_s6(root, stage="s5")
    with pytest.raises(ValueError, match="印章损坏或 stage 字段不符"):
        close(root, "阶段不符")
    assert get_status(root) == "active"


def test_close_closes_interrupted_run(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    stamp_s6(root)
    set_status(root, "interrupted", "harness restart")
    assert close(root, "补收口")["status"] == "completed"


@pytest.mark.parametrize("terminal", ["completed", "delivered", "abandoned"])
def test_close_refuses_terminal_statuses(tmp_path: Path, terminal: str):
    root = create(tmp_path / f"case-{terminal}", "demo")
    stamp_s6(root)
    set_status(root, terminal, f"force {terminal}")
    with pytest.raises(ValueError, match="拒绝收口：项目状态为"):
        close(root, "二次收口")
    assert get_status(root) == terminal


def test_closed_run_refuses_resume(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    stamp_s6(root)
    close(root, "历史 run 收口")
    with pytest.raises(ValueError, match="拒绝 resume"):
        resume(root)


def test_project_close_cli(tmp_path: Path, monkeypatch, capsys):
    root = create(tmp_path / "case", "demo")
    stamp_s6(root)
    code = run_cli(
        monkeypatch,
        "project", "close",
        "--project", str(root),
        "--reason", "s6 已盖章的历史 run",
    )
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "completed"
    assert report["history"][-1]["reason"] == "s6 已盖章的历史 run"
    assert get_status(root) == "completed"


def test_project_close_cli_refuses_unstamped_run(
    tmp_path: Path, monkeypatch, capsys
):
    root = create(tmp_path / "case", "demo")
    code = run_cli(
        monkeypatch, "project", "close", "--project", str(root)
    )
    assert code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "ValueError"
    assert "s6 印章不存在" in payload["message"]
    assert get_status(root) == "active"
