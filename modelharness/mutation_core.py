"""Mutation battery: replay red-team mutations and aggregate guard verdicts.

第 11 轮红队对 2023D 项目做了 21 条变异注入，只有 7 条被 14 层护栏抓住
（33.3%），四条篡改交付数字的变异（M06/M09/M10/M11）全部漏网。本模块把
这套攻击常驻化为回归资产：

* :func:`apply_mutation` 按 ``mutations.json`` 里的算子规格对 fixture 副本
  做一处最小注入（文本替换 / 锚点后插入 / JSON 指针改值、取反、追加）；
* :func:`detect` 把 Harness 的全部机械护栏聚合成一个发现列表，每条发现带
  检测器前缀（``paper_ir:`` / ``claims:`` / ``paper_content:`` /
  ``narrative:`` / ``sanitize:`` / ``regression_corpus:``），供电池逐条断言
  ``expected_detectors`` 至少命中其一。

headline_tamper 100% 检出的机制根基是 Paper IR 的 ``{num:}`` 占位符：
直接改 ``paper/draft.md`` 的数字破坏「src 重编译 == draft」的一致性
（draft_out_of_sync）；改 ``results/`` 工件则同时破坏 claim binding 与
predictions/registered.json 的锁定值（claims）以及回归语料的文本-工件锚点
（regression_corpus）；把占位符换成另一口径的合法 binding 则被语料锚点抓住。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .claims import audit_claims, audit_holdout
from .contracts import safe_relative
from .narrative_core import audit_paper as _audit_narrative
from .paper_content import audit_paper_content
from .paper_ir import _claim_records, _decision_statements, compile_paper
from .paper_ir_core import parse_manifest, parse_section, render_document
from .regression_corpus_core import CORPUS_RELATIVE, audit_regression_corpus
from .sanitize import _MARKER_RE, _inject_deferred, sanitize_report
from .storage import atomic_write_json, read_json

OPERATORS = {
    "numeric_swap", "sign_flip", "caliber_swap", "claim_rollback",
    "disclosure_inversion", "unit_swap", "reference_unmark",
}


def load_mutations(path: Path) -> list[dict]:
    """Load and shape-check a mutation battery manifest."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("mutations.json 必须是非空数组")
    for spec in data:
        if not isinstance(spec, dict) or not spec.get("id"):
            raise ValueError("mutation 条目必须是带 id 的对象")
        if spec.get("operator") not in OPERATORS:
            raise ValueError(
                f"{spec['id']}: 未知变异算子 {spec.get('operator')!r}"
            )
    return data


def _pointer_tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(f"JSON 指针必须以 / 开头: {pointer}")
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in pointer[1:].split("/")
    ]


def _edit_json(path: Path, edit: dict) -> None:
    data = read_json(path)
    tokens = _pointer_tokens(str(edit["pointer"]))
    parent: Any = data
    for token in tokens[:-1]:
        parent = parent[int(token)] if isinstance(parent, list) else parent[token]
    leaf = tokens[-1]
    if isinstance(parent, list) and leaf == "-":
        parent.append(edit["value"])
    elif edit.get("negate"):
        key: Any = int(leaf) if isinstance(parent, list) else leaf
        current = parent[key]
        if not isinstance(current, (int, float)) or isinstance(current, bool):
            raise ValueError(f"negate 只能作用于数值: {edit['pointer']}")
        parent[key] = -current
    elif isinstance(parent, list):
        parent[int(leaf)] = edit["value"]
    else:
        parent[leaf] = edit["value"]
    atomic_write_json(path, data)


def _edit_text(path: Path, edit: dict) -> None:
    text = path.read_text(encoding="utf-8")
    if "anchor" in edit:
        anchor = str(edit["anchor"])
        if anchor not in text:
            raise ValueError(
                f"anchor 不在目标文件中: {path.name}: {anchor[:40]!r}"
            )
        text = text.replace(anchor, anchor + str(edit["insert"]), 1)
    else:
        find = str(edit["find"])
        if find not in text:
            raise ValueError(
                f"find 不在目标文件中: {path.name}: {find[:40]!r}"
            )
        count = int(edit.get("count", 0))
        text = text.replace(
            find, str(edit.get("replace", "")), count if count > 0 else -1
        )
    path.write_text(text, encoding="utf-8", newline="\n")


