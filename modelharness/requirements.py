"""Source segmentation and requirement-ledger mechanical audits."""
from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any

from .claims import GENERATOR, json_pointer
from .contracts import safe_relative
from .safeeval import evaluate
from .storage import atomic_write_json, read_json
from .util import sha256


MODEL_MARKERS: tuple[str, ...] = (
    "请", "试", "确定", "估算", "给出", "讨论", "建立", "要求", "应", "需", "必须",
    "不低于", "不超过", "不少于", "不多于", "高于", "低于", "至多", "至少",
    "以上", "以下", "之间", "范围",
    "约", "大约", "波动", "概率", "平均", "允许", "不允许", "不能", "可调",
    "优化", "最大", "最小", "最优", "最少", "尽量",
)

DISPOSITIONS = {"requirement", "background", "data", "prohibition", "format"}
# Where a non-requirement disposition on a marker-hit segment is registered.
OVERRIDE_LEDGER = "problem/source_segmentation.json"
OVERRIDE_FIELDS: tuple[str, ...] = ("reason", "override_review")
# A segment override and a not_applicable requirement both waive a mandatory
# obligation, so both are held to the one review shape defined by
# templates/reviews/README.md. Prose is not a review at either layer.
REVIEW_SHAPE_HINT = (
    '{"verdict": "APPROVE", "reviewer": "<独立审核者>"} 或指向 reviews/*.json '
    "的项目内相对路径（该文件同样需 verdict=APPROVE 且 reviewer 非空）"
)
REQUIREMENT_TYPES = {
    "answer", "constraint", "model_condition", "data_input", "delivery",
    "prohibition",
}
REQUIREMENT_STATUSES = {"open", "satisfied", "not_applicable"}
EXPECTED_KINDS = {
    "number", "interval", "tuple", "set", "schedule", "policy", "checklist",
    "predicate",
}
_BREAK_RE = re.compile(r"[。！？；\n]")
_ITEM_RE = re.compile(
    r"(?m)^[ \t]*(?:问题[ \t]*[0-9]+|[（(][0-9]+[）)]|[0-9]+\.)[ \t]*"
)


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def segment_source(text: str) -> list[dict]:
    """Deterministically split at fixed punctuation and numbered items."""
    if not isinstance(text, str):
        raise TypeError("source text must be a string")
    boundaries = {0, len(text)}
    boundaries.update(match.end() for match in _BREAK_RE.finditer(text))
    boundaries.update(match.start() for match in _ITEM_RE.finditer(text))
    ordered = sorted(boundaries)
    records = []
    for start, end in zip(ordered, ordered[1:]):
        value = text[start:end]
        if not value:
            continue
        markers = [marker for marker in MODEL_MARKERS if marker in value]
        records.append({
            "id": f"s{len(records) + 1:04d}",
            "span": [start, end],
            "text": value,
            "text_sha256": _text_hash(value),
            "markers": markers,
        })
    return records


def _source_record(root: Path, artifact: str) -> dict:
    path = safe_relative(root, artifact)
    text = path.read_text(encoding="utf-8")
    segments = segment_source(text)
    for item in segments:
        if item["markers"]:
            item.update({
                "disposition": "requirement",
                "requirement_ids": [],
            })
        else:
            item.update({
                "disposition": "background",
                "reason": "机械分句未命中建模标记，待 S0 语义分类确认",
            })
    return {
        "artifact": artifact,
        "sha256": sha256(path),
        "segments": segments,
    }


def _gap_context(text: str, start: int, end: int) -> str:
    for segment in segment_source(text):
        span = segment["span"]
        if span[1] > start and span[0] < end:
            return segment["text"].strip()
    return text[start:end].strip()


def _decodable(path: Path) -> bool:
    """True when the artifact is UTF-8 text that sentence splitting accepts.

    Intake keeps every attachment verbatim, including the official PDF that
    almost every competition package ships, so ``data_raw`` routinely holds
    bytes that are not text at all.  Segmentation only claims authority over
    text; a binary attachment is recorded as skipped rather than crashing the
    whole extraction.
    """
    try:
        path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False
    return True


