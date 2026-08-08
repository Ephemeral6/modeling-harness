"""Controlled-comparison protocol: manifest contract plus a five-family audit.

A comparison run is only evidence if somebody actually marked the papers.
This module makes "跑完没人判卷" machine-visible: the manifest declares the
arms, the frozen judge and the report that must exist, and
:func:`audit_comparison` refuses to go green until all of it is on disk.

Relative paths inside a manifest resolve against the *protocol root*: the
repository root when the manifest lives in ``<root>/benchmarks/protocols/``,
otherwise the directory holding the manifest (which is what regression
fixtures rely on). Absolute paths — an external baseline that sits outside
the repository — are used verbatim.
"""
from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path

from . import lifecycle
from .provenance_core import scan_against_baselines
from .storage import CorruptStateError, read_json
from .util import write_json

SCHEMA = 1
ARM_KINDS = ("harness", "external_baseline")
TERMINAL_STATUSES = ("completed", "delivered", "abandoned")
COMPLETION_STATUSES = ("completed", "delivered")
REPORT_ARM_FIELDS = ("objective_value", "verdict")
DEFAULT_BASELINE_ID = "comparison_baseline"
ISOLATION_CLAUSE = "arms 不得读取彼此目录"
_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z_.-]+")


# --------------------------------------------------------------- 清单装载


def protocol_root(manifest_path: Path) -> Path:
    """Base directory that relative manifest paths resolve against."""
    parent = Path(manifest_path).resolve().parent
    if parent.name == "protocols" and parent.parent.name == "benchmarks":
        return parent.parent.parent
    return parent


def load_manifest(manifest_path: Path) -> dict:
    """Read one comparison manifest; structural breakage raises."""
    path = Path(manifest_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"对照实验清单不存在: {path}")
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(
            f"对照实验清单 schema 无效（需要 schema={SCHEMA}）: {path}"
        )
    return data


def _resolve(base: Path, value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (base / candidate).resolve()


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _manifest_errors(manifest: dict) -> list[str]:
    errors: list[str] = []
    if not _text(manifest.get("id")):
        errors.append("comparison manifest: 缺少 id")
    problem_ids = manifest.get("problem_ids")
    if not isinstance(problem_ids, list) or not problem_ids:
        errors.append("comparison manifest: problem_ids 必须是非空数组")
    if not _text(manifest.get("isolation")):
        errors.append(
            f"comparison manifest: 缺少 isolation 条款（应声明「{ISOLATION_CLAUSE}」）"
        )
    return errors


def _arms(manifest: dict) -> tuple[list[str], list[dict]]:
    raw = manifest.get("arms")
    if not isinstance(raw, list) or not raw:
        return ["comparison manifest: arms 必须是非空数组"], []
    errors: list[str] = []
    arms: list[dict] = []
    seen: set[str] = set()
    for position, item in enumerate(raw):
        label = f"comparison manifest: arms[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 不是对象")
            continue
        arm_id = _text(item.get("arm_id"))
        if not arm_id:
            errors.append(f"{label} 缺少 arm_id")
            continue
        if arm_id in seen:
            errors.append(f"{label} arm_id 重复: {arm_id}")
            continue
        seen.add(arm_id)
        if item.get("kind") not in ARM_KINDS:
            errors.append(
                f"arm {arm_id}: kind 必须是 {'/'.join(ARM_KINDS)} 之一"
            )
            continue
        arms.append(item)
    return errors, arms


def _harness_projects(base: Path, arms: list[dict]) -> list[tuple[str, Path]]:
    """(arm_id, project path) for harness arms whose directory exists."""
    pairs: list[tuple[str, Path]] = []
    for arm in arms:
        if arm.get("kind") != "harness":
            continue
        declared = _text(arm.get("project_path"))
        if not declared:
            continue
        project = _resolve(base, declared)
        if project.is_dir():
            pairs.append((_text(arm.get("arm_id")), project))
    return pairs


# ------------------------------------------------------ (a) 各臂状态已终结


