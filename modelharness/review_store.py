"""Append-only review lineage for cross-agent, cross-platform safety."""
from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .contracts import safe_relative, validate_review
from .storage import read_json
from .util import sha256

# 硬不变量 5 在审核层的统一提示语。evidence 层用 SELF_VERIFY_HINT，
# gate 层与终审层用这一条：两者的判定逻辑共用 evidence_core 的实现。
SELF_APPROVE_HINT = (
    "生成者不得自批，请由未参与该产物的 worker 出具审核："
    "modelharness task claim <independent_review 任务> --worker <另一 worker>"
    "，并在审核文件里写明 reviewer 与 task_id"
)


def _logical_parts(root: Path, logical: str) -> tuple[Path, str, str]:
    canonical = safe_relative(root, logical)
    return canonical, canonical.stem, canonical.suffix


def review_candidates(root: Path, logical: str) -> list[tuple[int, Path]]:
    """Return immutable lineage members ordered from oldest to newest."""
    canonical, stem, suffix = _logical_parts(root, logical)
    candidates: dict[int, Path] = {}
    if canonical.is_file():
        candidates[1] = canonical
    pattern = re.compile(
        rf"^{re.escape(stem)}_v([2-9]|[1-9][0-9]+){re.escape(suffix)}$",
        re.IGNORECASE,
    )
    if canonical.parent.is_dir():
        for path in canonical.parent.glob(f"{stem}_v*{suffix}"):
            match = pattern.match(path.name)
            if match and path.is_file():
                candidates[int(match.group(1))] = path
    return sorted(candidates.items())


def relative_review_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def latest_review_path(root: Path, logical: str) -> Path | None:
    candidates = review_candidates(root, logical)
    return candidates[-1][1] if candidates else None


def next_review_path(
    root: Path, logical: str, occupied: list[str] | None = None
) -> str:
    """Allocate a new sibling without replacing any prior review."""
    canonical, stem, suffix = _logical_parts(root, logical)
    versions = {version for version, _path in review_candidates(root, logical)}
    occupied_set = {
        safe_relative(root, item).resolve()
        for item in (occupied or [])
    }
    if not versions and canonical.resolve() not in occupied_set:
        return relative_review_path(root, canonical)
    version = max(versions or {1}) + 1
    while True:
        candidate = canonical.with_name(f"{stem}_v{version}{suffix}")
        if (
            not candidate.exists()
            and candidate.resolve() not in occupied_set
        ):
            return relative_review_path(root, candidate)
        version += 1


def resolve_review(
    root: Path,
    logical: str,
    *,
    contract_hash: str | None = None,
    artifact_hashes: dict[str, str | None] | None = None,
) -> dict | None:
    """Resolve the newest valid review for the current immutable inputs.

    A malformed newest member is returned as an error instead of silently
    falling back. Valid but stale members are skipped, allowing an intentional
    rollback to reuse an older review only when its exact hashes match.
    """
    for version, path in reversed(review_candidates(root, logical)):
        try:
            record = validate_review(read_json(path), path)
        except (ValueError, RuntimeError) as exc:
            return {
                "logical_path": logical,
                "path": relative_review_path(root, path),
                "version": version,
                "record": None,
                "sha256": sha256(path),
                "error": str(exc),
            }
        if (
            contract_hash is not None
            and record.get("contract_hash") != contract_hash
        ):
            continue
        if artifact_hashes is not None:
            reviewed = record.get("artifact_hashes", {})
            if any(
                digest is None or reviewed.get(relative) != digest
                for relative, digest in artifact_hashes.items()
            ):
                continue
        return {
            "logical_path": logical,
            "path": relative_review_path(root, path),
            "version": version,
            "record": record,
            "sha256": sha256(path),
            "error": None,
        }
    return None


def review_lineage_signature(root: Path, logical: str) -> dict[str, str]:
    return {
        relative_review_path(root, path): sha256(path)
        for _version, path in review_candidates(root, logical)
    }


# --------------------------------------------------------------------------
# 硬不变量 5：审核身份绑定
#
# evidence 层（4.6.1）已经把「生成者不得自验」落到 evidence verify 上。
# 这里把同一套判定复用到另外两处审核入口：阶段 gate（config/stages.json 的
# reviews）与交付终审（reviews/s6_paper_audit.json 谱系）。判定本体
# ——worker 归一化与 producer 身份解析——一律走 evidence_core，不另起炉灶。
# --------------------------------------------------------------------------


def _workflow_present(root: Path) -> bool:
    """工作流是否已启用。未启用时不实例化引擎，避免凭空建库。"""
    return (Path(root) / ".harness" / "workflow.sqlite3").is_file()