def extract_sources(root: Path, passes: int = 2) -> dict:
    """Create a deterministic two-pass source-segmentation baseline."""
    if passes < 1:
        raise ValueError("requirements extract passes must be positive")
    root = root.resolve()
    directory = root / "problem" / "data_raw"
    present = [path for path in sorted(directory.glob("*")) if path.is_file()]
    artifacts = [
        path.relative_to(root).as_posix()
        for path in present
        if _decodable(path)
    ]
    skipped = [
        {
            "artifact": path.relative_to(root).as_posix(),
            "sha256": sha256(path),
            "reason": "非 UTF-8 文本附件，机械分句不适用；语义内容须由同题文本件承载",
        }
        for path in present
        if not _decodable(path)
    ]
    if not artifacts:
        raise ValueError("problem/data_raw 中没有可分句源文件")
    runs = []
    for index in range(passes):
        presented = artifacts if index % 2 == 0 else list(reversed(artifacts))
        records = {
            artifact: _source_record(root, artifact)
            for artifact in presented
        }
        segment_number = 1
        for artifact in artifacts:
            for segment in records[artifact]["segments"]:
                segment["id"] = f"s{segment_number:04d}"
                segment_number += 1
        runs.append({
            "schema": 1,
            "sources": [records[artifact] for artifact in artifacts],
        })
    diff = extract_diff(root, runs[0], runs[1]) if len(runs) > 1 else {
        "requirement_ids": [],
        "dispositions": [],
    }
    needs_review = {
        item["segment_id"]
        for values in diff.values()
        for item in values
        if isinstance(item, dict) and "segment_id" in item
    }
    result = runs[0]
    result["passes"] = passes
    result["skipped_sources"] = skipped
    result["extraction_diff"] = diff
    result["needs_review"] = bool(needs_review)
    for source in result["sources"]:
        for segment in source["segments"]:
            if segment["id"] in needs_review:
                segment["needs_review"] = True
    atomic_write_json(
        root / "problem" / "source_segmentation.json",
        result,
    )
    return result


def _review_is_approved(root: Path, review: Any) -> bool:
    """True only for an APPROVE record that names its reviewer.

    The record may be inline or a project-relative path to the reviews/*.json
    file holding it. Free text never qualifies. Both the requirement-level
    not_applicable exemption and the segment-level disposition override route
    through here so that neither waiver can be bought with prose.
    """
    if isinstance(review, dict):
        return (
            str(review.get("verdict", "")).upper() == "APPROVE"
            and bool(str(review.get("reviewer", "")).strip())
        )
    if isinstance(review, str) and review.strip():
        # Free text lands here too; it resolves to a path that does not exist
        # (or refuses to resolve at all), which is exactly a rejection.
        try:
            data = read_json(safe_relative(root, review.strip()))
        except (OSError, ValueError, RuntimeError):
            return False
        return isinstance(data, dict) and _review_is_approved(root, data)
    return False


def _segment_override_defects(root: Path, segment: dict) -> list[str]:
    """Name the override fields that are absent or too weak to count."""
    satisfied = {
        "reason": bool(str(segment.get("reason", "")).strip()),
        "override_review": _review_is_approved(
            root, segment.get("override_review")
        ),
    }
    return [field for field in OVERRIDE_FIELDS if not satisfied[field]]


def _review_defect(root: Path, review: Any) -> str:
    """Say why this override_review value is not a review, in its own terms."""
    if review is None or (isinstance(review, str) and not review.strip()):
        return "当前 override_review 缺失；"
    if isinstance(review, dict):
        faults = []
        if str(review.get("verdict", "")).upper() != "APPROVE":
            faults.append(f"verdict={review.get('verdict')!r} 不是 APPROVE")
        if not str(review.get("reviewer", "")).strip():
            faults.append("reviewer 为空")
        return f"当前 inline 评审记录 {'、'.join(faults)}；"
    if not isinstance(review, str):
        return (
            f"当前 override_review 类型是 {type(review).__name__}，"
            "既不是评审记录也不是路径；"
        )
    value = review.strip()
    try:
        target = safe_relative(root, value)
    except (OSError, ValueError):
        target = None
    if target is not None and target.is_file():
        return f"{value} 存在，但内容不是 APPROVE 且署名 reviewer 的评审记录；"
    if value.endswith(".json") and not any(ch.isspace() for ch in value):
        return f"override_review 指向的评审文件不存在: {value}；"
    return (
        f"当前 override_review 是自由文本（{value[:30]}），"
        "自由文本不构成独立评审；"
    )


