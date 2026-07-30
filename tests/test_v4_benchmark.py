from pathlib import Path

from modelharness.benchmarking import pass_all_k, score_benchmark
from modelharness.episode import capture_episode
from modelharness.scaffold import create
from modelharness.storage import read_json


def test_episode_package_captures_authoritative_trace(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    destination = capture_episode(
        root,
        tmp_path / "episode",
        benchmark_id="smoke",
        model="test-model",
        seed=7,
    )
    manifest = read_json(destination / "episode.json")
    assert manifest["benchmark_id"] == "smoke"
    assert manifest["model"] == "test-model"
    assert manifest["seed"] == 7
    assert (destination / "tasks.json").is_file()
    assert (destination / "events.json").is_file()
    assert (
        destination / "project" / ".harness" / "problem_graph.json"
    ).is_file()
    assert not (
        destination / "project" / ".harness" / "workflow.sqlite3"
    ).exists()


def test_hidden_rubric_and_pass_all_k(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    fixture = (
        Path(__file__).parents[1]
        / "benchmarks" / "fixtures" / "degenerate_recovery.json"
    )
    report = score_benchmark(root, fixture)
    assert report["passed"] is True
    runs = [
        {"passed": True},
        {"passed": True},
        {"passed": False},
    ]
    assert pass_all_k(runs, 1) == 2 / 3
    assert pass_all_k(runs, 2) == 1 / 3
    assert pass_all_k(runs, 3) == 0
