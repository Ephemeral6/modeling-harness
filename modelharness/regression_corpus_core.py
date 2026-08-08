"""Project-agnostic regression corpus: normalized regex, negation, anchors.

第 11 轮红队（RT11-B3）证明纯子串语料可以三路绕过：改写句式把 forbidden_text
的错误说法原样复辟、保留 required_text 短语再在下一句就地否定、以及
artifact_value 与论文文字互不绑定（工件数对、论文数错，语料照样绿）。

本库把项目级 ``checks/l11_regression_corpus.py`` 上提为可复用核心，并封死这三条路：

* 目标文本先做 NFKC 归一化（全角折叠、零宽字符剥离、空白折叠）再按正则匹配，
  换全角数字、改标点、插空白都改变不了判定；
* ``required_text`` 支持 ``negation_window``——短语之后 ``window_chars`` 个
  归一化字符内不得出现任何反悔措辞；
* ``artifact_value`` 支持 ``anchors``——论文锚点之后的第一个数字必须等于工件
  当前值；工件与正文无论各改哪一头都会亮红，锚点本身被删同样亮红。

语料形态（``docs/regression_corpus.json``，schema 1，entries 数组）：

  forbidden_text —— 已判错的措辞不得再出现在 targets 里（regex=true 按正则族）
  required_text  —— 修复的实质措辞必须出现且未被 negation_window 否定
  artifact_value —— artifact#path 上的值必须满足 comparison expected，
                    可选 anchors 把值与论文锚点后的数字双向绑定
"""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .contracts import safe_relative
from .storage import read_json

CORPUS_RELATIVE = "docs/regression_corpus.json"

_ZERO_WIDTH_RE = re.compile("[​‌‍⁠﻿]")
_NUMBER_RE = re.compile(r"[-+]?[0-9]+(?:\.[0-9]+)?")


def normalize_text(text: str) -> str:
    """NFKC-fold, strip zero-width characters and collapse whitespace runs."""
    folded = unicodedata.normalize("NFKC", _ZERO_WIDTH_RE.sub("", text))
    return re.sub(r"\s+", " ", folded)


def dig(node: Any, path: str) -> Any:
    """Resolve a dotted path with numeric segments as list indexes."""
    for part in path.split("."):
        node = node[int(part)] if part.isdigit() else node[part]
    return node


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare(got: Any, expected: Any, comparison: str, tol: float) -> bool:
    if comparison == "eq":
        if _numeric(got) and _numeric(expected):
            return abs(float(got) - float(expected)) <= tol
        return got == expected
    if comparison == "ne":
        if _numeric(got) and _numeric(expected):
            return abs(float(got) - float(expected)) > tol
        return got != expected
    ordered = {
        "lt": lambda a, b: float(a) < float(b),
        "le": lambda a, b: float(a) <= float(b),
        "gt": lambda a, b: float(a) > float(b),
        "ge": lambda a, b: float(a) >= float(b),
    }
    if comparison not in ordered:
        raise ValueError(f"未知比较符: {comparison}")
    return ordered[comparison](got, expected)


def _label(entry: dict) -> str:
    identity = str(entry.get("id", "<无 id>"))
    if entry.get("round") is not None:
        return f"{identity}（第 {entry['round']} 轮）"
    return identity


def _pattern(entry: dict) -> re.Pattern[str]:
    raw = str(entry["pattern"])
    if entry.get("regex"):
        return re.compile(raw)
    return re.compile(re.escape(normalize_text(raw)))


def _normalized(root: Path, relative: str) -> str | None:
    path = safe_relative(root, relative)
    if not path.is_file():
        return None
    return normalize_text(path.read_text(encoding="utf-8"))


def _text_failures(root: Path, entry: dict) -> list[str]:
    label = _label(entry)
    summary = str(entry.get("summary", ""))
    kind = entry["kind"]
    pattern = _pattern(entry)
    window_chars = int(entry.get("window_chars", 40))
    tokens = list(entry.get("negation_window", []))
    failures: list[str] = []
    for relative in entry["targets"]:
        text = _normalized(root, relative)
        if text is None:
            failures.append(f"{label}: 目标文件缺失 {relative}")
            continue
        matches = list(pattern.finditer(text))
        if kind == "forbidden_text" and matches:
            failures.append(
                f"{label}: {relative} 又出现了已判错的措辞 "
                f"{matches[0].group(0)[:40]!r} —— {summary}"
            )
        if kind == "required_text":
            if not matches:
                failures.append(
                    f"{label}: {relative} 丢失了修复内容 "
                    f"{entry['pattern'][:40]!r} —— {summary}"
                )
                continue
            for match in matches:
                window = text[match.end():match.end() + window_chars]
                hits = [token for token in tokens if token in window]
                if hits:
                    failures.append(
                        f"{label}: {relative} 修复措辞被紧随其后否定"
                        f"（{hits}）—— {summary}"
                    )
                    break
    return failures