def _override_review_hint(root: Path, segment: dict) -> str:
    detail = _review_defect(root, segment.get("override_review"))
    return f"（{detail}override_review 必须是 {REVIEW_SHAPE_HINT}）"


def audit_segmentation(root: Path) -> list[str]:
    root = root.resolve()
    ledger = read_json(root / "problem" / "source_segmentation.json")
    if not isinstance(ledger, dict) or ledger.get("schema") != 1:
        return ["source_segmentation 缺失或 schema 非法"]
    errors: list[str] = []
    seen_segment_ids: set[str] = set()
    for source in ledger.get("sources", []):
        artifact = str(source.get("artifact", ""))
        try:
            path = safe_relative(root, artifact)
        except ValueError:
            errors.append(f"source artifact 越界: {artifact}")
            continue
        if not path.is_file():
            errors.append(f"source artifact 缺失: {artifact}")
            continue
        text = path.read_text(encoding="utf-8")
        if source.get("sha256") != sha256(path):
            errors.append(f"source_segmentation stale: {artifact}")
        cursor = 0
        for segment in sorted(
            source.get("segments", []),
            key=lambda item: item.get("span", [0, 0])[0],
        ):
            segment_id = segment.get("id")
            if segment_id in seen_segment_ids:
                errors.append(f"segment id 重复: {segment_id}")
            elif isinstance(segment_id, str):
                seen_segment_ids.add(segment_id)
            span = segment.get("span")
            if (
                not isinstance(span, list)
                or len(span) != 2
                or not all(isinstance(value, int) for value in span)
                or span[0] < cursor
                or span[1] < span[0]
                or span[1] > len(text)
            ):
                errors.append(
                    f"segment span 非法或重叠: {artifact}/{segment.get('id')}"
                )
                continue
            gap = text[cursor:span[0]]
            if gap.strip():
                errors.append(
                    f"未分配原文片段: "
                    f"{_gap_context(text, cursor, span[0])}"
                )
            actual = text[span[0]:span[1]]
            if segment.get("text_sha256") != _text_hash(actual):
                errors.append(
                    f"segment text_sha256 stale: {artifact}/{segment.get('id')}"
                )
            markers = [
                marker for marker in MODEL_MARKERS if marker in actual
            ]
            # Both fields are read off this very segment in
            # problem/source_segmentation.json; name them so the fix has a
            # landing spot instead of an unlocatable "override".
            defects = _segment_override_defects(root, segment)
            if (
                markers
                and segment.get("disposition") != "requirement"
                and defects
            ):
                hint = (
                    _override_review_hint(root, segment)
                    if "override_review" in defects else ""
                )
                errors.append(
                    f"命中建模标记但无 requirement/独立 override: "
                    f"{actual.strip()}"
                    f"（segment={segment.get('id')}，artifact={artifact}）；"
                    f"如确属背景，请在 {OVERRIDE_LEDGER} 的该 segment 上补齐 "
                    f"{'、'.join(defects)} 字段登记 override{hint}；"
                    f"否则把 disposition 改为 requirement 并回填 "
                    f"requirement_ids"
                )
            if segment.get("disposition") not in DISPOSITIONS:
                errors.append(
                    f"segment disposition 非法: {artifact}/{segment.get('id')}"
                )
            cursor = max(cursor, span[1])
        tail = text[cursor:]
        if tail.strip():
            errors.append(
                f"未分配原文片段: "
                f"{_gap_context(text, cursor, len(text))}"
            )
    if ledger.get("needs_review"):
        errors.append("source_segmentation 仍有 needs_review 差异")
    return sorted(set(errors))


def _segment_index(value: dict) -> dict[str, dict]:
    return {
        segment["id"]: segment
        for source in value.get("sources", [])
        for segment in source.get("segments", [])
        if isinstance(segment, dict) and isinstance(segment.get("id"), str)
    }


