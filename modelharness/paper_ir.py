"""I/O facade compiling paper/src into paper/paper_ir.json and paper/draft.md."""
from __future__ import annotations

from pathlib import Path

from .claims import _evaluate_one
from .contracts import safe_relative
from .paper_ir_core import (
    GENERATOR,
    MANIFEST_RELATIVE,
    build_ir,
    lint_document,
    normalize_whitelist,
    parse_manifest,
    parse_section,
    render_document,
)
from .storage import atomic_write_json, read_json
from .util import sha256


def _claim_records(root: Path) -> dict[str, dict]:
    """Evaluate bindings in memory; never write results/claim_values.json."""
    binding_path = root / "config" / "claim_bindings.json"
    if not binding_path.is_file():
        return {}
    bindings = read_json(binding_path)
    if not isinstance(bindings, dict) or bindings.get("schema") != 1:
        raise ValueError("config/claim_bindings.json schema 必须是 1")
    claims = bindings.get("claims")
    if not isinstance(claims, dict):
        raise ValueError("config/claim_bindings.json claims 必须是对象")
    records: dict[str, dict] = {}
    for claim_id, binding in claims.items():
        record = _evaluate_one(root, claim_id, binding)
        records[claim_id] = {
            "displayed": record.get("displayed"),
            "valid": record.get("status") == "valid",
            "derived": (
                isinstance(binding, dict) and "derived_from" in binding
            ),
        }
    return records


def _decision_statements(root: Path) -> dict[str, str]:
    """Read the parallel-agent decision manifest; tolerate absence."""
    data = read_json(
        root / "results" / "decision_variable_manifest.json"
    )
    if not isinstance(data, dict) or data.get("schema") != 1:
        return {}
    variables = data.get("variables")
    if not isinstance(variables, list):
        return {}
    return {
        str(item["id"]): str(item.get("statement", ""))
        for item in variables
        if isinstance(item, dict) and item.get("id")
    }


def _number_whitelist(root: Path) -> tuple[list[str], list[str]]:
    contract = read_json(root / "config" / "paper_content_contract.json", {})
    raw = contract.get("number_whitelist") if isinstance(contract, dict) else None
    values, entry_errors = normalize_whitelist(raw)
    return values, [
        f"invalid_number_whitelist: config/paper_content_contract.json: {item}"
        for item in entry_errors
    ]


def compile_paper(root: Path, check: bool = False) -> dict:
    """Compile the structured paper source; ``check`` lints without writing."""
    root = root.resolve()
    manifest_path = root / "paper" / "src" / "manifest.json"
    if not manifest_path.is_file():
        if check:
            return {
                "schema": 1, "generator": GENERATOR, "adopted": False,
                "checked": True, "ok": True, "errors": [],
                "note": f"{MANIFEST_RELATIVE} 缺失，Paper IR 未启用",
            }
        raise ValueError(f"{MANIFEST_RELATIVE} 缺失：请先创建论文结构化源")
    specs, errors = parse_manifest(read_json(manifest_path))
    sections: list[dict] = []
    for spec in specs:
        relative = f"paper/src/{spec['file']}"
        path = safe_relative(root, relative)
        if not path.is_file():
            errors.append(f"section_missing: {relative}")
            continue
        sections.append({
            "file": relative,
            "title": spec.get("title"),
            "sha256": sha256(path),
            "blocks": parse_section(path.read_text(encoding="utf-8")),
        })
    claims = _claim_records(root)
    whitelist, whitelist_errors = _number_whitelist(root)
    errors.extend(whitelist_errors)
    decisions = _decision_statements(root)
    errors.extend(lint_document(sections, claims, whitelist, decisions))
    report = {
        "schema": 1,
        "generator": GENERATOR,
        "adopted": True,
        "checked": check,
        "ok": not errors,
        "errors": errors,
        "sections": [
            {"file": section["file"], "blocks": len(section["blocks"])}
            for section in sections
        ],
    }
    if check or errors:
        return report
    atomic_write_json(
        root / "paper" / "paper_ir.json",
        build_ir(sections, sha256(manifest_path)),
    )
    draft = root / "paper" / "draft.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(
        render_document(sections, claims, decisions),
        encoding="utf-8", newline="\n",
    )
    report["outputs"] = {
        "paper_ir": "paper/paper_ir.json",
        "draft": "paper/draft.md",
        "draft_sha256": sha256(draft),
    }
    return report
