"""Paper IR: typed-placeholder parsing, linting and deterministic rendering."""
from __future__ import annotations

import re
from typing import Any

GENERATOR = "modelharness.paper_ir.compile_paper"
MANIFEST_RELATIVE = "paper/src/manifest.json"

PLACEHOLDER_RE = re.compile(
    r"\{(num|ev|decision):([A-Za-z][A-Za-z0-9_.:-]{0,127})\}"
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_TABLE_RE = re.compile(r"^\s*\|")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_.])")
_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
_INLINE_MATH_RE = re.compile(r"\$[^$\n]+\$")
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_LIST_MARKER_RE = re.compile(r"^(\s*)\d+[.)]\s")
_ARITHMETIC_RE = re.compile(r"[+\-±×÷*%]|相差|之和|之差|合计|百分之")
_CJK_RE = re.compile(r"[一-鿿]")
_SECTION_FILE_RE = re.compile(r"^[^/\\]+\.md$")

_BANNER = (
    "<!-- 本文件由 modelharness paper compile 生成；"
    "请编辑 paper/src/ 下的源分节，不要直接修改本文件。 -->"
)
_INLINE_TYPES = {"num": "claim_number", "ev": "citation", "decision": "decision"}
_INLINE_KEYS = {"num": "claim_id", "ev": "evidence_id", "decision": "variable_id"}


def parse_manifest(data: Any) -> tuple[list[dict], list[str]]:
    """Validate the section manifest and return ordered section specs."""
    prefix = f"manifest_invalid: {MANIFEST_RELATIVE}"
    if not isinstance(data, dict) or data.get("schema") != 1:
        return [], [f"{prefix}: schema 必须是 1"]
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        return [], [f"{prefix}: sections 必须是非空数组"]
    specs: list[dict] = []
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(sections):
        if not isinstance(item, dict):
            errors.append(f"{prefix}: sections[{index}] 必须是对象")
            continue
        name = item.get("file")
        if (
            not isinstance(name, str)
            or name.startswith(".")
            or _SECTION_FILE_RE.match(name) is None
        ):
            errors.append(f"{prefix}: sections[{index}].file 非法")
            continue
        if name in seen:
            errors.append(f"{prefix}: sections[{index}].file 重复: {name}")
            continue
        title = item.get("title")
        if title is not None and not isinstance(title, str):
            errors.append(f"{prefix}: sections[{index}].title 必须是字符串")
            continue
        seen.add(name)
        specs.append({"file": name, "title": title})
    return specs, errors


def _parse_inlines(text: str, first_line: int) -> list[dict]:
    nodes: list[dict] = []
    for offset, raw in enumerate(text.split("\n")):
        line_no = first_line + offset
        position = 0
        for match in PLACEHOLDER_RE.finditer(raw):
            if match.start() > position:
                nodes.append({
                    "type": "text",
                    "line": line_no,
                    "text": raw[position:match.start()],
                })
            kind = match.group(1)
            nodes.append({
                "type": _INLINE_TYPES[kind],
                "line": line_no,
                _INLINE_KEYS[kind]: match.group(2),
            })
            position = match.end()
        if position < len(raw):
            nodes.append({
                "type": "text", "line": line_no, "text": raw[position:],
            })
    return nodes


def parse_section(text: str) -> list[dict]:
    """Parse one section source into a flat block list with 1-based lines."""
    lines = text.split("\n")
    total = len(lines)
    blocks: list[dict] = []
    index = 0
    if lines and lines[0].strip() == "---":
        index = 1
        while index < total and lines[index].strip() != "---":
            index += 1
        if index < total:
            index += 1
        chunk = "\n".join(lines[:index])
        blocks.append({
            "type": "front_matter", "line": 1, "text": chunk,
            "inlines": _parse_inlines(chunk, 1),
        })
    while index < total:
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append({
                "type": "heading", "line": index + 1,
                "level": len(heading.group(1)), "text": heading.group(2),
            })
            index += 1
            continue
        if _FENCE_RE.match(line):
            start = index
            index += 1
            while index < total and not _FENCE_RE.match(lines[index]):
                index += 1
            if index < total:
                index += 1
            blocks.append({
                "type": "pseudocode", "line": start + 1,
                "text": "\n".join(lines[start:index]),
            })
            continue
        if stripped.startswith("$$"):
            start = index
            index += 1
            if not (len(stripped) > 2 and stripped.endswith("$$")):
                while index < total and not lines[index].strip().endswith("$$"):
                    index += 1
                if index < total:
                    index += 1
            blocks.append({
                "type": "equation", "line": start + 1,
                "text": "\n".join(lines[start:index]),
            })
            continue
        if _TABLE_RE.match(line):
            start = index
            while index < total and _TABLE_RE.match(lines[index]):
                index += 1
            blocks.append({
                "type": "table", "line": start + 1,
                "text": "\n".join(lines[start:index]),
            })
            continue
        start = index
        while index < total:
            candidate = lines[index]
            if (
                not candidate.strip()
                or _HEADING_RE.match(candidate)
                or _FENCE_RE.match(candidate)
                or _TABLE_RE.match(candidate)
                or candidate.strip().startswith("$$")
            ):
                break
            index += 1
        chunk = "\n".join(lines[start:index])
        blocks.append({
            "type": "prose", "line": start + 1, "text": chunk,
            "inlines": _parse_inlines(chunk, start + 1),
        })
    return blocks


