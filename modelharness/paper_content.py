"""Requirement-driven paper exposition contracts and mechanical audits."""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from .contracts import safe_relative
from .storage import atomic_write_json, read_json
from .util import now, sha256


_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_FORMAT_PATTERNS = {
    "equation": re.compile(
        r"\$\$[\s\S]+?\$\$|\$[^\n$]+\$|\\\[[\s\S]+?\\\]"
        r"|\\begin\{(?:equation\*?|align\*?|gather\*?)\}",
        re.MULTILINE,
    ),
    "table": re.compile(
        r"^\s*\|.+\|\s*$\n^\s*\|\s*:?-{3,}[^\n]+\|\s*$"
        r"|\\begin\{(?:table|tabular)\}",
        re.MULTILINE,
    ),
    "pseudocode": re.compile(
        r"```(?:text|pseudo|pseudocode|algorithm)?\s*\n[\s\S]+?```"
        r"|\\begin\{(?:algorithm|algorithmic)\}",
        re.IGNORECASE,
    ),
    "figure": re.compile(
        r"!\[[^\]]*\]\([^)]+\)|\\includegraphics(?:\[[^\]]*\])?\{[^}]+\}"
        r"|```mermaid\s*\n[\s\S]+?```",
        re.IGNORECASE,
    ),
    "flowchart": re.compile(
        r"```mermaid\s*\n[\s\S]+?```|!\[[^\]]*(?:流程|flow|state)[^\]]*\]"
        r"\([^)]+\)",
        re.IGNORECASE,
    ),
}


def _profile(root: Path) -> dict:
    value = read_json(root / "config" / "delivery_profile.json", {})
    return value if isinstance(value, dict) else {}


def contract_path(root: Path) -> Path | None:
    """Return the active profile's contract, or ``None`` when it is opt-out."""
    relative = _profile(root).get("paper_content_contract")
    if relative is None:
        return None
    if not isinstance(relative, str) or not relative:
        raise ValueError("delivery profile.paper_content_contract 非法")
    return safe_relative(root, relative)


def content_contract_enabled(root: Path) -> bool:
    return contract_path(root.resolve()) is not None


def _contract(root: Path) -> tuple[Path, dict] | None:
    path = contract_path(root)
    if path is None:
        return None
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("Paper Content Contract 缺失或 schema 不是 1")
    for field in ("body", "appendices", "obligation_defaults"):
        if field not in data:
            raise ValueError(f"Paper Content Contract 缺少 {field}")
    if not isinstance(data["body"], str) or not data["body"]:
        raise ValueError("Paper Content Contract.body 非法")
    if not isinstance(data["appendices"], list) or not all(
        isinstance(item, str) and item for item in data["appendices"]
    ):
        raise ValueError("Paper Content Contract.appendices 非法")
    if not isinstance(data["obligation_defaults"], dict):
        raise ValueError("Paper Content Contract.obligation_defaults 非法")
    return path, data


def _requirements(root: Path) -> dict[str, dict]:
    data = read_json(root / "problem" / "requirements.json", {})
    values = data.get("requirements", {}) if isinstance(data, dict) else {}
    return {
        str(key): value for key, value in values.items()
        if isinstance(key, str) and isinstance(value, dict)
    } if isinstance(values, dict) else {}


def _normalize_obligation(raw: Any) -> dict:
    if isinstance(raw, str):
        return {"kind": raw}
    if not isinstance(raw, dict):
        raise ValueError("paper obligation 必须是字符串或对象")
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError("paper obligation.kind 缺失")
    value = dict(raw)
    value["kind"] = kind
    return value


def required_obligations(contract: dict, requirement: dict) -> list[dict]:
    """Resolve defaults plus requirement-specific, load-bearing paper duties."""
    defaults = contract.get("obligation_defaults", {})
    raw_defaults = defaults.get(
        str(requirement.get("type", "answer")), defaults.get("answer", [])
    )
    if not isinstance(raw_defaults, list):
        raise ValueError("Paper Content Contract obligation default 必须是数组")
    custom = requirement.get("paper_obligations", [])
    if not isinstance(custom, list):
        raise ValueError("requirement.paper_obligations 必须是数组")
    resolved: dict[str, dict] = {}
    order: list[str] = []
    for raw in [*raw_defaults, *custom]:
        item = _normalize_obligation(raw)
        kind = item["kind"]
        if kind not in resolved:
            order.append(kind)
            resolved[kind] = {}
        resolved[kind].update(item)
    return [resolved[kind] for kind in order]


