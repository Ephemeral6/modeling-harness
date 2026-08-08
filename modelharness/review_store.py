"""Append-only review lineage for cross-agent, cross-platform safety."""
from __future__ import annotations

import re
from pathlib import Path

from .contracts import safe_relative, validate_review
from .storage import read_json
from .util import sha256


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
