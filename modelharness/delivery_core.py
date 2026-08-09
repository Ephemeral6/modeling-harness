"""Delivery gate and freeze: no terminal APPROVE, no final render or freeze."""
from __future__ import annotations

from pathlib import Path

from .claims import audit_claims
from .contracts import safe_relative, validate_review
from .lifecycle import set_status
from .paper_content import audit_paper_content
from .review_store import (
    has_independent_review_task,
    identity_enforced,
    relative_review_path,
    review_candidates,
    review_identity_errors,
)
from .sanitize import INTERNAL_ARTIFACTS, sanitize_report
from .stages import StageService
from .storage import atomic_write_json, read_json
from .util import now, sha256

TERMINAL_REVIEW = "reviews/s6_paper_audit.json"
FREEZE_MANIFEST = "paper/delivery_freeze.json"


def resolve_latest_review(
    root: Path, logical: str = TERMINAL_REVIEW
) -> dict | None:
    """Return the newest lineage member of ``logical``, without fallback.

    交付门禁只认谱系最新裁决。这与 ``review_store.resolve_review`` 的
    "跳过哈希失配版本回退旧审"语义刻意不同：陈旧 APPROVE 被更新的
    REJECT 覆盖后不得复活，最新成员失配时也不得回退放行。
    """
    root = Path(root).resolve()
    candidates = review_candidates(root, logical)
    if not candidates:
        return None
    version, path = candidates[-1]
    record = validate_review(read_json(path), path)
    return {
        "logical_path": logical,
        "path": relative_review_path(root, path),
        "version": version,
        "record": record,
        "sha256": sha256(path),
    }


def terminal_identity_errors(
    root: Path, resolved: dict, draft: str = "paper/draft.md"
) -> list[str]:
    """硬不变量 5 在交付终审的落点：终审不得由产物生成者出具。

    审核范围取「当前工作稿」并上该审核 ``artifact_hashes`` 声明覆盖的全部
    工件——reviewer 只要生产过其中任何一件，这份 APPROVE 就是自批。
    """
    root = Path(root).resolve()
    record = resolved["record"]
    artifacts = [draft, *record.get("artifact_hashes", {})]
    return review_identity_errors(
        root,
        record,
        resolved["path"],
        artifacts,
        label="交付终审",
        require_reviewer=identity_enforced(root),
        # 竞赛 profile 且项目确实在用工作流登记独立审核时，终审必须绑定
        # 一个真实的 independent_review 任务；未启用工作流的历史项目
        # （夹具、4.6 之前的 run）不因此塌陷。
        require_review_task=(
            identity_enforced(root) and has_independent_review_task(root)
        ),
    )


def require_terminal_approval(
    root: Path,
    draft: str = "paper/draft.md",
    logical: str = TERMINAL_REVIEW,
) -> dict:
    """Raise unless the newest terminal review APPROVEs the exact current draft."""
    root = Path(root).resolve()
    draft_path = safe_relative(root, draft)
    if not draft_path.is_file():
        raise ValueError(f"交付门禁: 工作稿不存在: {draft}")
    try:
        resolved = resolve_latest_review(root, logical)
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"交付门禁: 终审文件非法: {exc}") from exc
    if resolved is None:
        raise ValueError(
            f"交付门禁: 终审缺失: {logical} 谱系不存在任何审核，"
            "无终审 APPROVE 不得渲染或冻结 final"
        )
    record = resolved["record"]
    if str(record.get("verdict", "")).upper() != "APPROVE":
        raise ValueError(
            f"交付门禁: 终审谱系最新裁决为 {record.get('verdict')}"
            f"（{resolved['path']}），拒绝渲染或冻结 final"
        )
    reviewed = record.get("artifact_hashes", {}).get(draft)
    current = sha256(draft_path)
    if reviewed != current:
        raise ValueError(
            f"交付门禁: 终审 APPROVE（{resolved['path']}）绑定的 {draft} "
            f"哈希与当前稿不一致（审核 {reviewed}，当前 {current}），"
            "陈旧批准不放行"
        )
    identity = terminal_identity_errors(root, resolved, draft)
    if identity:
        raise ValueError("交付门禁: 终审身份校验失败:\n" + "\n".join(identity))
    return resolved


def freeze_delivery(root: Path, reason: str) -> dict:
    """Freeze the delivery set after every machine precondition passes."""
    root = Path(root).resolve()
    if not reason or not reason.strip():
        raise ValueError("交付冻结必须提供 reason")
    prefix = StageService(root).valid_prefix()
    if "s6" not in prefix:
        raise ValueError(
            f"交付冻结前置失败: s6 印章不在有效前缀内（当前 {prefix}）"
        )
    terminal = require_terminal_approval(root)
    if not (root / "paper" / "final.md").is_file():
        raise ValueError("交付冻结前置失败: paper/final.md 尚未渲染")
    report = sanitize_report(root)
    if not report.get("ok"):
        raise ValueError(
            "交付冻结前置失败: sanitize 存在 "
            f"{len(report.get('violations', []))} 项交付违规"
        )
    for label, errors in (
        ("论文内容合同", audit_paper_content(root)),
        ("claim 审计", audit_claims(root)),
    ):
        if errors:
            raise ValueError(
                f"交付冻结前置失败: {label}未闭合:\n" + "\n".join(errors)
            )
    files: dict[str, str] = {}
    for path in sorted((root / "paper").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative == FREEZE_MANIFEST or relative in INTERNAL_ARTIFACTS:
            continue
        files[relative] = sha256(path)
    manifest = {
        "schema": 1,
        "frozen_at": now(),
        "reason": reason.strip(),
        "files": files,
        "s6_stamp_sha256": sha256(root / ".harness" / "stamps" / "s6.json"),
        "terminal_review": {
            "path": terminal["path"],
            "sha256": terminal["sha256"],
        },
    }
    atomic_write_json(root / "paper" / "delivery_freeze.json", manifest)
    set_status(root, "delivered", reason)
    return manifest


def verify_freeze(root: Path) -> dict:
    """Rehash the frozen delivery set against the manifest and report drift."""
    root = Path(root).resolve()
    manifest = read_json(root / "paper" / "delivery_freeze.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError(f"交付冻结清单缺失或损坏: {FREEZE_MANIFEST}")
    files = manifest.get("files", {})
    drift: list[str] = []
    for relative, digest in files.items():
        path = root / relative
        if not path.is_file():
            drift.append(f"冻结文件缺失: {relative}")
        elif sha256(path) != digest:
            drift.append(f"冻结后哈希漂移: {relative}")
    for path in sorted((root / "paper").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if (
            relative != FREEZE_MANIFEST
            and relative not in INTERNAL_ARTIFACTS
            and relative not in files
        ):
            drift.append(f"冻结后新增未登记文件: {relative}")
    stamp = root / ".harness" / "stamps" / "s6.json"
    if not stamp.is_file() or sha256(stamp) != manifest.get("s6_stamp_sha256"):
        drift.append("冻结后哈希漂移: .harness/stamps/s6.json")
    terminal = manifest.get("terminal_review", {})
    review_relative = str(terminal.get("path") or "")
    review = (root / review_relative) if review_relative else None
    if (
        review is None
        or not review.is_file()
        or sha256(review) != terminal.get("sha256")
    ):
        drift.append(f"冻结后哈希漂移: {review_relative or '终审文件缺失'}")
    return {
        "schema": 1,
        "ok": not drift,
        "drift": drift,
        "files": len(files),
        "manifest": FREEZE_MANIFEST,
    }
