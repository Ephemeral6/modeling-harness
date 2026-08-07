"""Derived improvement opportunities and discharge accounting."""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from .problem_graph import canonical_hash, validate_problem_graph
from .storage import atomic_write_json, read_json


ACTIONS = {
    "expanded_search",
    "dominance_proof",
    "budget_qualified_stop",
    "strength_downgrade",
    "deferred",
}
REASON_CODES = {
    "budget_exhausted",
    "dominated_by_bound",
    "out_of_scope_by_user",
    "problem_forced",
    "resolved_interior",
}

# Search-starvation thresholds (P0 keeps them as module constants; no config
# file).  See detect_search_starvation for how they combine.
STARVATION_MIN_NODES = 64
STARVATION_LAST_INCUMBENT_FRACTION = 0.5

# 4.5 mechanism 4: budgeted searches finishing above this optimality-gap
# fraction leave enough headroom to open a "raise search quality" node.
GAP_ALERT_FRACTION = 0.05
SEARCH_IMPROVEMENT_KINDS = {"search_gap_headroom", "search_starvation"}
SEARCH_IMPROVEMENT_NODE_PREFIX = "s3x.search_improvement."
SEARCH_IMPROVEMENT_PARENT = "s3.solver_validation"
SEARCH_IMPROVEMENT_PROPOSAL_PATH = (
    Path(".harness") / "search_improvement_proposal.json"
)


def _searches(diag: dict) -> list[tuple[str, dict]]:
    searches = diag.get("searches", {}) if isinstance(diag, dict) else {}
    return [
        (str(search_id), search)
        for search_id, search in searches.items()
        if isinstance(search, dict)
    ]


def _opportunity(kind: str, severity: str, **fields: Any) -> dict:
    payload = {"kind": kind, "severity": severity, **fields}
    payload["id"] = "opp-" + canonical_hash(payload)[:16]
    return payload


def detect_boundary_optima(diag: dict) -> list[dict]:
    hits: list[tuple[str, str, str]] = []
    for search_id, search in _searches(diag):
        for dimension, raw in search.get("dimensions", {}).items():
            if (
                not isinstance(raw, dict)
                or raw.get("source") != "artificial_search_bound"
            ):
                continue
            domain = raw.get("domain", [])
            selected = raw.get("selected")
            if not isinstance(domain, list) or not domain:
                continue
            if selected == min(domain) or selected == max(domain):
                boundary = "lower" if selected == min(domain) else "upper"
                hits.append((search_id, str(dimension), boundary))
    severity = "high" if len(hits) >= 2 else "medium"
    opportunities = [
        _opportunity(
            "artificial_boundary_optimum",
            severity,
            search_id=search_id,
            dimension=dimension,
            boundary=boundary,
        )
        for search_id, dimension, boundary in hits
    ]
    for search_id, search in _searches(diag):
        for dimension, raw in search.get("dimensions", {}).items():
            if (
                not isinstance(raw, dict)
                or raw.get("source") != "artificial_search_bound"
            ):
                continue
            domain = sorted(set(raw.get("domain", [])))
            selected = raw.get("selected")
            if len(domain) >= 3 and selected in {domain[1], domain[-2]}:
                opportunities.append(_opportunity(
                    "near_boundary_optimum",
                    "low",
                    search_id=search_id,
                    dimension=str(dimension),
                    selected=selected,
                ))
    return opportunities


def _available_count(value: Any) -> int | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        low, high = value
        return max(0, high - low + 1)
    if isinstance(value, list):
        return len(value)
    return None


def detect_grid_under_resolution(diag: dict) -> list[dict]:
    opportunities = []
    for search_id, search in _searches(diag):
        for dimension, raw in search.get("dimensions", {}).items():
            if not isinstance(raw, dict):
                continue
            domain = raw.get("domain", [])
            available = _available_count(raw.get("domain_available"))
            if (
                isinstance(domain, list)
                and len(domain) <= 3
                and available is not None
                and available > 2 * len(domain)
            ):
                opportunities.append(_opportunity(
                    "under_resolved_grid",
                    "medium",
                    search_id=search_id,
                    dimension=str(dimension),
                    tested=len(domain),
                    available=available,
                ))
    return opportunities