def initialize_content_coverage(root: Path) -> dict:
    """Materialize the requirement → exposition worksheet for the active profile."""
    root = root.resolve()
    loaded = _contract(root)
    if loaded is None:
        raise ValueError("当前 Delivery Profile 未启用 Paper Content Contract")
    path, contract = loaded
    records: dict[str, dict] = {}
    for requirement_id, requirement in _requirements(root).items():
        if requirement.get("mandatory") is not True:
            continue
        obligations = {}
        for spec in required_obligations(contract, requirement):
            kind = spec["kind"]
            obligations[kind] = {
                "kind": kind,
                "placement": spec.get("placement", "body"),
                "formats": spec.get("formats", ["prose"]),
                "min_evidence": spec.get("min_evidence", 1),
                "requires_body_reference": spec.get(
                    "requires_body_reference", False
                ),
                "disclosure_fields": spec.get("disclosure_fields", []),
                "artifact_contract": spec.get("artifact_contract"),
                "target": "",
                "section": "",
                "anchors": [],
                "body_anchor": "",
                "evidence_ids": [],
                "disclosure": {},
                "artifact": None,
            }
        records[requirement_id] = {
            "question": requirement.get("question", ""),
            "claim_ids": requirement.get("claim_ids", []),
            "obligations": obligations,
        }
    relative = path.relative_to(root).as_posix()
    matrix = {
        "schema": 1,
        "contract": relative,
        "contract_sha256": sha256(path),
        "body": contract["body"],
        "appendices": contract["appendices"],
        "requirements": records,
    }
    atomic_write_json(root / "paper" / "content_coverage.json", matrix)
    return matrix


def _section_text(text: str, section: str) -> str | None:
    target = section.strip().casefold()
    if not target:
        return None
    headings = list(_HEADING_RE.finditer(text))
    for index, match in enumerate(headings):
        label = match.group(2).strip().casefold()
        if target not in label:
            continue
        level = len(match.group(1))
        end = len(text)
        for following in headings[index + 1:]:
            if len(following.group(1)) <= level:
                end = following.start()
                break
        return text[match.start():end]
    return None


def _has_format(section: str, kind: str) -> bool:
    if kind == "prose":
        plain = re.sub(r"[`#|*_{}$\\\[\]()>:-]", "", section)
        return len(re.sub(r"\s+", "", plain)) >= 20
    pattern = _FORMAT_PATTERNS.get(kind)
    return bool(pattern and pattern.search(section))


def _evidence_valid(nodes: dict, evidence_id: str) -> bool:
    item = nodes.get(evidence_id)
    return bool(
        isinstance(item, dict)
        and item.get("status") == "verified"
        and item.get("freshness", "valid") == "valid"
    )