def extract_diff(root: Path, pass_a: dict, pass_b: dict) -> dict:
    del root
    left, right = _segment_index(pass_a), _segment_index(pass_b)
    ids = sorted(set(left) | set(right))
    requirement_ids = []
    dispositions = []
    for segment_id in ids:
        a, b = left.get(segment_id, {}), right.get(segment_id, {})
        a_ids = sorted(a.get("requirement_ids", []))
        b_ids = sorted(b.get("requirement_ids", []))
        if a_ids != b_ids:
            requirement_ids.append({
                "segment_id": segment_id,
                "pass_a": a_ids,
                "pass_b": b_ids,
            })
        if a.get("disposition") != b.get("disposition"):
            dispositions.append({
                "segment_id": segment_id,
                "pass_a": a.get("disposition"),
                "pass_b": b.get("disposition"),
            })
    return {
        "requirement_ids": requirement_ids,
        "dispositions": dispositions,
    }


def monotone_nondecreasing(values: Any) -> bool:
    try:
        items = list(values)
        return all(left <= right for left, right in zip(items, items[1:]))
    except (TypeError, ValueError):
        return False


def monotone_nonincreasing(values: Any) -> bool:
    try:
        items = list(values)
        return all(left >= right for left, right in zip(items, items[1:]))
    except (TypeError, ValueError):
        return False


def within(value: Any, lower: Any, upper: Any) -> bool:
    try:
        return lower <= value <= upper
    except TypeError:
        return False


def sums_to(values: Any, target: Any, tolerance: float = 0) -> bool:
    try:
        return math.isclose(
            float(sum(values)),
            float(target),
            rel_tol=0,
            abs_tol=float(tolerance),
        )
    except (TypeError, ValueError):
        return False


def covers(container: Any, required: Any) -> bool:
    if (
        isinstance(container, dict)
        and isinstance(required, dict)
        and {"lower", "upper"} <= set(container)
        and {"lower", "upper"} <= set(required)
    ):
        return (
            container["lower"] <= required["lower"]
            and container["upper"] >= required["upper"]
        )
    try:
        return set(required) <= set(container)
    except TypeError:
        return False


_PREDICATE_FUNCTIONS = {
    "monotone_nondecreasing": monotone_nondecreasing,
    "monotone_nonincreasing": monotone_nonincreasing,
    "within": within,
    "sums_to": sums_to,
    "covers": covers,
    "len": len,
}
_DICT_ACCESS_RE = re.compile(
    r"""x\[['"]([A-Za-z_][A-Za-z0-9_]*)['"]\]"""
)


def _predicate_value(
    root: Path, expected: dict, claim_value: Any
) -> tuple[bool, Any]:
    artifact = expected.get("artifact")
    if not artifact:
        return True, claim_value
    try:
        path = safe_relative(root, str(artifact))
    except ValueError:
        return False, None
    if not path.is_file():
        return False, None
    return json_pointer(
        read_json(path),
        str(expected.get("pointer", "")),
    )


def _evaluate_predicate(expression: str, value: Any) -> bool:
    names: dict[str, Any] = {"x": value}
    if isinstance(value, dict):
        names.update({
            key: item
            for key, item in value.items()
            if isinstance(key, str) and key.isidentifier()
        })

    def replace(match: re.Match) -> str:
        key = match.group(1)
        name = f"x_{key}"
        names[name] = value.get(key) if isinstance(value, dict) else None
        return name

    normalized = _DICT_ACCESS_RE.sub(replace, expression)
    # Keep safeeval's AST whitelist narrow while supporting the documented
    # conjunction form as a sequence of independently safe comparisons.
    clauses = re.split(r"\s+and\s+", normalized.strip())
    return bool(clauses) and all(
        bool(evaluate(clause, names, functions=_PREDICATE_FUNCTIONS))
        for clause in clauses
    )


def _kind_matches(kind: str, value: Any, fields: list[str]) -> bool:
    number = (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )
    if kind == "number":
        return number
    if kind in {"interval", "tuple"}:
        return (
            isinstance(value, dict)
            and all(field in value for field in fields)
        )
    if kind == "set":
        return isinstance(value, (list, tuple, set, dict))
    if kind == "schedule":
        return isinstance(value, (list, dict))
    if kind in {"policy", "checklist"}:
        return isinstance(value, dict)
    if kind == "predicate":
        return isinstance(value, bool)
    return False


