import json
from pathlib import Path

from modelharness.autopilot import next_packet
from modelharness.evidence import EvidenceGraph
from modelharness.scaffold import create
from modelharness.util import write_json


def test_autopilot_advances_and_requests_parallel_work(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    packet = next_packet(root)
    assert packet["stage"] == "s0"
    assert packet["phase"] == "build_evidence"
    assert len(packet["parallel_agent_plan"]) >= 2
    write_json(root / ".harness" / "stamps" / "s0.json", {"stage": "s0"})
    packet = next_packet(root)
    assert packet["stage"] == "s1"
    assert packet["continue"] is True


def test_autopilot_detects_repair_loop(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    graph = EvidenceGraph(root)
    for node_id, artifact in [
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ]:
        graph.add(node_id, "problem", node_id, artifact)
        graph.verify(node_id)
    # S0 has no reviewer and is ready for its gate.
    assert next_packet(root)["phase"] == "gate"
    write_json(root / ".harness" / "stamps" / "s0.json", {"stage": "s0"})
    for node_id, artifact in [
        ("model.spec", "docs/model_spec.md"),
        ("model.assumptions", "docs/assumptions.md"),
    ]:
        graph.add(node_id, "model", node_id, artifact)
        graph.verify(node_id)
    write_json(root / "reviews" / "s1_referee.json", {"verdict": "REJECT"})
    write_json(root / "reviews" / "s1_redteam.json", {"verdict": "APPROVE"})
    packet = next_packet(root)
    assert packet["phase"] == "repair_loop"
