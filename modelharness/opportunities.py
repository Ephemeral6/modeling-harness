"""Derived improvement opportunities and discharge accounting."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .problem_graph import canonical_hash
from .storage import read_json


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
            if isinstance(values, list) and values[:2] == list(reversed(expected)):
                opportunities.append(_opportunity(
                    "robust_reoptimization",
                    "high",
                    search_id=search_id,
                    stress_index=index,
                    trigger="rank_flip",
                ))
    return opportunities


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
        return []
    evidence = read_json(root / ".harness" / "evidence.json", {})
    records = [
        *detect_boundary_optima(diag),
        *detect_grid_under_resolution(diag),
        *detect_optimality_gap(evidence or {}, diag),
        *detect_rank_flip(diag),
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
