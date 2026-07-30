from pathlib import Path

from modelharness.evaluation import score_project
from modelharness.scaffold import create


def test_evaluation_reports_local_and_milestone_metrics(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    report = score_project(root)
    assert report["problem_graph"]["active_nodes"] == 7
    assert report["problem_graph"]["verified_nodes"] == 0
    assert report["problem_graph"]["closure_rate"] == 0
    assert report["milestones"]["depth"] == 0
    assert report["milestones"]["completion_rate"] == 0
    assert report["integrity"]["ok"]
