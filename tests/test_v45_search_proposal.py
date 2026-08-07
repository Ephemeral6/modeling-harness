"""4.5 机制 4：gap 超阈自动物化“提升求解质量”边界机会节点。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from modelharness.opportunities import (
    GAP_ALERT_FRACTION,
    SEARCH_IMPROVEMENT_NODE_PREFIX,
    SEARCH_IMPROVEMENT_PROPOSAL_PATH,
    audit_discharge,
    build_ledger,
    build_search_improvement_proposal,
    write_proposal,
)
from modelharness.problem_graph import validate_problem_graph


FIXTURES = (
    Path(__file__).resolve().parents[1]
    / "benchmarks"
    / "fixtures"
    / "regression"
)


def _project(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(FIXTURES / name, target)
    return target


def _improvement_nodes(proposal: dict) -> dict:
    return {
        node_id: node
        for node_id, node in proposal["nodes"].items()
        if node_id.startswith(SEARCH_IMPROVEMENT_NODE_PREFIX)
    }


def test_proposal_passes_problem_graph_validation(tmp_path):
    root = _project(tmp_path, "search_gap_headroom")

    proposal = build_search_improvement_proposal(root)

    assert validate_problem_graph(proposal) is proposal
    nodes = _improvement_nodes(proposal)
    node_id = SEARCH_IMPROVEMENT_NODE_PREFIX + "Q2.route_mip"
    assert list(nodes) == [node_id]
    node = nodes[node_id]
    assert node["milestone"] == "s3"
    assert node["depends_on"] == ["s3.solver_validation"]
    acceptance = [
        item for item in node["acceptance"]
        if isinstance(item, dict) and item.get("kind") == "search_quality"
    ]
    assert acceptance, "提案节点缺少 search_quality 验收"
    assert acceptance[0]["search_id"] == "Q2.route_mip"
    assert acceptance[0]["max_gap_fraction"] == GAP_ALERT_FRACTION
    assert acceptance[0]["when"] == "optimization_relevant"
    headroom_ids = sorted(
        item["id"]
        for item in build_ledger(root)
        if item["kind"] == "search_gap_headroom"
    )
    assert node["opportunity_ids"] == headroom_ids


def test_proposal_idempotent_per_opportunity(tmp_path):
    root = _project(tmp_path, "search_gap_headroom")

    first = build_search_improvement_proposal(root)
    second = build_search_improvement_proposal(root)
    assert first == second

    # 提案被采纳（成为项目问题图）后再生成：不得重复节点或漂移。
    graph_path = root / ".harness" / "problem_graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    graph_path.write_text(
        json.dumps(first, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    applied = build_search_improvement_proposal(root)
    assert applied == first
    assert len(_improvement_nodes(applied)) == 1


def test_starvation_fixture_also_yields_headroom_and_single_node(tmp_path):
    root = _project(tmp_path, "search_starvation")

    ledger = build_ledger(root)
    by_kind = {
        kind: [item for item in ledger if item["kind"] == kind]
        for kind in ("search_starvation", "search_gap_headroom")
    }
    assert [x["search_id"] for x in by_kind["search_starvation"]] == [
        "Q1.open_grain_mip"
    ]
    assert [x["search_id"] for x in by_kind["search_gap_headroom"]] == [
        "Q1.open_grain_mip"
    ]
    starvation_id = by_kind["search_starvation"][0]["id"]
    headroom_id = by_kind["search_gap_headroom"][0]["id"]
    assert starvation_id != headroom_id, "同一 search 两类机会的 id 必须可区分"

    proposal = build_search_improvement_proposal(root)
    assert validate_problem_graph(proposal) is proposal
    nodes = _improvement_nodes(proposal)
    node_id = SEARCH_IMPROVEMENT_NODE_PREFIX + "Q1.open_grain_mip"
    assert list(nodes) == [node_id]
    assert nodes[node_id]["opportunity_ids"] == sorted(
        [starvation_id, headroom_id]
    )


def test_discharged_opportunities_do_not_rematerialize(tmp_path):
    root = _project(tmp_path, "search_gap_headroom")
    targets = [
        item["id"]
        for item in build_ledger(root)
        if item["kind"] in {"search_gap_headroom", "search_starvation"}
    ]
    assert targets
    outcomes_path = root / "results" / "opportunity_outcomes.json"
    outcomes_path.write_text(
        json.dumps({
            "schema": 1,
            "outcomes": [
                {
                    "opportunity_id": value,
                    "action": "budget_qualified_stop",
                    "reason_code": "budget_exhausted",
                }
                for value in targets
            ],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    proposal = build_search_improvement_proposal(root)

    assert _improvement_nodes(proposal) == {}


def test_headroom_kind_enters_discharge_audit(tmp_path):
    root = _project(tmp_path, "search_gap_headroom")
    headroom_id = next(
        item["id"]
        for item in build_ledger(root)
        if item["kind"] == "search_gap_headroom"
    )

    errors = audit_discharge(root)

    assert f"Opportunity 未处置: {headroom_id}" in errors


def test_write_proposal_lands_on_agreed_path(tmp_path):
    root = _project(tmp_path, "search_gap_headroom")

    path = write_proposal(root)

    assert path == root / SEARCH_IMPROVEMENT_PROPOSAL_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    assert validate_problem_graph(data)
    assert _improvement_nodes(data)