def _status_errors(base: Path, arms: list[dict]) -> list[str]:
    errors: list[str] = []
    for arm in arms:
        if arm.get("kind") != "harness":
            continue
        arm_id = _text(arm.get("arm_id"))
        declared = _text(arm.get("project_path"))
        if not declared:
            errors.append(f"arm {arm_id}: kind=harness 必须声明 project_path")
            continue
        project = _resolve(base, declared)
        if not project.is_dir():
            errors.append(f"arm {arm_id}: harness 项目路径不存在: {project}")
            continue
        try:
            status = lifecycle.get_status(project)
        except CorruptStateError as exc:
            errors.append(f"arm {arm_id}: 项目状态不可读（{exc}）")
            continue
        if status not in TERMINAL_STATUSES:
            errors.append(
                f"arm {arm_id}: 项目状态为 {status}，对照实验要求 "
                f"{'/'.join(TERMINAL_STATUSES)} 之一（跑完必须收口）"
            )
    return errors


# ------------------------------------------------ (b) 判卷报告存在且覆盖全臂


def _report_rows(data) -> dict[str, dict] | None:
    rows = data.get("arms") if isinstance(data, dict) else None
    if isinstance(rows, dict):
        return {
            key: value for key, value in rows.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
    if isinstance(rows, list):
        indexed: dict[str, dict] = {}
        for item in rows:
            if isinstance(item, dict) and _text(item.get("arm_id")):
                indexed[_text(item["arm_id"])] = item
        return indexed
    return None


def _report_errors(base: Path, manifest: dict, arms: list[dict]) -> list[str]:
    declared = _text(manifest.get("report_path"))
    if not declared:
        return ["comparison report missing: 清单未声明 report_path"]
    path = _resolve(base, declared)
    if not path.is_file():
        return [
            f"comparison report missing: {declared} 不存在"
            "（各臂跑完但从未判卷）"
        ]
    try:
        data = read_json(path)
    except CorruptStateError:
        return [f"comparison report incomplete: {declared} 无法解析 JSON"]
    rows = _report_rows(data)
    if rows is None:
        return [
            f"comparison report incomplete: {declared} 缺少 arms 字段"
            "（数组或以 arm_id 为键的对象）"
        ]
    errors: list[str] = []
    for arm in arms:
        arm_id = _text(arm.get("arm_id"))
        row = rows.get(arm_id)
        if row is None:
            errors.append(
                f"comparison report incomplete: {declared} 未覆盖 arm {arm_id}"
            )
            continue
        for field in REPORT_ARM_FIELDS:
            if row.get(field) in (None, ""):
                errors.append(
                    f"comparison report incomplete: arm {arm_id} "
                    f"缺少 {field} 字段"
                )
    return errors


# -------------------------------------------------------- (c) 基线独立性扫描


def _baseline_groups(manifest: dict) -> tuple[list[str], dict[str, list[dict]]]:
    raw = manifest.get("baseline_artifacts")
    if raw is None:
        return [], {}
    if not isinstance(raw, list):
        return ["comparison manifest: baseline_artifacts 必须是数组"], {}
    errors: list[str] = []
    groups: dict[str, list[dict]] = {}
    for position, item in enumerate(raw):
        label = f"comparison manifest: baseline_artifacts[{position}]"
        if not isinstance(item, dict) or not _text(item.get("sha256")):
            errors.append(f"{label} 缺少 path/sha256")
            continue
        baseline_id = _text(item.get("baseline_id")) or DEFAULT_BASELINE_ID
        groups.setdefault(baseline_id, []).append({
            "path": _text(item.get("path")),
            "sha256": _text(item["sha256"]),
        })
    return errors, groups


def _write_registries(directory: Path, groups: dict[str, list[dict]]) -> None:
    for position, (baseline_id, files) in enumerate(sorted(groups.items())):
        safe = _UNSAFE_NAME.sub("_", baseline_id) or "baseline"
        write_json(directory / f"{position:02d}_{safe}.json", {
            "schema": 1,
            "baseline_id": baseline_id,
            "files": files,
        })


def _leak_errors(
    base: Path, arms: list[dict], groups: dict[str, list[dict]]
) -> list[str]:
    if not groups:
        return []
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix="mh-comparison-") as temp:
        registries = Path(temp)
        _write_registries(registries, groups)
        for arm_id, project in _harness_projects(base, arms):
            try:
                hits = scan_against_baselines(project, registries)
            except CorruptStateError as exc:
                errors.append(
                    f"baseline leak: arm {arm_id} 的 import_manifest 不可解析"
                    f"，独立性不可判（{exc}）"
                )
                continue
            errors.extend(f"baseline leak: arm {arm_id}: {hit}" for hit in hits)
    return errors


