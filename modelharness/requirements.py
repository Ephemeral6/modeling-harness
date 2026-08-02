"""Source segmentation and requirement-ledger mechanical audits."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from .contracts import safe_relative
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


def extract_sources(root: Path, passes: int = 2) -> dict:
    """Create a deterministic two-pass source-segmentation baseline."""
    if passes < 1:
        raise ValueError("requirements extract passes must be positive")
    root = root.resolve()
    directory = root / "problem" / "data_raw"
    artifacts = [
        path.relative_to(root).as_posix()
        for path in sorted(directory.glob("*"))
        if path.is_file()
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


def audit_segmentation(root: Path) -> list[str]:
    root = root.resolve()
    ledger = read_json(root / "problem" / "source_segmentation.json")
    if not isinstance(ledger, dict) or ledger.get("schema") != 1:
        return ["source_segmentation 缺失或 schema 非法"]
    errors: list[str] = []
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
            if (
                markers
                and segment.get("disposition") != "requirement"
                and (
                    not str(segment.get("reason", "")).strip()
                    or not str(segment.get("override_review", "")).strip()
                )
            ):
                errors.append(
                    f"命中建模标记但无 requirement/独立 override: {actual.strip()}"
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
