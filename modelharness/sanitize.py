"""Render internal paper drafts and mechanically audit delivery artifacts."""
from __future__ import annotations

import re
from pathlib import Path

from .contracts import safe_relative
from .storage import atomic_write_json, read_json


INTERNAL_PATTERNS: list[tuple[str, str]] = [
    (r"\[\[[^\]\n]{1,128}\]\]", "evidence_marker"),
    (r"[A-Za-z]:[\\/](?:Users|home)[\\/]", "absolute_path"),
    (r"(?<![\w./])\.harness/", "internal_path"),
    (
        r"(?<![\w./])(?:results|config|checks)/"
        r"[A-Za-z0-9_./-]+\.(?:json|py|csv)",
        "internal_path",
    ),
    (r"\bTODO\b|\bFIXME\b|\bXXX\b|待补|占位符?", "placeholder"),
    (r"\{\{[^}\n]{1,64}\}\}", "unresolved_template"),
    (r"\b[0-9a-f]{40,64}\b", "raw_hash"),
]

_MARKER_RE = re.compile(INTERNAL_PATTERNS[0][0])
_HEADING_RE = re.compile(r"(?m)^#{1,6}\s+(.+?)\s*$")
_FIGURE_RE = re.compile(r"!\[[^\]\n]*\]\([^)]+\)")
_FIGURE_NUMBER_RE = re.compile(r"(?:图|Figure)\s*([0-9]+)", re.IGNORECASE)
_REFERENCE_HEADER_RE = re.compile(
    r"(?mi)^#{1,6}\s*(?:参考文献|references?)\s*$"
)
_REFERENCE_ENTRY_RE = re.compile(
    r"(?m)^\s*(?:\[[0-9]+\]|[0-9]+[.)])\s*(\S.*)$"
)
_SELF_REFERENCE_RE = re.compile(
    r"本文项目内|随论文交付|"
    r"(?<![\w./])(?:results|config|checks|paper|docs|src)/",
    re.IGNORECASE,
)
_YAML_FRONT_MATTER_RE = re.compile(
    r"\A---\s*\n(?P<body>.*?)\n---\s*(?:\n|\Z)", re.DOTALL
)
_YAML_ABSTRACT_RE = re.compile(r"(?mi)^abstract\s*:")


def _profile(root: Path) -> dict:
    return read_json(root / "config" / "delivery_profile.json", {}) or {}


def _titles(text: str) -> set[str]:
    titles = {
        match.strip().casefold() for match in _HEADING_RE.findall(text)
    }
    front_matter = _YAML_FRONT_MATTER_RE.match(text)
    if front_matter and _YAML_ABSTRACT_RE.search(front_matter.group("body")):
        titles.add("摘要")
    return titles


def _missing_sections(text: str, profile: dict, field: str) -> list[str]:
    titles = _titles(text)
    return [
        str(section)
        for section in profile.get(field, [])
        if str(section).strip().casefold() not in titles
    ]


def _reference_entries(text: str) -> list[str]:
    match = _REFERENCE_HEADER_RE.search(text)
    if not match:
        return []
    return _REFERENCE_ENTRY_RE.findall(text[match.end():])


def _inject_deferred(root: Path, text: str) -> str:
    data = read_json(root / "results" / "opportunity_outcomes.json", {})
    outcomes = data.get("outcomes", []) if isinstance(data, dict) else []
    pending = [
        item for item in outcomes
        if isinstance(item, dict)
        and item.get("action") in {"deferred", "budget_qualified_stop"}
        and item.get("opportunity_id")
        and item["opportunity_id"] not in text
    ]
    if not pending:
        return text
    reasons = {
        "budget_exhausted": "预算已耗尽",
        "dominated_by_bound": "已由界支配",
        "out_of_scope_by_user": "超出用户授权范围",
        "problem_forced": "边界由题面强制",
        "resolved_interior": "扩展后已回到内部解",
    }
    lines = ["", "### 未闭合改进机会（自动注入）", ""]
    for item in pending:
        reason = reasons.get(item.get("reason_code"), str(item.get("reason_code")))
        lines.append(
            f"- {item['opportunity_id']}：{reason}；"
            f"处置={item['action']}，结论受此限制。"
        )
    return text.rstrip() + "\n" + "\n".join(lines) + "\n"