def _evidence_facade(root: Path):
    from .evidence_core import EvidenceGraph

    return EvidenceGraph(Path(root).resolve())


def identity_enforced(root: Path) -> bool:
    """当前 Delivery Profile 是否强制审核身份（cumcm / mcm_icm）。"""
    return _evidence_facade(root)._isolation_required()


def producer_workers(root: Path, artifacts: Iterable[str]) -> list[str]:
    """按持久化工作流状态，判定这些工件的生成者 worker 集合。

    直接复用 ``EvidenceGraph._producer_identity``：producer_task_id 与
    「占有该工件的非 independent_review 任务」两条口径完全一致，
    否则漏掉 owns 兜底就等于给自批留后门。
    """
    root = Path(root).resolve()
    if not _workflow_present(root):
        return []
    graph = _evidence_facade(root)
    workers: set[str] = set()
    for artifact in artifacts:
        if not artifact or not str(artifact).strip():
            continue
        try:
            identity = graph._producer_identity({
                "artifact": str(artifact),
                "producer_task_id": None,
            })
        except ValueError:
            continue
        workers.update(item for item in identity["workers"] if item)
    return sorted(workers)


def has_independent_review_task(root: Path) -> bool:
    """项目是否已经用工作流登记过 independent_review 任务。"""
    root = Path(root).resolve()
    if not _workflow_present(root):
        return False
    from .workflow import WorkflowEngine

    return any(
        task.get("task_type") == "independent_review"
        for task in WorkflowEngine(root).list_tasks()
    )


def review_identity_errors(
    root: Path,
    record: dict,
    review_path: str,
    artifacts: Iterable[str],
    *,
    label: str,
    require_reviewer: bool,
    require_review_task: bool = False,
) -> list[str]:
    """审核者身份校验，返回中文错误列表（空列表表示通过）。

    兼容策略（沿用 evidence 层 4.6.1 的口径）：

    * ``reviewer`` 键整个缺失属于旧格式，只有竞赛 profile 才判失败，
      其它 profile grandfathering 放行，历史 run 与旧夹具不塌陷；
    * ``reviewer`` 键存在但为空串/空白，是当代格式写坏了，一律判失败；
    * 一旦身份可解析，与生成者冲突就一律判失败——与 profile 无关，
      因为冲突只可能出现在已经记录了身份的新格式上。
    """
    from .evidence_core import normalize_worker

    root = Path(root).resolve()
    errors: list[str] = []
    declared = record.get("reviewer")
    reviewer = "" if declared is None else str(declared).strip()
    if not reviewer:
        if "reviewer" in record and declared is not None:
            errors.append(
                f"{label}的 reviewer 为空: {review_path}；{SELF_APPROVE_HINT}"
            )
        elif require_reviewer:
            errors.append(
                f"{label}未署名 reviewer: {review_path}；{SELF_APPROVE_HINT}"
            )
    task_id = str(record.get("task_id") or "").strip()
    if task_id:
        from .workflow import WorkflowEngine

        task = None
        if _workflow_present(root):
            try:
                task = WorkflowEngine(root).get_task(task_id)
            except ValueError:
                task = None
        if task is None:
            errors.append(
                f"{label}绑定的审核任务不存在: {task_id}（{review_path}）"
            )
        else:
            if task.get("task_type") != "independent_review":
                errors.append(
                    f"{label}绑定的任务不是 independent_review: {task_id}"
                    f"（task_type={task.get('task_type')}，{review_path}）；"
                    f"{SELF_APPROVE_HINT}"
                )
            if task.get("status") != "completed":
                errors.append(
                    f"{label}绑定的审核任务未 completed: {task_id}"
                    f"（status={task.get('status')}，{review_path}）"
                )
            worker = task.get("worker")
            if (
                reviewer
                and worker
                and normalize_worker(reviewer) != normalize_worker(worker)
            ):
                errors.append(
                    f"{label}的 reviewer 与审核任务的 worker 不一致: "
                    f"{reviewer} != {worker}（{review_path}）"
                )
            reviewer = reviewer or (worker or "")
    elif require_review_task:
        errors.append(
            f"{label}未绑定 independent_review 任务（缺 task_id）: "
            f"{review_path}；{SELF_APPROVE_HINT}"
        )
    clash = [
        item for item in producer_workers(root, artifacts)
        if normalize_worker(item) == normalize_worker(reviewer)
    ]
    if normalize_worker(reviewer) and clash:
        errors.append(
            f"{label}的 reviewer 是该产物的生成者，不得自批: {clash[0]}"
            f"（{review_path}）；{SELF_APPROVE_HINT}"
        )
    return errors