def normalize_whitelist(raw: Any) -> tuple[list[str], list[str]]:
    """Return canonical whitelist values plus errors for illegal entries."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["number_whitelist 必须是数组"]
    values: list[str] = []
    errors: list[str] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or "value" not in entry:
            errors.append(f"number_whitelist[{index}] 缺少 value")
            continue
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"number_whitelist[{index}] 缺少 reason")
            continue
        values.append(str(entry["value"]))
    return values, errors


def _has_arithmetic(text: str) -> bool:
    """Detect arithmetic wording; 只/年 style CJK unit slashes are exempt."""
    if _ARITHMETIC_RE.search(text):
        return True
    for match in re.finditer("/", text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if _CJK_RE.match(before) and _CJK_RE.match(after):
            continue
        return True
    return False


def _whitelisted(text: str, whitelist: list[str]) -> bool:
    for value in whitelist:
        if text == value:
            return True
        try:
            if float(text) == float(value):
                return True
        except ValueError:
            continue
    return False


def _lint_prose_line(
    raw: str,
    file: str,
    line_no: int,
    claims: dict[str, dict],
    whitelist: list[str],
    decisions: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    where = f"{file}:{line_no}"
    matches = list(PLACEHOLDER_RE.finditer(raw))
    for match in matches:
        kind, ident = match.group(1), match.group(2)
        if kind == "num":
            record = claims.get(ident)
            if record is None:
                errors.append(
                    f"unbound_claim: {where}: {{num:{ident}}} 没有对应 binding"
                )
            elif not record.get("valid"):
                errors.append(
                    f"stale_claim_value: {where}: {ident} 的 binding 评估未通过"
                )
        elif kind == "decision" and ident not in decisions:
            errors.append(
                f"unbound_decision_reference: {where}: {ident} 不在 "
                "results/decision_variable_manifest.json"
            )
    numeric = [match for match in matches if match.group(1) == "num"]
    for left, right in zip(numeric, numeric[1:]):
        between = raw[left.end():right.start()]
        if not _has_arithmetic(between):
            continue
        endpoints = (left.group(2), right.group(2))
        if any(claims.get(ident, {}).get("derived") for ident in endpoints):
            continue
        errors.append(
            f"cross_caliber_arithmetic: {where}: {endpoints[0]} 与 "
            f"{endpoints[1]} 之间存在运算表述且无 derived_from 声明"
        )
    masked = PLACEHOLDER_RE.sub("\x00", raw)
    masked = _INLINE_MATH_RE.sub("\x00", masked)
    masked = _INLINE_CODE_RE.sub("\x00", masked)
    masked = _LIST_MARKER_RE.sub("\\g<1>\x00 ", masked)
    for match in _NUMBER_RE.finditer(masked):
        text = match.group(0)
        if _YEAR_RE.match(text) or _whitelisted(text, whitelist):
            continue
        errors.append(
            f"bare_number_in_prose: {where}: 裸数字 {text} 未经 {{num:}} 占位符"
        )
    return errors


def lint_document(
    sections: list[dict],
    claims: dict[str, dict],
    whitelist: list[str],
    decisions: dict[str, str],
) -> list[str]:
    """Lint prose and front matter; each error carries file plus line."""
    errors: list[str] = []
    for section in sections:
        for block in section["blocks"]:
            if block["type"] not in {"prose", "front_matter"}:
                continue
            for offset, raw in enumerate(block["text"].split("\n")):
                errors.extend(_lint_prose_line(
                    raw, section["file"], block["line"] + offset,
                    claims, whitelist, decisions,
                ))
    return errors


def _render_inline(
    text: str, claims: dict[str, dict], decisions: dict[str, str]
) -> str:
    def replace(match: re.Match[str]) -> str:
        kind, ident = match.group(1), match.group(2)
        if kind == "num":
            displayed = claims.get(ident, {}).get("displayed")
            return str(displayed) if displayed is not None else match.group(0)
        if kind == "ev":
            return f"[[{ident}]]"
        return decisions.get(ident) or match.group(0)

    return PLACEHOLDER_RE.sub(replace, text)


def render_document(
    sections: list[dict],
    claims: dict[str, dict],
    decisions: dict[str, str],
) -> str:
    """Render the block tree back to deterministic Markdown."""
    parts = [_BANNER]
    for section in sections:
        if section.get("title"):
            parts.append(f"# {section['title']}")
        for block in section["blocks"]:
            if block["type"] == "heading":
                parts.append("#" * block["level"] + " " + block["text"])
            elif block["type"] in {"prose", "front_matter"}:
                parts.append(_render_inline(block["text"], claims, decisions))
            else:
                parts.append(block["text"])
    return "\n\n".join(parts) + "\n"


def build_ir(sections: list[dict], manifest_sha256: str) -> dict:
    """Assemble the timestamp-free, deterministic IR document."""
    return {
        "schema": 1,
        "generator": GENERATOR,
        "manifest_sha256": manifest_sha256,
        "sections": [
            {
                "file": section["file"],
                "title": section.get("title"),
                "sha256": section["sha256"],
                "blocks": section["blocks"],
            }
            for section in sections
        ],
    }