def _audit_artifact(root: Path, item: Any, spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return []
    if not isinstance(item, dict):
        return ["artifact declaration missing"]
    relative = item.get("path")
    if not isinstance(relative, str) or not relative:
        return ["artifact.path missing"]
    try:
        path = safe_relative(root, relative)
    except ValueError as exc:
        return [str(exc)]
    if not path.is_file():
        return [f"artifact missing: {relative}"]
    expected_hash = item.get("sha256")
    if not isinstance(expected_hash, str) or expected_hash != sha256(path):
        return [f"artifact hash stale: {relative}"]
    kind = spec.get("kind", "file")
    if kind == "csv_table":
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                fields = reader.fieldnames or []
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            return [f"CSV artifact unreadable: {relative}: {exc}"]
        errors = []
        minimum = spec.get("min_rows", 1)
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
            errors.append("artifact_contract.min_rows invalid")
        elif len(rows) < minimum:
            errors.append(
                f"artifact row shortfall: {relative} has {len(rows)}, needs {minimum}"
            )
        required = spec.get("required_fields", [])
        if not isinstance(required, list) or not all(
            isinstance(field, str) and field for field in required
        ):
            errors.append("artifact_contract.required_fields invalid")
        else:
            missing = sorted(set(required) - set(fields))
            if missing:
                errors.append(f"artifact fields missing: {relative}: {missing}")
        return errors
    if kind == "json_records":
        data = read_json(path)
        records = data.get("records") if isinstance(data, dict) else data
        if not isinstance(records, list):
            return [f"JSON records missing: {relative}"]
        minimum = spec.get("min_rows", 1)
        return [] if len(records) >= minimum else [
            f"artifact row shortfall: {relative} has {len(records)}, needs {minimum}"
        ]
    return []


def _decision_manifest_roles(root: Path) -> dict[str, str]:
    """Map declared decision-variable ids to roles; empty when unavailable."""
    data = read_json(root / "results" / "decision_variable_manifest.json")
    if not isinstance(data, dict) or data.get("schema") != 1:
        return {}
    variables = data.get("variables")
    if not isinstance(variables, list):
        return {}
    return {
        item["id"]: str(item.get("role", ""))
        for item in variables
        if isinstance(item, dict)
        and isinstance(item.get("id"), str) and item["id"]
    }


def _decision_claim_issues(
    root: Path, contract: dict, texts: dict[str, str]
) -> list[str]:
    """Every joint-decision phrase must map to real ``role=decision`` variables."""
    phrases = contract.get("decision_claim_phrases", [])
    if not isinstance(phrases, list) or not all(
        isinstance(phrase, str) and phrase for phrase in phrases
    ):
        return ["Paper Content Contract.decision_claim_phrases 非法"]
    raw_claims = contract.get("decision_claims", [])
    if not isinstance(raw_claims, list):
        return ["Paper Content Contract.decision_claims 非法"]
    issues: list[str] = []
    declared: dict[str, list[str]] = {}
    for claim in raw_claims:
        phrase = claim.get("phrase") if isinstance(claim, dict) else None
        variable_ids = (
            claim.get("variable_ids") if isinstance(claim, dict) else None
        )
        if (
            not isinstance(phrase, str) or not phrase
            or not isinstance(variable_ids, list) or not variable_ids
            or not all(
                isinstance(value, str) and value for value in variable_ids
            )
        ):
            issues.append("Paper Content Contract.decision_claims 条目非法")
            continue
        declared.setdefault(phrase, []).extend(variable_ids)
    roles = _decision_manifest_roles(root)
    for relative, text in sorted(texts.items()):
        for phrase in phrases:
            if phrase not in text:
                continue
            if phrase not in declared:
                issues.append(
                    f"undeclared_decision_claim: {relative}: {phrase}"
                )
                continue
            phantom = sorted({
                variable_id for variable_id in declared[phrase]
                if roles.get(variable_id) != "decision"
            })
            if phantom:
                issues.append(
                    f"phantom_decision_variable: {relative}: "
                    f"{phrase} -> {phantom}"
                )
    return issues


def _reproduction_chain_issues(root: Path, contract: dict) -> list[str]:
    """Headline claim bindings must name an existing regenerator script."""
    if contract.get("require_regenerator_for_headline_claims") is not True:
        return []
    bindings = read_json(root / "config" / "claim_bindings.json", {})
    claims = bindings.get("claims", {}) if isinstance(bindings, dict) else {}
    if not isinstance(claims, dict):
        return []
    issues: list[str] = []
    for claim_id, binding in sorted(claims.items()):
        if not isinstance(binding, dict) or binding.get("headline") is not True:
            continue
        regenerator = binding.get("regenerator")
        if not isinstance(regenerator, str) or not regenerator:
            issues.append(
                f"broken_reproduction_chain: {claim_id}: regenerator missing"
            )
            continue
        try:
            script = safe_relative(root, regenerator)
        except ValueError as exc:
            issues.append(f"broken_reproduction_chain: {claim_id}: {exc}")
            continue
        if not script.is_file():
            issues.append(
                f"broken_reproduction_chain: {claim_id}: "
                f"regenerator script missing: {regenerator}"
            )
    return issues


def _appendix_policy_issues(contract: dict, body: str) -> list[str]:
    labels = contract.get("appendix_only_labels", [])
    if not isinstance(labels, list):
        return ["Paper Content Contract.appendix_only_labels 非法"]
    headings = [match.group(2).casefold() for match in _HEADING_RE.finditer(body)]
    return [
        f"appendix-only content appears in body: {label}"
        for label in labels
        if isinstance(label, str) and label
        and any(label.casefold() in heading for heading in headings)
    ]


def audit_paper_content(root: Path) -> list[str]:
    """Audit exposition shape, verified support, placement and detailed artifacts."""
    root = root.resolve()
    loaded = _contract(root)
    if loaded is None:
        return []
    path, contract = loaded
    issues: list[str] = []
    warnings: list[str] = []
    matrix = read_json(root / "paper" / "content_coverage.json")
    requirements = _requirements(root)
    expected = {
        key: value for key, value in requirements.items()
        if value.get("mandatory") is True
    }
    results: list[dict] = []
    required_count = 0
    passed_count = 0

    if not isinstance(matrix, dict) or matrix.get("schema") != 1:
        issues.append("paper content coverage missing or schema is not 1")
        matrix = {}
    if matrix.get("contract") != path.relative_to(root).as_posix():
        issues.append("paper content coverage contract path is stale")
    if matrix.get("contract_sha256") != sha256(path):
        issues.append("paper content coverage contract hash is stale")
    body_relative = contract["body"]
    appendices = contract["appendices"]
    documents: dict[str, str] = {}
    for relative in [body_relative, *appendices]:
        try:
            target = safe_relative(root, relative)
        except ValueError as exc:
            issues.append(str(exc))
            continue
        if not target.is_file():
            issues.append(f"paper content document missing: {relative}")
            continue
        documents[relative] = target.read_text(encoding="utf-8")
    body = documents.get(body_relative, "")
    issues.extend(_appendix_policy_issues(contract, body))
    claim_texts = {body_relative: body}
    draft_path = root / "paper" / "draft.md"
    if draft_path.is_file():
        claim_texts.setdefault(
            "paper/draft.md", draft_path.read_text(encoding="utf-8")
        )
    issues.extend(_decision_claim_issues(root, contract, claim_texts))
    issues.extend(_reproduction_chain_issues(root, contract))

    matrix_requirements = matrix.get("requirements", {})
    if not isinstance(matrix_requirements, dict):
        issues.append("paper content coverage.requirements invalid")
        matrix_requirements = {}
    evidence = read_json(root / ".harness" / "evidence.json", {})
    nodes = evidence.get("nodes", {}) if isinstance(evidence, dict) else {}
    nodes = nodes if isinstance(nodes, dict) else {}

    for requirement_id, requirement in expected.items():
        record = matrix_requirements.get(requirement_id)
        expected_specs = {
            item["kind"]: item
            for item in required_obligations(contract, requirement)
        }
        obligations = record.get("obligations", {}) if isinstance(record, dict) else {}
        if not isinstance(record, dict):
            issues.append(f"{requirement_id}: paper content coverage missing")
        if not isinstance(obligations, dict):
            issues.append(f"{requirement_id}: obligations invalid")
            obligations = {}
        actual_kinds = set(obligations)
        expected_kinds = set(expected_specs)
        if actual_kinds != expected_kinds:
            issues.append(
                f"{requirement_id}: obligation set stale; expected "
                f"{sorted(expected_kinds)}, got {sorted(actual_kinds)}"
            )
        for kind, spec in expected_specs.items():
            required_count += 1
            prefix = f"{requirement_id}.{kind}"
            item = obligations.get(kind)
            item_issues: list[str] = []
            if not isinstance(item, dict):
                item_issues.append("coverage item missing")
            else:
                placement = spec.get("placement", "body")
                target = item.get("target")
                allowed = {
                    "body": {body_relative},
                    "appendix": set(appendices),
                    "either": {body_relative, *appendices},
                }.get(placement)
                if allowed is None:
                    item_issues.append(f"invalid placement: {placement}")
                    allowed = set()
                if target not in allowed:
                    item_issues.append(
                        f"target {target!r} violates placement {placement}"
                    )
                text = documents.get(target, "") if isinstance(target, str) else ""
                section = item.get("section")
                section_text = (
                    _section_text(text, section)
                    if isinstance(section, str) else None
                )
                if section_text is None:
                    item_issues.append(f"section missing: {section!r}")
                    section_text = ""
                anchors = item.get("anchors")
                if not isinstance(anchors, list) or not anchors or not all(
                    isinstance(anchor, str) and anchor for anchor in anchors
                ):
                    item_issues.append("anchors must be a non-empty string array")
                    anchors = []
                for anchor in anchors:
                    if anchor.casefold() not in section_text.casefold():
                        item_issues.append(f"anchor missing from section: {anchor}")
                formats = spec.get("formats", ["prose"])
                if not isinstance(formats, list) or not formats:
                    item_issues.append("formats invalid")
                else:
                    for format_name in formats:
                        if not isinstance(format_name, str) or not _has_format(
                            section_text, format_name
                        ):
                            item_issues.append(
                                f"required representation missing: {format_name}"
                            )
                evidence_ids = item.get("evidence_ids")
                minimum = spec.get("min_evidence", 1)
                if not isinstance(evidence_ids, list) or not all(
                    isinstance(evidence_id, str) and evidence_id
                    for evidence_id in evidence_ids
                ):
                    item_issues.append("evidence_ids invalid")
                    evidence_ids = []
                if not isinstance(minimum, int) or minimum < 0:
                    item_issues.append("min_evidence invalid")
                elif len(set(evidence_ids)) < minimum:
                    item_issues.append(
                        f"needs at least {minimum} verified evidence id(s)"
                    )
                for evidence_id in evidence_ids:
                    if not _evidence_valid(nodes, evidence_id):
                        item_issues.append(
                            f"evidence is not verified and fresh: {evidence_id}"
                        )
                fields = spec.get("disclosure_fields", [])
                disclosure = item.get("disclosure")
                if not isinstance(fields, list):
                    item_issues.append("disclosure_fields invalid")
                    fields = []
                if fields:
                    if not isinstance(disclosure, dict):
                        item_issues.append("validation disclosure missing")
                        disclosure = {}
                    missing_fields = [
                        field for field in fields
                        if not isinstance(disclosure.get(field), str)
                        or not disclosure[field].strip()
                    ]
                    if missing_fields:
                        item_issues.append(
                            f"disclosure fields missing: {missing_fields}"
                        )
                if spec.get("requires_body_reference") is True:
                    body_anchor = item.get("body_anchor")
                    if not isinstance(body_anchor, str) or not body_anchor.strip():
                        item_issues.append("body reference missing")
                    elif body_anchor.casefold() not in body.casefold():
                        item_issues.append(
                            f"body reference anchor missing: {body_anchor}"
                        )
                item_issues.extend(_audit_artifact(
                    root, item.get("artifact"), spec.get("artifact_contract")
                ))
            if item_issues:
                issues.extend(f"{prefix}: {message}" for message in item_issues)
            else:
                passed_count += 1
            results.append({
                "requirement_id": requirement_id,
                "kind": kind,
                "passed": not item_issues,
                "issues": item_issues,
            })

    page_target = contract.get("page_target", {})
    render = read_json(root / "paper" / "render_report.json", {})
    page_count = render.get("page_count") if isinstance(render, dict) else None
    if isinstance(page_target, dict) and isinstance(page_count, int):
        minimum = page_target.get("min")
        maximum = page_target.get("max")
        if isinstance(minimum, int) and isinstance(maximum, int) and not (
            minimum <= page_count <= maximum
        ):
            warnings.append(
                f"正文页数 {page_count}，不在建议的 {minimum}–{maximum} 页范围内；"
                "页数是软目标，语义义务仍按硬 Gate 审计。"
            )

    report = {
        "schema": 1,
        "created_at": now(),
        "profile": _profile(root).get("name"),
        "contract": path.relative_to(root).as_posix(),
        "contract_sha256": sha256(path),
        "verdict": "PASS" if not issues else "FAIL",
        "summary": {
            "required": required_count,
            "passed": passed_count,
            "coverage_rate": (
                passed_count / required_count if required_count else 1.0
            ),
        },
        "page_target": {**page_target, "actual": page_count}
        if isinstance(page_target, dict) else {"actual": page_count},
        "warnings": warnings,
        "issues": issues,
        "obligations": results,
    }
    atomic_write_json(root / "results" / "paper_coverage.json", report)
    return issues