def detect_optimality_gap(evidence: dict, diag: dict) -> list[dict]:
    del evidence
    opportunities = []
    for search_id, search in _searches(diag):
        raw = search.get("optimality_gap", search.get("gap"))
        gap = raw.get("value") if isinstance(raw, dict) else raw
        if isinstance(gap, (int, float)) and not isinstance(gap, bool) and gap > 0:
            opportunities.append(_opportunity(
                "optimality_gap",
                "medium",
                search_id=search_id,
                gap=float(gap),
            ))
    return opportunities


def detect_search_starvation(diag: dict) -> list[dict]:
    """Flag time-limited searches that stopped hungry instead of converged.

    A search counts as starved when its optional ``budget`` block reports
    ``termination == "time_limit"`` with a positive ``final_gap_fraction``
    while either fewer than ``STARVATION_MIN_NODES`` nodes were explored or
    the last incumbent update landed before
    ``STARVATION_LAST_INCUMBENT_FRACTION * time_limit_seconds``.  Entries
    without a ``budget`` block are skipped so legacy projects never
    false-positive.
    """

    def number(value: Any) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return None

    def last_incumbent_seconds(updates: Any) -> float | None:
        if not isinstance(updates, list):
            return None
        times = [
            parsed
            for update in updates
            for parsed in [number(
                update.get("time_seconds")
                if isinstance(update, dict) else update
            )]
            if parsed is not None
        ]
        return max(times) if times else None

    opportunities = []
    for search_id, search in _searches(diag):
        budget = search.get("budget")
        if not isinstance(budget, dict):
            continue
        gap = number(budget.get("final_gap_fraction"))
        if budget.get("termination") != "time_limit":
            continue
        if gap is None or gap <= 0:
            continue
        nodes = number(budget.get("nodes_explored"))
        starved_nodes = nodes is not None and nodes < STARVATION_MIN_NODES
        time_limit = number(budget.get("time_limit_seconds"))
        last_update = last_incumbent_seconds(budget.get("incumbent_updates"))
        stale_incumbent = (
            time_limit is not None
            and time_limit > 0
            and last_update is not None
            and last_update < STARVATION_LAST_INCUMBENT_FRACTION * time_limit
        )
        if not (starved_nodes or stale_incumbent):
            continue
        opportunities.append(_opportunity(
            "search_starvation",
            "high",
            search_id=search_id,
            nodes_explored=budget.get("nodes_explored"),
            gap=gap,
            summary=(
                f"search {search_id} 在 time_limit 终止仍留 gap={gap:.4g}，"
                f"nodes_explored={budget.get('nodes_explored')}，疑似搜索饥饿"
            ),
        ))
    return opportunities


def detect_gap_headroom(diag: dict) -> list[dict]:
    """Flag budgeted searches that finished with material optimality headroom.

    A search earns a ``search_gap_headroom`` opportunity when its optional
    ``budget`` block reports ``final_gap_fraction`` above
    ``GAP_ALERT_FRACTION``, or when the same search already counts as starved
    per :func:`detect_search_starvation` (a starved search keeps headroom even
    below the alert threshold).  Both kinds may coexist for one search: the
    payloads differ at least by ``kind``, so the derived ids stay distinct.
    Entries without a ``budget`` block are skipped so legacy projects never
    false-positive.
    """
    starved_ids = {
        item.get("search_id") for item in detect_search_starvation(diag)
    }
    opportunities = []
    for search_id, search in _searches(diag):
        budget = search.get("budget")
        if not isinstance(budget, dict):
            continue
        raw = budget.get("final_gap_fraction")
        gap = (
            float(raw)
            if isinstance(raw, (int, float)) and not isinstance(raw, bool)
            else None
        )
        above = gap is not None and gap > GAP_ALERT_FRACTION
        starved = search_id in starved_ids
        if not (above or starved):
            continue
        trigger = "gap_above_threshold" if above else "starved_search"
        gap_text = f"{gap:.4g}" if gap is not None else "unknown"
        opportunities.append(_opportunity(
            "search_gap_headroom",
            "high",
            search_id=search_id,
            gap=gap,
            threshold=GAP_ALERT_FRACTION,
            trigger=trigger,
            summary=(
                f"search {search_id} 收工仍留 gap={gap_text}"
                f"（阈值 {GAP_ALERT_FRACTION:g}，触发={trigger}），"
                "存在提升求解质量的余量"
            ),
        ))
    return opportunities