# ----------------------------------------------- (d) judge 冻结早于各臂完成


def _moment(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def _completion_moment(project: Path) -> datetime | None:
    """Earliest completed/delivered stamp in status_history, if any."""
    try:
        meta = read_json(project / "modeling-project.json")
    except CorruptStateError:
        return None
    history = meta.get("status_history") if isinstance(meta, dict) else None
    moments: list[datetime] = []
    for item in history if isinstance(history, list) else []:
        if isinstance(item, dict) and item.get("status") in COMPLETION_STATUSES:
            moment = _moment(item.get("at"))
            if moment is not None:
                moments.append(moment)
    return min(moments) if moments else None


def _judge_freeze_errors(
    base: Path, manifest: dict, arms: list[dict]
) -> list[str]:
    judge = manifest.get("judge")
    judge = judge if isinstance(judge, dict) else {}
    frozen = _moment(judge.get("frozen_at"))
    errors: list[str] = []
    if frozen is None:
        errors.append(
            "judge freeze missing: judge.frozen_at 缺失或不可解析，"
            "无法证明判卷参数在各臂完成之前冻结"
        )
    if not _text(judge.get("frozen_params_sha256")):
        errors.append(
            "judge freeze missing: judge.frozen_params_sha256 缺失，"
            "判卷参数未冻结，事后调参不可排除"
        )
    for arm_id, project in _harness_projects(base, arms):
        finished = _completion_moment(project)
        if finished is None:
            errors.append(
                f"judge freeze unverifiable (warning): arm {arm_id} 的 "
                "status_history 没有 completed/delivered 记录，"
                "judge 冻结与该臂完成的先后不可判"
            )
            continue
        if frozen is not None and frozen >= finished:
            errors.append(
                f"judge freeze too late: judge.frozen_at="
                f"{_text(judge.get('frozen_at'))} 不早于 arm {arm_id} 的完成"
                f"时间 {finished.isoformat()}"
            )
    return errors


# ------------------------------------------------- (e) judge 与生成者相互独立


def _judge_independence_errors(manifest: dict, arms: list[dict]) -> list[str]:
    judge = manifest.get("judge")
    worker = _text(judge.get("worker")) if isinstance(judge, dict) else ""
    if not worker:
        return []
    errors: list[str] = []
    for arm in arms:
        if _text(arm.get("producer")) == worker:
            errors.append(
                f"judge independence violated: judge.worker={worker} 同时是 "
                f"arm {_text(arm.get('arm_id'))} 的 producer"
                "（生成者不能批准自己的结论）"
            )
    return errors


# ------------------------------------------------------------------ 汇总


def audit_comparison(manifest_path: Path) -> list[str]:
    """Audit one controlled-comparison protocol; empty list means收口完成.

    Five families of findings:
    (a) every ``kind="harness"`` arm must point at an existing project whose
        lifecycle status已终结（completed / delivered / abandoned）；
    (b) ``report_path`` must exist and score every declared ``arm_id`` with
        both ``objective_value`` and ``verdict``；
    (c) each harness arm is scanned against ``baseline_artifacts`` — a
        byte-identical file without an ``import_manifest`` record is a leak；
    (d) ``judge.frozen_at`` must precede每条 harness 臂的完成时刻；缺
        status_history 时保守降级为 warning 级“不可判”而不是默认通过；
    (e) ``judge.worker`` must not be any arm's ``producer``。
    """
    path = Path(manifest_path).expanduser().resolve()
    manifest = load_manifest(path)
    base = protocol_root(path)
    errors = _manifest_errors(manifest)
    arm_errors, arms = _arms(manifest)
    errors.extend(arm_errors)
    errors.extend(_status_errors(base, arms))
    errors.extend(_report_errors(base, manifest, arms))
    baseline_errors, groups = _baseline_groups(manifest)
    errors.extend(baseline_errors)
    errors.extend(_leak_errors(base, arms, groups))
    errors.extend(_judge_freeze_errors(base, manifest, arms))
    errors.extend(_judge_independence_errors(manifest, arms))
    return errors