def apply_mutation(root: Path, spec: dict) -> list[str]:
    """Apply one mutation spec to a scratch copy; return touched paths."""
    root = Path(root).resolve()
    operator = spec.get("operator")
    if operator not in OPERATORS:
        raise ValueError(f"未知变异算子: {operator!r}")
    params = spec.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(f"{spec.get('id')}: params 必须是对象")
    edits = params.get("edits")
    if edits is None:
        edits = [{**params, "target": spec.get("target")}]
    touched: list[str] = []
    for edit in edits:
        relative = str(edit.get("target") or spec.get("target") or "")
        path = safe_relative(root, relative)
        if not path.is_file():
            raise ValueError(f"变异目标不存在: {relative}")
        if "pointer" in edit:
            _edit_json(path, edit)
        else:
            _edit_text(path, edit)
        touched.append(relative)
    return touched


def _paper_ir_issues(root: Path) -> list[str]:
    manifest_path = root / "paper" / "src" / "manifest.json"
    if not manifest_path.is_file():
        return []
    report = compile_paper(root, check=True)
    issues = list(report.get("errors", []))
    draft_path = root / "paper" / "draft.md"
    if not draft_path.is_file():
        issues.append("draft_missing: paper/draft.md 未编译")
        return issues
    specs, manifest_errors = parse_manifest(read_json(manifest_path))
    if manifest_errors:
        return issues
    sections: list[dict] = []
    for spec in specs:
        relative = f"paper/src/{spec['file']}"
        path = safe_relative(root, relative)
        if not path.is_file():
            return issues
        sections.append({
            "file": relative,
            "title": spec.get("title"),
            "blocks": parse_section(path.read_text(encoding="utf-8")),
        })
    rendered = render_document(
        sections, _claim_records(root), _decision_statements(root)
    )
    if rendered != draft_path.read_text(encoding="utf-8"):
        issues.append(
            "draft_out_of_sync: paper/draft.md 与 paper/src 重编译结果不一致"
        )
    return issues


def _claims_issues(root: Path) -> list[str]:
    if not (root / "config" / "claim_bindings.json").is_file():
        return []
    paper = (
        "paper/final.md"
        if (root / "paper" / "final.md").is_file() else "paper/draft.md"
    )
    return audit_claims(root, paper) + audit_holdout(root)


def _narrative_issues(root: Path) -> list[str]:
    if not (root / "paper" / "draft.md").is_file():
        return []
    return _audit_narrative(root, "paper/draft.md")


def _delivery_issues(root: Path) -> list[str]:
    draft_path = root / "paper" / "draft.md"
    final_path = root / "paper" / "final.md"
    issues: list[str] = []
    if final_path.is_file() and draft_path.is_file():
        staged = _inject_deferred(
            root, draft_path.read_text(encoding="utf-8")
        )
        if _MARKER_RE.sub("", staged) != final_path.read_text(
            encoding="utf-8"
        ):
            issues.append(
                "final_out_of_sync: paper/final.md 与 paper/draft.md "
                "渲染结果不一致"
            )
    target = "paper/final.md" if final_path.is_file() else "paper/draft.md"
    report = sanitize_report(root, target)
    for violation in report.get("violations", []):
        extra = {
            key: value for key, value in violation.items() if key != "kind"
        }
        issues.append(
            f"{violation['kind']}: "
            f"{json.dumps(extra, ensure_ascii=False, sort_keys=True)}"
            if extra else str(violation["kind"])
        )
    return issues


def _corpus_issues(root: Path) -> list[str]:
    if not (root / CORPUS_RELATIVE).is_file():
        return []
    return audit_regression_corpus(root)


def detect(root: Path) -> list[str]:
    """Aggregate every mechanical guard into one prefixed finding list."""
    root = Path(root).resolve()
    findings: list[str] = []

    def run(detector: str, func: Callable[[], list[str]]) -> None:
        try:
            findings.extend(f"{detector}: {item}" for item in func())
        except Exception as exc:  # noqa: BLE001 — 检测器崩溃本身就是发现
            findings.append(
                f"{detector}: detector_error: {type(exc).__name__}: {exc}"
            )

    run("paper_ir", lambda: _paper_ir_issues(root))
    run("claims", lambda: _claims_issues(root))
    run("paper_content", lambda: audit_paper_content(root))
    run("narrative", lambda: _narrative_issues(root))
    run("sanitize", lambda: _delivery_issues(root))
    run("regression_corpus", lambda: _corpus_issues(root))
    return sorted(set(findings))
