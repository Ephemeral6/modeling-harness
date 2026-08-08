"""I/O facade for the repair begin/verify contract (fix-without-new-defects)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .claims import audit_claims
from .contracts import safe_relative
from .narrative import audit_paper
from .paper_content import audit_paper_content
from .paper_ir import compile_paper
from .repair_core import (
    REGRESSION_CORPUS_RELATIVE,
    corpus_cites,
    new_violations,
    normalize_scope,
    out_of_scope_changes,
    repairs_dir,
    snapshot_project,
)
from .sanitize import sanitize_report
from .storage import CorruptStateError, atomic_write_json, file_lock, read_json
from .util import now, sha256

_REPAIR_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _delivery_entries(root: Path) -> list[str]:
    """Flatten sanitize violations; drop offsets so entries diff stably."""
    report = sanitize_report(root)
    entries: list[str] = []
    for violation in report.get("violations", []):
        payload = {
            key: value for key, value in violation.items()
            if key not in {"kind", "span"}
        }
        entries.append(
            f"{violation.get('kind')}: "
            + json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
    return entries


def _paper_ir_entries(root: Path) -> list[str]:
    if not (root / "paper" / "src" / "manifest.json").is_file():
        return []
    report = compile_paper(root, check=True)
    return [str(error) for error in report.get("errors", [])]


def guardrail_violations(root: Path) -> list[str]:
    """聚合当前可用的机械审计为可差集的护栏违规条目；容忍项目缺某类文件。"""
    root = root.resolve()
    entries: list[str] = []
    auditors = (
        ("claims", lambda: audit_claims(root)),
        ("paper_content", lambda: audit_paper_content(root)),
        ("narrative", lambda: audit_paper(root)),
        ("delivery", lambda: _delivery_entries(root)),
        ("paper_ir", lambda: _paper_ir_entries(root)),
    )
    for name, runner in auditors:
        try:
            entries.extend(f"{name}: {item}" for item in runner())
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            entries.append(
                f"{name}: guardrail_crash: {type(exc).__name__}: {exc}"
            )
    return sorted(set(entries))


def _parse_finding(root: Path, finding: str) -> tuple[Path, str]:
    if "#" not in finding:
        raise ValueError("finding 必须是 <审核文件路径>#<条目id> 形式")
    relative, entry_id = finding.rsplit("#", 1)
    relative, entry_id = relative.strip(), entry_id.strip()
    if not relative or not entry_id:
        raise ValueError("finding 必须是 <审核文件路径>#<条目id> 形式")
    path = safe_relative(root, relative.replace("\\", "/"))
    if not path.is_file():
        raise ValueError(f"审核文件不存在: {relative}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if entry_id not in text:
        raise ValueError(f"审核文件中找不到条目: {entry_id}")
    return path, entry_id


def _allocate_id(directory: Path) -> str:
    index = len(list(directory.glob("R*.json"))) + 1
    while (directory / f"R{index:03d}.json").exists():
        index += 1
    return f"R{index:03d}"


def begin_repair(root: Path, finding: str, scope: list[str]) -> dict:
    """登记一次修复：finding 锚点、写入范围、全项目快照与护栏违规基线。"""
    root = root.resolve()
    finding_path, entry_id = _parse_finding(root, finding)
    globs = normalize_scope(scope)
    baseline = guardrail_violations(root)
    corpus_path = root / REGRESSION_CORPUS_RELATIVE
    directory = repairs_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    with file_lock(directory / "repairs"):
        repair_id = _allocate_id(directory)
        record = {
            "schema": 1,
            "id": repair_id,
            "created_at": now(),
            "finding": {
                "path": finding_path.relative_to(root).as_posix(),
                "sha256": sha256(finding_path),
                "entry_id": entry_id,
            },
            "scope": globs,
            "snapshot": snapshot_project(root),
            "guardrail_baseline": baseline,
            "regression_corpus": {
                "path": REGRESSION_CORPUS_RELATIVE,
                "sha256": (
                    sha256(corpus_path) if corpus_path.is_file() else None
                ),
            },
            "verifies": [],
        }
        atomic_write_json(directory / f"{repair_id}.json", record)
    return record


def _corpus_violation(root: Path, record: dict, notes: list[str]) -> str | None:
    """(c) 段：语料缺失则跳过并注明，否则要求新增引用 finding 的条目。"""
    entry_id = str(record.get("finding", {}).get("entry_id", ""))
    corpus_path = root / REGRESSION_CORPUS_RELATIVE
    if not corpus_path.is_file():
        notes.append(f"{REGRESSION_CORPUS_RELATIVE} 缺失，跳过回归语料检查")
        return None
    try:
        corpus = read_json(corpus_path)
    except CorruptStateError:
        return (
            f"repair_without_regression_entry: {entry_id} "
            f"回归语料损坏，无法核对"
        )
    begin_sha = record.get("regression_corpus", {}).get("sha256")
    if corpus_cites(corpus, entry_id) and sha256(corpus_path) != begin_sha:
        return None
    return (
        f"repair_without_regression_entry: {entry_id} "
        f"需要新增引用该 finding 的语料条目"
    )


def verify_repair(root: Path, repair_id: str) -> dict:
    """三段机械核查：域外改动、新增护栏违规、回归语料条目；结果写回记录。"""
    root = root.resolve()
    if _REPAIR_ID_RE.match(repair_id) is None:
        raise ValueError(f"非法 repair id: {repair_id}")
    path = repairs_dir(root) / f"{repair_id}.json"
    record = read_json(path)
    if not isinstance(record, dict) or record.get("schema") != 1:
        raise ValueError(f"repair 记录缺失或损坏: {repair_id}")
    violations: list[str] = []
    notes: list[str] = []
    for relative in out_of_scope_changes(
        record.get("snapshot", {}),
        snapshot_project(root),
        record.get("scope", []),
    ):
        violations.append(f"out_of_scope_change: {relative}")
    for entry in new_violations(
        record.get("guardrail_baseline", []), guardrail_violations(root)
    ):
        violations.append(f"repair_introduced_defect: {entry}")
    corpus_violation = _corpus_violation(root, record, notes)
    if corpus_violation is not None:
        violations.append(corpus_violation)
    report = {
        "schema": 1,
        "repair_id": repair_id,
        "at": now(),
        "ok": not violations,
        "violations": violations,
        "notes": notes,
    }
    with file_lock(path):
        stored = read_json(path)
        if not isinstance(stored, dict) or stored.get("schema") != 1:
            raise ValueError(f"repair 记录缺失或损坏: {repair_id}")
        verifies = stored.setdefault("verifies", [])
        if not isinstance(verifies, list):
            raise ValueError(f"repair 记录 verifies 段损坏: {repair_id}")
        verifies.append(report)
        atomic_write_json(path, stored)
    return report