def detect_rank_flip(diag: dict) -> list[dict]:
    opportunities = []
    for search_id, search in _searches(diag):
        baseline = search.get("baseline_ranking", [])
        if not isinstance(baseline, list) or len(baseline) < 2:
            continue
        expected = baseline[:2]
        for index, ranking in enumerate(search.get("stress_rankings", [])):
            values = (
                ranking.get("ranking", [])
                if isinstance(ranking, dict) else ranking
            )
            if (
                isinstance(values, list)
                and all(candidate in values for candidate in expected)
                and values.index(expected[1]) < values.index(expected[0])
            ):
                opportunities.append(_opportunity(
                    "robust_reoptimization",
                    "high",
                    search_id=search_id,
                    stress_index=index,
                    trigger="rank_flip",
                ))
    return opportunities


def detect_infeasibility(diag: dict) -> list[dict]:
    """Find parameter-free hard-constraint failures under stress scenarios."""
    opportunities = []
    for search_id, search in _searches(diag):
        scenarios = search.get(
            "stress_scenarios", search.get("stress_results", [])
        )
        if not isinstance(scenarios, list):
            continue
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict):
                continue
            constraints = scenario.get("hard_constraints", {})
            constraint_failed = (
                isinstance(constraints, dict)
                and any(
                    value is False
                    or (
                        isinstance(value, dict)
                        and value.get("satisfied") is False
                    )
                    for value in constraints.values()
                )
            )
            violations = scenario.get("hard_constraint_violations", [])
            failed = (
                scenario.get("feasible") is False
                or scenario.get("infeasible") is True
                or constraint_failed
                or bool(violations)
            )
            if failed:
                opportunities.append(_opportunity(
                    "stress_infeasibility",
                    "high",
                    search_id=search_id,
                    stress_index=index,
                    scenario_id=scenario.get("id"),
                    trigger="infeasibility",
                ))
    return opportunities


def detect_unmaterialized_branches(assumptions: dict) -> list[dict]:
    """Create high-severity opportunities for load-bearing unexplored branches."""
    values = (
        assumptions.get("assumptions", {})
        if isinstance(assumptions, dict)
        and isinstance(assumptions.get("assumptions"), dict)
        else assumptions
    )
    if not isinstance(values, dict):
        return []
    opportunities = []
    for assumption_id, assumption in values.items():
        if not isinstance(assumption, dict):
            continue
        branches = assumption.get("alternative_branches", [])
        unmaterialized = [
            branch.get("id")
            for branch in branches
            if isinstance(branch, dict)
            and (
                branch.get("materialized") is not True
                or not branch.get("results")
            )
        ]
        if (
            assumption.get("forced_by_source") is False
            and assumption.get("estimated_impact") in {"high", "medium"}
            and not assumption.get("dominance_proof")
            and assumption.get("cost_acceptable") is True
            and assumption.get("resolution") in {
                None, "", "unresolved",
            }
            and unmaterialized
        ):
            opportunities.append(_opportunity(
                "unmaterialized_assumption_branch",
                "high",
                assumption_id=str(assumption_id),
                branch_ids=unmaterialized,
                affected_requirements=assumption.get(
                    "affected_requirements", []
                ),
                affected_claims=assumption.get("affected_claims", []),
                direction_of_bias=assumption.get("direction_of_bias"),
            ))
    return opportunities