def _not_applicable_is_reviewed(root: Path, requirement: dict) -> bool:
    if not str(requirement.get("reason", "")).strip():
        return False
    return _review_is_approved(
        root,
        requirement.get("independent_review", requirement.get("review")),
    )


def _claim_contract_errors(
    root: Path,
    requirement_id: str,
    requirement: dict,
    claim_id: str,
    bindings: dict,
    values: dict,
) -> list[str]:
    errors: list[str] = []
    binding = bindings.get(claim_id)
    claim = values.get(claim_id)
    expected = requirement.get("expected", {})
    if not isinstance(binding, dict):
        return [f"{requirement_id} 未闭合: claim binding 缺失 {claim_id}"]
    if requirement_id not in binding.get("requirement_ids", []):
        errors.append(
            f"{requirement_id} 未闭合: {claim_id} 未反向绑定 requirement"
        )
    if not isinstance(claim, dict) or claim.get("status") != "valid":
        errors.append(f"{requirement_id} 未闭合: claim value 无效 {claim_id}")
        return errors
    kind = expected.get("kind")
    if binding.get("value_type") != kind:
        errors.append(
            f"{requirement_id} kind 不匹配: expected={kind}, "
            f"binding={binding.get('value_type')}"
        )
    fields = expected.get("fields", [])
    value = claim.get("source_value")
    if (
        kind in EXPECTED_KINDS
        and not _kind_matches(
            str(kind),
            value,
            fields if isinstance(fields, list) else [],
        )
    ):
        errors.append(f"{requirement_id} kind/fields 不匹配: {claim_id}")

    conditioned_on = expected.get("conditioned_on")
    if conditioned_on:
        parent_claim = binding.get("conditioned_on_claim")
        parent_requirement = None
        all_requirements = read_json(
            root / "problem" / "requirements.json", {}
        ).get("requirements", {})
        if isinstance(all_requirements, dict):
            parent_requirement = all_requirements.get(conditioned_on)
        allowed = (
            parent_requirement.get("claim_ids", [])
            if isinstance(parent_requirement, dict) else []
        )
        parent_binding = bindings.get(parent_claim)
        if (
            parent_claim not in allowed
            or not isinstance(parent_binding, dict)
            or not binding.get("scenario_id")
            or binding.get("scenario_id") != parent_binding.get("scenario_id")
        ):
            errors.append(
                f"{requirement_id} scenario/conditioned_on 不匹配"
            )

    predicate = expected.get("predicate")
    if predicate:
        found, predicate_value = _predicate_value(root, expected, value)
        try:
            passed = found and _evaluate_predicate(
                str(predicate), predicate_value
            )
        except (ArithmeticError, SyntaxError, TypeError, ValueError):
            passed = False
        if not passed:
            errors.append(f"{requirement_id} predicate 未通过: {predicate}")
    return errors


def audit_requirement_extraction(root: Path) -> list[str]:
    """Audit the S0 source-segment to requirement-ledger mapping."""
    root = root.resolve()
    ledger = read_json(root / "problem" / "requirements.json")
    segmentation = read_json(root / "problem" / "source_segmentation.json")
    if not isinstance(ledger, dict) or ledger.get("schema") != 1:
        return ["requirements extraction 缺失 problem/requirements.json"]
    requirements = ledger.get("requirements")
    if not isinstance(requirements, dict):
        return ["requirements extraction 中 requirements 非对象"]
    if not isinstance(segmentation, dict) or segmentation.get("schema") != 1:
        return ["requirements extraction 缺失 source_segmentation"]
    errors = audit_segmentation(root)
    segments = _segment_index(segmentation)
    for segment_id, segment in segments.items():
        if segment.get("disposition") != "requirement":
            continue
        ids = segment.get("requirement_ids", [])
        if not isinstance(ids, list) or not ids:
            errors.append(f"{segment_id} requirement segment 未映射")
            continue
        for requirement_id in ids:
            if requirement_id not in requirements:
                errors.append(
                    f"{segment_id} 映射未知 requirement: {requirement_id}"
                )
    for requirement_id, requirement in requirements.items():
        source = (
            requirement.get("source", {})
            if isinstance(requirement, dict) else {}
        )
        for segment_id in source.get("segment_ids", []):
            if segment_id not in segments:
                errors.append(
                    f"{requirement_id} 引用未知 source segment: {segment_id}"
                )
    return sorted(set(errors))


