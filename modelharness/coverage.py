"""Requirement-to-paper explanation coverage, orthogonal to claim evidence."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contracts import safe_relative
from .optimization import optimization_relevant
from .storage import atomic_write_json, read_json


def _requirements(root: Path) -> dict[str, dict]:
    value = read_json(root / "problem" / "requirements.json", {})
    records = value.get("requirements", {}) if isinstance(value, dict) else {}
    return records if isinstance(records, dict) else {}


def required_elements(root: Path, requirement: dict) -> list[str]:
    explicit = requirement.get("explanation_elements")
    if isinstance(explicit, list) and all(isinstance(item, str) for item in explicit):
        return explicit
    kind = requirement.get("type")
    if kind == "answer":
        values = ["answer", "derivation", "verification", "scope"]
        if optimization_relevant(root):
            values[1:1] = ["model", "constraints", "algorithm"]
        return values
    if kind in {"constraint", "model_condition", "prohibition"}:
        return ["constraints", "verification", "scope"]
    if kind == "data_input":
        return ["data", "verification"]
    if kind == "delivery":
        return ["compliance"]
    return ["answer", "scope"]


def initialize_coverage_matrix(root: Path) -> dict:
    root = root.resolve()
    path = root / "paper" / "coverage_matrix.json"
    if path.exists():
        raise ValueError("paper/coverage_matrix.json already exists")
    records = {}
    for requirement_id, requirement in sorted(_requirements(root).items()):
        if not isinstance(requirement, dict) or requirement.get("mandatory") is False:
            continue
        elements = required_elements(root, requirement)
        records[requirement_id] = {
            "claim_ids": list(requirement.get("claim_ids", [])),
            "required_elements": elements,
            "elements": {
                name: {"section": "", "anchors": [], "evidence_ids": []}
                for name in elements
            },
        }
    value = {"schema": 1, "paper": "paper/final.md", "requirements": records}
    atomic_write_json(path, value)
    return value


def _section_exists(text: str, section: str) -> bool:
    if not section.strip():
        return False
    target = section.strip().casefold()
    return any(
        target in line.lstrip("# ").strip().casefold()
        for line in text.splitlines()
        if re.match(r"^\s*#{1,6}\s+", line)
    )


def audit_explanation_coverage(root: Path) -> list[str]:
    """Fail on missing answer parts, even when every existing claim is valid."""
    root = root.resolve()
    requirements = _requirements(root)
    if not requirements:
        return []
    data = read_json(root / "paper" / "coverage_matrix.json")
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["coverage_matrix missing or schema is not 1"]
    paper_relative = str(data.get("paper", "paper/final.md"))
    try:
        paper_path = safe_relative(root, paper_relative)
    except ValueError:
        return [f"coverage paper path escapes project: {paper_relative}"]
    if not paper_path.is_file():
        return [f"coverage paper missing: {paper_relative}"]
    text = paper_path.read_text(encoding="utf-8")
    records = data.get("requirements", {})
    evidence = read_json(root / ".harness" / "evidence.json", {})
    evidence_nodes = evidence.get("nodes", {}) if isinstance(evidence, dict) else {}
    errors: list[str] = []
    for requirement_id, requirement in requirements.items():
        if not isinstance(requirement, dict) or requirement.get("mandatory") is False:
            continue
        record = records.get(requirement_id) if isinstance(records, dict) else None
        if not isinstance(record, dict):
            errors.append(f"mandatory requirement absent from coverage matrix: {requirement_id}")
            continue
        expected_claims = set(map(str, requirement.get("claim_ids", [])))
        mapped_claims = set(map(str, record.get("claim_ids", [])))
        if not expected_claims.issubset(mapped_claims):
            errors.append(f"coverage matrix misses requirement claims: {requirement_id}")
        expected_elements = required_elements(root, requirement)
        declared = record.get("required_elements", [])
        if set(declared) != set(expected_elements):
            errors.append(f"coverage element contract drift: {requirement_id}")
        elements = record.get("elements", {})
        for name in expected_elements:
            item = elements.get(name) if isinstance(elements, dict) else None
            if not isinstance(item, dict):
                errors.append(f"coverage element missing: {requirement_id}/{name}")
                continue
            section = str(item.get("section", ""))
            if not _section_exists(text, section):
                errors.append(f"coverage section missing: {requirement_id}/{name}")
            anchors = item.get("anchors", [])
            if not isinstance(anchors, list) or not anchors or not all(
                isinstance(anchor, str) and anchor.strip() and anchor in text
                for anchor in anchors
            ):
                errors.append(f"coverage anchor missing from paper: {requirement_id}/{name}")
            if name == "verification":
                ids = item.get("evidence_ids", [])
                if not isinstance(ids, list) or not ids:
                    errors.append(f"verification evidence missing: {requirement_id}")
                for evidence_id in ids if isinstance(ids, list) else []:
                    node = evidence_nodes.get(evidence_id, {}) if isinstance(evidence_nodes, dict) else {}
                    if node.get("status") != "verified" or node.get("freshness", "valid") != "valid":
                        errors.append(f"verification evidence not current: {requirement_id}/{evidence_id}")
    return sorted(set(errors))