def render_assumptions(root: Path) -> Path:
    """Render docs/assumptions.md as a view of the authoritative JSON ledger."""
    root = root.resolve()
    data = read_json(root / "docs" / "assumptions.json", {})
    values = (
        data.get("assumptions", {})
        if isinstance(data, dict)
        and isinstance(data.get("assumptions"), dict)
        else data
    )
    lines = [
        "# 假设登记表",
        "",
        "> 本文件由 docs/assumptions.json 生成；请勿直接维护此视图。",
        "",
        "| ID | 假设 | 题面强制 | 影响 | 偏差方向 | 分支状态 | 处置 |",
        "|---|---|---:|---|---|---|---|",
    ]
    if isinstance(values, dict):
        for assumption_id, assumption in sorted(values.items()):
            if not isinstance(assumption, dict):
                continue
            branches = assumption.get("alternative_branches", [])
            branch_state = ", ".join(
                f"{item.get('id')}="
                f"{'已物化' if item.get('materialized') else '未物化'}"
                for item in branches
                if isinstance(item, dict)
            )
            lines.append(
                f"| {assumption_id} | {assumption.get('statement', '')} | "
                f"{'是' if assumption.get('forced_by_source') else '否'} | "
                f"{assumption.get('estimated_impact', '')} | "
                f"{assumption.get('direction_of_bias', '')} | "
                f"{branch_state} | {assumption.get('resolution', '')} |"
            )
    output = root / "docs" / "assumptions.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def detect_unbounded_optimum_wording(root: Path) -> list[dict]:
    root = root.resolve()
    paper = root / "paper" / "final.md"
    if not paper.is_file():
        paper = root / "paper" / "draft.md"
    if not paper.is_file():
        return []
    sentences = [
        value.strip()
        for value in re.split(r"(?<=[。！？])|\n+", paper.read_text(encoding="utf-8"))
        if value.strip()
    ]
    claim_re = re.compile(r"最优|最佳|全局最优|optimal|dominates", re.IGNORECASE)
    guard_re = re.compile(r"上界|下界|gap|间隙|近优|集合|区间", re.IGNORECASE)
    opportunities = []
    for index, sentence in enumerate(sentences):
        if not claim_re.search(sentence):
            continue
        window = " ".join(sentences[max(0, index - 3):index + 4])
        if not guard_re.search(window):
            opportunities.append(_opportunity(
                "unbounded_optimum_wording",
                "medium",
                sentence_index=index,
                excerpt=sentence[:240],
            ))
    return opportunities


def build_ledger(root: Path) -> list[dict]:
    """Build the opportunity view without introducing authoritative state."""
    root = root.resolve()
    diag = read_json(root / "results" / "research_diagnostics.json", {})
    if not isinstance(diag, dict):
        diag = {}
    evidence = read_json(root / ".harness" / "evidence.json", {})
    assumptions = read_json(root / "docs" / "assumptions.json", {})
    records = [
        *detect_boundary_optima(diag),
        *detect_grid_under_resolution(diag),
        *detect_optimality_gap(evidence or {}, diag),
        *detect_search_starvation(diag),
        *detect_gap_headroom(diag),
        *detect_rank_flip(diag),
        *detect_infeasibility(diag),
        *detect_unmaterialized_branches(assumptions),
        *detect_unbounded_optimum_wording(root),
    ]
    for raw in diag.get("opportunities", []):
        if isinstance(raw, dict):
            item = dict(raw)
            item.setdefault("severity", "medium")
            item.setdefault("kind", "registered_opportunity")
            item.setdefault("id", "opp-" + canonical_hash(item)[:16])
            records.append(item)
    unique = {item["id"]: item for item in records}
    return [unique[key] for key in sorted(unique)]


def audit_discharge(root: Path) -> list[str]:
    root = root.resolve()
    ledger = build_ledger(root)
    data = read_json(root / "results" / "opportunity_outcomes.json", {})
    outcomes = data.get("outcomes", []) if isinstance(data, dict) else []
    by_id = {
        item.get("opportunity_id"): item
        for item in outcomes
        if isinstance(item, dict) and item.get("opportunity_id")
    }
    errors = [
        f"Opportunity 未处置: {item['id']}"
        for item in ledger
        if item["id"] not in by_id
    ]
    final = root / "paper" / "final.md"
    text = final.read_text(encoding="utf-8") if final.is_file() else ""
    for opportunity_id, item in by_id.items():
        action = item.get("action")
        reason = item.get("reason_code")
        if action not in ACTIONS:
            errors.append(f"Opportunity action 非法: {opportunity_id}")
        if reason not in REASON_CODES:
            errors.append(f"Opportunity reason_code 非法: {opportunity_id}")
        if (
            action in {"deferred", "budget_qualified_stop"}
            and opportunity_id not in text
        ):
            errors.append(f"Opportunity 未注入成稿局限性: {opportunity_id}")
    return sorted(set(errors))


