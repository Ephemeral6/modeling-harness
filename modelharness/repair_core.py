"""Repair contract core: scoped snapshots, defect diffing and pass@k stats."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from .storage import read_json
from .util import sha256

REPAIRS_RELATIVE = ".harness/repairs"
REGRESSION_CORPUS_RELATIVE = "docs/regression_corpus.json"

# 由 harness 机器重新生成的派生物：修复域外变更检查对它们豁免。
# 名单来自仓库内全部机器写盘出口的扫描：
#   paper_ir.compile_paper            -> paper/paper_ir.json, paper/draft.md
#   sanitize.render_final             -> paper/final.md, paper/claim_map.json
#   sanitize.sanitize_report          -> results/delivery_check.json
#   narrative.build_brief             -> paper/delivery_manifest.json
#   narrative_core.build_brief        -> paper/narrative_brief.md
#   claims.evaluate_claims            -> results/claim_values.json
#   paper_content.audit_paper_content -> results/paper_coverage.json
#   optimization.build_result_provenance -> results/result_provenance.json
#   paper.render_pdf                  -> paper/render_report.json,
#                                        paper/final.pdf, logs/paper_render.log
DERIVED_ARTIFACTS: tuple[str, ...] = (
    "logs/paper_render.log",
    "paper/claim_map.json",
    "paper/delivery_manifest.json",
    "paper/draft.md",
    "paper/final.md",
    "paper/final.pdf",
    "paper/narrative_brief.md",
    "paper/paper_ir.json",
    "paper/render_report.json",
    "results/claim_values.json",
    "results/delivery_check.json",
    "results/paper_coverage.json",
    "results/result_provenance.json",
)

_VOLATILE_SUFFIXES = (".lock", ".tmp", ".sqlite3-wal", ".sqlite3-shm")


def repairs_dir(root: Path) -> Path:
    return root.resolve() / ".harness" / "repairs"


def snapshot_excluded(relative: str) -> bool:
    """快照跳过修复记录自身、机器派生物与易变运行时文件。"""
    if relative in DERIVED_ARTIFACTS:
        return True
    if relative == REPAIRS_RELATIVE or relative.startswith(
        REPAIRS_RELATIVE + "/"
    ):
        return True
    if "__pycache__" in relative.split("/"):
        return True
    return relative.endswith(_VOLATILE_SUFFIXES)


def snapshot_project(root: Path) -> dict[str, str]:
    """全项目文件哈希快照，键为 POSIX 相对路径。"""
    root = root.resolve()
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if snapshot_excluded(relative):
            continue
        snapshot[relative] = sha256(path)
    return snapshot


def normalize_scope(scope: list[str] | None) -> list[str]:
    """Canonicalize scope globs to POSIX form; empty scopes are illegal."""
    values: list[str] = []
    for glob in scope or []:
        text = str(glob).replace("\\", "/").strip()
        while text.startswith("./"):
            text = text[2:]
        if text:
            values.append(text)
    if not values:
        raise ValueError("repair scope 至少需要一个 glob")
    return values


def match_scope(relative: str, scope: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(relative, glob) for glob in scope)


def out_of_scope_changes(
    before: dict[str, str], after: dict[str, str], scope: list[str]
) -> list[str]:
    """声明域与回归语料之外的一切新增、修改或删除。"""
    return [
        relative
        for relative in sorted(set(before) | set(after))
        if before.get(relative) != after.get(relative)
        and relative != REGRESSION_CORPUS_RELATIVE
        and not match_scope(relative, scope)
    ]


def new_violations(baseline: list[str], current: list[str]) -> list[str]:
    """Guardrail entries present now but absent from the begin baseline."""
    return sorted(set(current) - set(baseline))


def corpus_cites(data: Any, entry_id: str) -> bool:
    """回归语料中是否存在引用该 finding 条目 id 的条目。"""
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entry_id:
        return False
    return any(
        entry_id in json.dumps(entry, ensure_ascii=False)
        for entry in entries
    )


def load_records(root: Path) -> list[dict]:
    """All schema-1 repair records, ordered by file name (id order)."""
    directory = repairs_dir(root)
    if not directory.is_dir():
        return []
    records: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        value = read_json(path)
        if isinstance(value, dict) and value.get("schema") == 1:
            records.append(value)
    return records


def repair_pass_k(root: Path, k: int) -> float | None:
    """最近 k 次 verify 的全绿比率；不足 k 次时与 pass_all_k 同风格返回 None。"""
    events: list[tuple[str, bool]] = []
    for record in load_records(root):
        verifies = record.get("verifies", [])
        if not isinstance(verifies, list):
            continue
        for report in verifies:
            if isinstance(report, dict):
                events.append(
                    (str(report.get("at", "")), bool(report.get("ok")))
                )
    events.sort(key=lambda item: item[0])
    if k <= 0 or k > len(events):
        return None
    return sum(1 for _, ok in events[-k:] if ok) / k