def audit_requirements(root: Path) -> list[str]:
    """Audit mandatory closure, claim types, scenarios, predicates, and source age."""
    root = root.resolve()
    ledger = read_json(root / "problem" / "requirements.json")
    if not isinstance(ledger, dict) or ledger.get("schema") != 1:
        return ["requirements 未闭合: problem/requirements.json 缺失或非法"]
    requirements = ledger.get("requirements")
    if not isinstance(requirements, dict):
        return ["requirements 未闭合: requirements 必须是对象"]
    binding_doc = read_json(root / "config" / "claim_bindings.json", {})
    bindings = (
        binding_doc.get("claims", {})
        if isinstance(binding_doc, dict) else {}
    )
    value_doc = read_json(root / "results" / "claim_values.json", {})
    values = value_doc.get("claims", {}) if isinstance(value_doc, dict) else {}
    generated = (
        isinstance(value_doc, dict)
        and value_doc.get("generator") == GENERATOR
    )
    evidence_doc = read_json(root / ".harness" / "evidence.json")
    evidence_nodes = (
        evidence_doc.get("nodes", {})
        if isinstance(evidence_doc, dict) else None
    )
    errors: list[str] = []
    segmentation = read_json(
        root / "problem" / "source_segmentation.json", {}
    )
    segment_index = (
        _segment_index(segmentation)
        if isinstance(segmentation, dict) else {}
    )
    if ledger.get("source_segmentation"):
        errors.extend(audit_segmentation(root))
    for requirement_id, requirement in requirements.items():
        if not isinstance(requirement, dict):
            errors.append(f"{requirement_id} 未闭合: requirement 非对象")
            continue
        if requirement.get("type") not in REQUIREMENT_TYPES:
            errors.append(f"{requirement_id} type 非法")
        status = requirement.get("status")
        if status not in REQUIREMENT_STATUSES:
            errors.append(f"{requirement_id} status 非法")
        expected = requirement.get("expected")
        if (
            not isinstance(expected, dict)
            or expected.get("kind") not in EXPECTED_KINDS
        ):
            errors.append(f"{requirement_id} expected.kind 非法")
            expected = {}
        source = requirement.get("source", {})
        if isinstance(source, dict) and source.get("segment_ids"):
            source_segments = [
                segment_index.get(segment_id)
                for segment_id in source.get("segment_ids", [])
            ]
            if any(segment is None for segment in source_segments):
                errors.append(f"{requirement_id} source segment 缺失")
            quote_hash = source.get("quote_sha256")
            if quote_hash and all(
                isinstance(segment, dict) for segment in source_segments
            ):
                quote = "".join(
                    str(segment.get("text", ""))
                    for segment in source_segments
                )
                if _text_hash(quote) != quote_hash:
                    errors.append(f"{requirement_id} source quote_sha256 stale")
        mandatory = requirement.get("mandatory") is True
        claim_ids = requirement.get("claim_ids", [])
        if not isinstance(claim_ids, list) or not all(
            isinstance(item, str) for item in claim_ids
        ):
            errors.append(f"{requirement_id} claim_ids 非法")
            claim_ids = []
        if mandatory and status == "not_applicable":
            if not _not_applicable_is_reviewed(root, requirement):
                errors.append(
                    f"{requirement_id} 未闭合: not_applicable 缺 reason/独立 review"
                )
            continue
        if mandatory and (status != "satisfied" or not claim_ids):
            errors.append(f"{requirement_id} 未闭合: mandatory requirement")
        for claim_id in claim_ids:
            if not generated:
                errors.append(
                    f"{requirement_id} 未闭合: claim_values 非生成工件"
                )
            errors.extend(_claim_contract_errors(
                root,
                requirement_id,
                requirement,
                claim_id,
                bindings if isinstance(bindings, dict) else {},
                values if isinstance(values, dict) else {},
            ))
            if evidence_nodes is not None:
                node = evidence_nodes.get(claim_id)
                if (
                    not isinstance(node, dict)
                    or node.get("status") != "verified"
                    or node.get("freshness", "valid") != "valid"
                ):
                    errors.append(
                        f"{requirement_id} 未闭合: evidence/freshness {claim_id}"
                    )
    return sorted(set(errors))
