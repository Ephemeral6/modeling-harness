"""Small, orthogonal runtime-state vocabulary shared by the harness."""
from __future__ import annotations

EXECUTION_STATUSES = {
    "not_run", "queued", "running", "completed", "error",
    "recovery_pending", "cancelled",
}
VERDICTS = {"unassessed", "pass", "fail", "inconclusive", "not_applicable"}
AUTHORITIES = {"machine", "human", "hybrid"}
FRESHNESS_STATES = {"valid", "stale", "missing", "tampered"}


def outcome(
    execution_status: str,
    verdict: str = "unassessed",
    authority: str = "machine",
    freshness: str = "valid",
    **extra,
) -> dict:
    """Build a validated state record and a compatibility ``ok`` value."""
    if execution_status not in EXECUTION_STATUSES:
        raise ValueError(f"unknown execution_status: {execution_status}")
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict: {verdict}")
    if authority not in AUTHORITIES:
        raise ValueError(f"unknown authority: {authority}")
    if freshness not in FRESHNESS_STATES:
        raise ValueError(f"unknown freshness: {freshness}")
    record = {
        "execution_status": execution_status,
        "verdict": verdict,
        "authority": authority,
        "freshness": freshness,
        "ok": (
            execution_status == "completed"
            and verdict == "pass"
            and freshness == "valid"
        ),
    }
    record.update(extra)
    return record


def derive_freshness(
    path_exists: bool, expected_hash: str | None, actual_hash: str | None
) -> str:
    if not path_exists:
        return "missing"
    if expected_hash is None:
        return "stale"
    if actual_hash != expected_hash:
        return "tampered"
    return "valid"
