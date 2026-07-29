from pathlib import Path

from modelharness.conversation import run


def test_conversation_intake(tmp_path: Path):
    harness = tmp_path / "harness"
    harness.mkdir()
    attachment = tmp_path / "题面.txt"
    attachment.write_text("求最优调度方案", encoding="utf-8")
    data = tmp_path / "data.csv"
    data.write_text("x,y\n1,2\n", encoding="utf-8")

    result = run(
        "真实题目测试", "完整解决，全程不使用 Claude",
        [str(attachment), str(data)], harness,
    )
    project = Path(result["project"])
    assert result["files_received"] == 2
    assert (project / "problem" / "data_raw" / "题面.txt").is_file()
    assert (project / "problem" / "data_raw" / "data.csv").is_file()
    assert "求最优调度方案" in (
        project / "problem" / "statement.md"
    ).read_text(encoding="utf-8")
    assert (harness / "projects" / ".current.json").is_file()
    assert not (harness / ".modelharness-current.json").exists()
