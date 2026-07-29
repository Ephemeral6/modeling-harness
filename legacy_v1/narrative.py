from __future__ import annotations

import re
from pathlib import Path

from .evidence import EvidenceGraph
from .util import read_json, write_json

CLAIM_RE = re.compile(r"\[\[([A-Za-z0-9_.:-]+)\]\]")


def build_brief(root: Path) -> Path:
    graph = EvidenceGraph(root)
    outline = read_json(root / "config" / "narrative.json", {})
    lines = [
        "# 论文叙事证据包",
        "",
        "> 只允许使用下列已验证节点。正文中的关键结论须保留 `[[节点ID]]` 引用，"
        "定稿时再转成脚注或来源说明。",
        "",
    ]
    for section in outline.get("sections", []):
        lines += [f"## {section['title']}", "", section["question"], ""]
        allowed = set(section.get("evidence_kinds", []))
        nodes = graph.verified(allowed or None)
        if not nodes:
            lines += ["- 暂无可用的 verified 证据。", ""]
            continue
        for node in nodes:
            lines.append(
                f"- [[{node['id']}]] {node['statement']} "
                f"（证据：`{node['artifact']}`）"
            )
        lines.append("")
    out = root / "paper" / "narrative_brief.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def audit_paper(root: Path, paper: str = "paper/draft.md") -> list[str]:
    graph = EvidenceGraph(root)
    path = root / paper
    if not path.exists():
        return [f"论文不存在: {paper}"]
    refs = CLAIM_RE.findall(path.read_text(encoding="utf-8"))
    errors = []
    if not refs:
        errors.append("正文没有任何 [[证据节点ID]] 引用")
    for node_id in refs:
        node = graph.nodes.get(node_id)
        if not node:
            errors.append(f"引用未知节点: {node_id}")
        elif node["status"] != "verified":
            errors.append(f"引用未验证节点: {node_id} ({node['status']})")
    errors.extend(graph.audit())
    return sorted(set(errors))