def render_final(
    root: Path,
    draft: str = "paper/draft.md",
    out: str = "paper/final.md",
) -> Path:
    """Strip internal evidence markers and materialize a delivery artifact."""
    root = root.resolve()
    source = safe_relative(root, draft)
    target = safe_relative(root, out)
    if not source.is_file():
        raise ValueError(f"工作稿不存在: {draft}")
    text = _inject_deferred(
        root, source.read_text(encoding="utf-8")
    )
    parts: list[str] = []
    claims: list[dict] = []
    cursor = 0
    final_offset = 0
    for match in _MARKER_RE.finditer(text):
        before = text[cursor:match.start()]
        parts.append(before)
        final_offset += len(before)
        claims.append({
            "claim_id": match.group(0)[2:-2],
            "marker": match.group(0),
            "draft_span": [match.start(), match.end()],
            "final_offset": final_offset,
        })
        cursor = match.end()
    parts.append(text[cursor:])
    rendered = "".join(parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(rendered, encoding="utf-8", newline="\n")
    atomic_write_json(root / "paper" / "claim_map.json", {
        "schema": 1,
        "draft": draft,
        "final": out,
        "claims": claims,
    })
    sanitize_report(root, out)
    return target


def sanitize_report(
    root: Path,
    path: str = "paper/final.md",
) -> dict:
    """Return and persist structured G9 delivery violations."""
    root = root.resolve()
    target = safe_relative(root, path)
    profile = _profile(root)
    violations: list[dict] = []
    if not target.is_file():
        violations.append({"kind": "missing_delivery", "path": path})
        text = ""
    else:
        text = target.read_text(encoding="utf-8")
    for pattern, kind in INTERNAL_PATTERNS:
        for match in re.finditer(pattern, text):
            violations.append({
                "kind": kind,
                "span": [match.start(), match.end()],
                "text": match.group(0),
            })

    figures = _FIGURE_RE.findall(text)
    minimum_figures = int(profile.get("min_figures", 0))
    if len(figures) < minimum_figures:
        violations.append({
            "kind": "figure_shortfall",
            "actual": len(figures),
            "required": minimum_figures,
        })
    numbers = [int(value) for value in _FIGURE_NUMBER_RE.findall(text)]
    unique_numbers = sorted(set(numbers))
    if unique_numbers and unique_numbers != list(
        range(1, max(unique_numbers) + 1)
    ):
        violations.append({
            "kind": "figure_numbering_gap",
            "actual": unique_numbers,
        })

    references = _reference_entries(text)
    external = [
        entry for entry in references if not _SELF_REFERENCE_RE.search(entry)
    ]
    minimum_references = int(profile.get("min_external_references", 0))
    if len(external) < minimum_references:
        violations.append({
            "kind": "reference_shortfall",
            "actual": len(external),
            "required": minimum_references,
        })
    if references and not external:
        violations.append({
            "kind": "self_citation_only",
            "actual": len(references),
        })

    for section in _missing_sections(text, profile, "required_sections"):
        violations.append({"kind": "missing_section", "section": section})
    format_spec = profile.get("format_spec")
    if isinstance(format_spec, str) and format_spec:
        spec = safe_relative(root, format_spec)
        if not spec.is_file():
            violations.append({
                "kind": "format_spec_unbound",
                "path": format_spec,
            })
    scenario_sets = read_json(root / "results" / "scenario_sets.json", {})
    if (
        isinstance(scenario_sets, dict)
        and scenario_sets.get("selection_bias_acknowledged") is True
    ):
        qualifiers = (
            "选择偏差", "非无偏", "偏乐观", "selection bias",
            "not unbiased",
        )
        if not any(value.casefold() in text.casefold() for value in qualifiers):
            violations.append({
                "kind": "selection_bias_qualification_missing",
                "path": path,
            })
    report = {
        "schema": 1,
        "path": path,
        "ok": not violations,
        "violations": violations,
        "stats": {
            "figures": len(figures),
            "references": len(references),
            "external_references": len(external),
        },
    }
    atomic_write_json(root / "results" / "delivery_check.json", report)
    return report