def _search_improvement_node(
    search_id: str,
    slug: str,
    opportunity_ids: list[str],
    depends_on: list[str],
    method_pack: str,
) -> dict:
    artifact = f"results/search_improvement/{slug}.json"
    evidence_id = f"result.search_improvement.{slug}"[:128]
    return {
        "question": (
            f"search {search_id} 的求解质量能否提升到 gap ≤ "
            f"{GAP_ALERT_FRACTION:g}，或给出机器可查的支配性界/预算耗尽证明？"
        ),
        "task_type": "solver_engineering",
        "milestone": "s3",
        "depends_on": depends_on,
        "input_evidence": [],
        "method_pack": method_pack,
        "enforce_contract": True,
        "opportunity_ids": opportunity_ids,
        "outputs": [
            {
                "evidence_id": evidence_id,
                "kind": "result",
                "statement": (
                    f"search {search_id} 提升后的求解遥测与 gap 结论"
                ),
                "artifact": artifact,
            }
        ],
        "acceptance": [
            {
                "kind": "search_quality",
                "search_id": search_id,
                "max_gap_fraction": GAP_ALERT_FRACTION,
                "when": "optimization_relevant",
            }
        ],
        "workstreams": [
            {
                "id": "search_improvement",
                "role": "solver-team",
                "description": (
                    f"通过更强模型化、热启动或更大预算收敛 {search_id}，"
                    "或给出对偶界/支配性证明"
                ),
                "owns": [artifact],
                "outputs": [evidence_id],
                "budget": {"max_attempts": 3},
            }
        ],
        "reviews": [],
        "risk": {
            "downstream_impact": 4,
            "uncertainty": 3,
            "estimated_cost": 3,
        },
    }


def build_search_improvement_proposal(root: Path) -> dict:
    """Materialize undischarged search opportunities as a plan proposal.

    Returns a full problem-graph revision proposal — never auto-applied.
    Each undischarged ``search_gap_headroom`` / ``search_starvation``
    opportunity becomes an ``s3x.search_improvement.<search_id>`` boundary
    node whose acceptance demands machine-checkable search quality.  The
    result always passes :func:`validate_problem_graph` and is idempotent
    per opportunity id: a search whose node already exists (proposal applied
    earlier) only accumulates opportunity ids instead of duplicating nodes.
    Apply through the existing ``plan validate`` / ``plan apply`` chain,
    e.g. after :func:`write_proposal` lands it on
    ``SEARCH_IMPROVEMENT_PROPOSAL_PATH``.
    """
    root = root.resolve()
    data = read_json(root / "results" / "opportunity_outcomes.json", {})
    outcomes = data.get("outcomes", []) if isinstance(data, dict) else []
    handled = {
        item.get("opportunity_id")
        for item in outcomes
        if isinstance(item, dict) and item.get("opportunity_id")
    }
    by_search: dict[str, list[dict]] = {}
    for item in build_ledger(root):
        if item["kind"] not in SEARCH_IMPROVEMENT_KINDS:
            continue
        if item["id"] in handled:
            continue
        search_id = str(item.get("search_id") or "")
        if search_id:
            by_search.setdefault(search_id, []).append(item)
    graph = read_json(root / ".harness" / "problem_graph.json")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), dict):
        seed = (
            Path(__file__).parent.parent / "templates" /
            "config" / "problem_graph.seed.json"
        )
        graph = read_json(seed, {"schema": 1, "revision": 0, "nodes": {}})
    proposal = copy.deepcopy(graph)
    nodes = proposal["nodes"]
    parent = nodes.get(SEARCH_IMPROVEMENT_PARENT)
    method_pack = (
        parent.get("method_pack") if isinstance(parent, dict) else None
    ) or "solver-validation"
    for search_id in sorted(by_search):
        opportunity_ids = sorted({
            item["id"] for item in by_search[search_id]
        })
        slug = re.sub(r"[^A-Za-z0-9_.:-]", "_", search_id)
        node_id = (SEARCH_IMPROVEMENT_NODE_PREFIX + slug)[:128]
        existing = nodes.get(node_id)
        if isinstance(existing, dict):
            existing["opportunity_ids"] = sorted({
                *existing.get("opportunity_ids", []),
                *opportunity_ids,
            })
            continue
        nodes[node_id] = _search_improvement_node(
            search_id,
            slug,
            opportunity_ids,
            [SEARCH_IMPROVEMENT_PARENT] if parent is not None else [],
            method_pack,
        )
    return validate_problem_graph(proposal)


def write_proposal(root: Path) -> Path:
    """Persist the proposal where plan validate/apply can consume it."""
    root = root.resolve()
    proposal = build_search_improvement_proposal(root)
    path = root / SEARCH_IMPROVEMENT_PROPOSAL_PATH
    atomic_write_json(path, proposal)
    return path
