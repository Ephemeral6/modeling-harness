from pathlib import Path

import pytest

import modelharness.intake as intake_module


def test_failed_intake_removes_staging_and_does_not_move_pointer(
    tmp_path: Path, monkeypatch
):
    harness = tmp_path / "harness"
    harness.mkdir()
    source = tmp_path / "statement.txt"
    source.write_text("problem", encoding="utf-8")

    def fail_copy(*args, **kwargs):
        raise OSError("injected copy failure")

    monkeypatch.setattr(intake_module.shutil, "copy2", fail_copy)
    with pytest.raises(OSError):
        intake_module.intake(harness, "failure", "start", [source])

    projects = harness / "projects"
    assert not (projects / ".current.json").exists()
    assert not list(projects.glob(".intake-*"))
    assert not [p for p in projects.iterdir() if not p.name.startswith(".")]
