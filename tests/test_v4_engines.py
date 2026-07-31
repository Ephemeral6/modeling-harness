import json
from pathlib import Path

import pytest

from modelharness import engines
from modelharness.intake import intake
from modelharness.scaffold import create


def test_resolve_explicit_and_invalid():
    assert engines.resolve("codex") == "codex"
    assert engines.resolve("claude-code") == "claude-code"
    with pytest.raises(ValueError):
        engines.resolve("gpt-terminal")


def test_resolve_auto_never_fails(monkeypatch):
    monkeypatch.setattr(engines.shutil, "which", lambda _name: None)
    assert engines.resolve("auto") == "codex"


def test_detect_reports_all_engines():
    report = engines.detect()
    assert set(report) == {"codex", "claude-code"}
    for item in report.values():
        assert {"display", "available", "cli", "entry_files"} <= set(item)


def test_headless_command_none_when_missing(monkeypatch):
    monkeypatch.setattr(engines.shutil, "which", lambda _name: None)
    assert engines.headless_command("claude-code") is None


def test_scaffold_records_engine_and_entry_files(tmp_path: Path):
    project = create(tmp_path / "proj", "引擎测试", engine="claude-code")
    meta = json.loads(
        (project / "modeling-project.json").read_text(encoding="utf-8")
    )
    assert meta["engine"] == "claude-code"
    assert (project / "AGENTS.md").is_file()
    assert (project / "CLAUDE.md").is_file()
    assert (project / "docs" / "notebook.md").is_file()


def test_intake_without_files_materializes_pasted_text(tmp_path: Path):
    prompt = "预测未来一小时共享单车需求并给出调度方案。"
    result = intake(tmp_path, "纯文本题面", prompt, [])
    project = Path(result["project"])
    raw = project / "problem" / "data_raw" / "001__pasted_statement.md"
    assert raw.read_text(encoding="utf-8").strip() == prompt
    statement = (project / "problem" / "statement.md").read_text(
        encoding="utf-8"
    )
    assert prompt in statement
    manifest = json.loads(
        (project / "problem" / "intake_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    entry = manifest["files"][0]
    assert entry["origin"] == "conversation_paste"
    assert entry["sha256"]