def _anchor_number_ok(
    text_number: str, value: Any, decimals: Any, tol: float
) -> bool:
    try:
        got = Decimal(text_number)
    except InvalidOperation:
        return False
    if decimals is None:
        try:
            return abs(float(got) - float(value)) <= tol
        except (TypeError, ValueError):
            return False
    try:
        quantum = Decimal("1").scaleb(-int(decimals))
        want = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return got == want


def _anchor_failures(root: Path, entry: dict, value: Any) -> list[str]:
    label = _label(entry)
    summary = str(entry.get("summary", ""))
    tol = float(entry.get("tol", 1e-9))
    failures: list[str] = []
    for anchor in entry.get("anchors", []):
        pattern = re.compile(str(anchor["pattern"]))
        decimals = anchor.get("decimals")
        window_chars = int(anchor.get("window_chars", 40))
        min_count = int(anchor.get("min_count", 1))
        for relative in anchor["targets"]:
            text = _normalized(root, relative)
            if text is None:
                failures.append(f"{label}: 锚点目标文件缺失 {relative}")
                continue
            matches = list(pattern.finditer(text))
            if len(matches) < min_count:
                failures.append(
                    f"{label}: {relative} 锚点缺失 "
                    f"{anchor['pattern']!r} —— {summary}"
                )
                continue
            for match in matches:
                window = text[match.end():match.end() + window_chars]
                number = _NUMBER_RE.search(window)
                if number is None:
                    failures.append(
                        f"{label}: {relative} 锚点 {anchor['pattern']!r} "
                        f"后没有数字 —— {summary}"
                    )
                    continue
                if not _anchor_number_ok(
                    number.group(0), value, decimals, tol
                ):
                    failures.append(
                        f"{label}: {relative} 锚点 {anchor['pattern']!r} "
                        f"后数字 {number.group(0)} 与工件值 {value!r} "
                        f"不一致 —— {summary}"
                    )
    return failures


def _artifact_failures(root: Path, entry: dict) -> list[str]:
    label = _label(entry)
    summary = str(entry.get("summary", ""))
    artifact = str(entry["artifact"])
    path_expr = str(entry["path"])
    try:
        got = dig(
            read_json(safe_relative(root, artifact)), path_expr
        )
    except Exception as exc:  # noqa: BLE001 — 取值失败本身就是发现
        return [
            f"{label}: 取值失败 {artifact}#{path_expr} "
            f"{type(exc).__name__}"
        ]
    failures: list[str] = []
    comparison = str(entry.get("comparison", "eq"))
    tol = float(entry.get("tol", 1e-9))
    if not _compare(got, entry["expected"], comparison, tol):
        failures.append(
            f"{label}: {artifact}#{path_expr} = {got!r}，"
            f"要求 {comparison} {entry['expected']!r} —— {summary}"
        )
    failures.extend(_anchor_failures(root, entry, got))
    return failures


def audit_regression_corpus(
    root: Path, corpus: str = CORPUS_RELATIVE
) -> list[str]:
    """Audit every corpus entry; each failure names the entry it violates."""
    root = root.resolve()
    path = safe_relative(root, corpus)
    if not path.is_file():
        return [f"回归语料缺失: {corpus}"]
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        return [f"回归语料 schema 必须是 1: {corpus}"]
    entries = data.get("entries")
    if not isinstance(entries, list):
        return [f"回归语料 entries 必须是数组: {corpus}"]
    failures: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            failures.append("回归语料条目必须是对象")
            continue
        kind = entry.get("kind")
        try:
            if kind in ("forbidden_text", "required_text"):
                failures.extend(_text_failures(root, entry))
            elif kind == "artifact_value":
                failures.extend(_artifact_failures(root, entry))
            else:
                failures.append(f"{_label(entry)}: 未知的语料类型 {kind}")
        except Exception as exc:  # noqa: BLE001 — 条目损坏必须亮红而非跳过
            failures.append(
                f"{_label(entry)}: 条目核对失败 {type(exc).__name__}: {exc}"
            )
    return failures
