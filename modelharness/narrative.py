from __future__ import annotations

import re
from pathlib import Path

from .evidence import EvidenceGraph
from .storage import read_json

CLAIM_RE = re.compile(r"\[\[([A-Za-z][A-Za-z0-9_.:-]{0,127})\]\]")


def build_brief(root: Path) -> Path:
    graph = EvidenceGraph(root)
    outline = read_json(root / "config" / "narrative.json", {"sections": []})
    lines = [
        "# 论文叙事证据包", "",
        "> 只允许使用下列 verified 证据。关键结论保留 `[[节点ID]]`。", "",
    ]
    for section in outline.get("sections", []):
        lines += [f"## {section['title']}", "", section["question"], ""]
        kinds = set(section.get("evidence_kinds", []))
        nodes = graph.verified(kinds or None)
        lines.extend(
            f"- [[{item['id']}]] {item['statement']}（`{item['artifact']}`）"
            for item in nodes
        )
        lines.append("")
    output = root / "paper" / "narrative_brief.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    return output


def audit_paper(root: Path, paper: str = "paper/draft.md") -> list[str]:
    graph = EvidenceGraph(root)
    path = (root / paper).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return [f"论文不存在或越界: {paper}"]
    text = path.read_text(encoding="utf-8")
    refs = CLAIM_RE.findall(text)
    errors = [] if refs else ["正文没有任何 [[证据节点ID]] 引用"]
    nodes = graph.nodes
    for node_id in refs:
        if node_id not in nodes:
            errors.append(f"引用未知节点: {node_id}")
        elif nodes[node_id]["status"] != "verified":
            errors.append(f"引用未验证节点: {node_id}")
    errors.extend(graph.audit())
    return sorted(set(errors))
