"""Retrospective judge for the 2026-08-02 blind comparison (codex vs harness).

WHAT THIS IS
------------
The 2026-blind-codex-vs-harness protocol registered three harness arms and one
external codex arm, then nobody ever marked the papers.  This script is the
missing judge, written **after** every arm had already finished.  It reads the
input measurement table (``measurements.json``, which carries one on-disk
provenance pointer per number), re-verifies each number against its source
file, recomputes every gap percentage and ranking from scratch, and emits
``report.json``.  Nothing in the report is hand-typed: rerunning this script
regenerates the report byte-for-byte.

HONESTY RED LINE
----------------
The judge parameters here were frozen *after* the arms completed, so this run
does **not** satisfy clause (d) of the protocol ("judge freezes parameters
before any arm finishes").  It has zero pre-registration value.  The
``comparison audit`` finding "judge freeze too late" is the correct, intended
outcome and must not be papered over by backdating ``frozen_at``.

USAGE
-----
    python benchmarks/protocols/judges/retrospective_2026_blind.py
    python benchmarks/protocols/judges/retrospective_2026_blind.py --check

``--check`` recomputes in memory and diffs against the report on disk
(exit 1 on any drift), which is how "the report is recomputable, not
transcribed" stays true over time.

Pure standard library.  Paths via pathlib.  Windows-safe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = 1
JUDGE_ID = "retrospective-independent-judge"
PROTOCOL_ID = "2026-blind-codex-vs-harness"

# judges/ -> protocols/ -> benchmarks/ -> <repo root>
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
REPORT_DIR = REPO_ROOT / "benchmarks" / "reports" / PROTOCOL_ID
MEASUREMENTS_PATH = REPORT_DIR / "measurements.json"
REPORT_PATH = REPORT_DIR / "report.json"

# Comparability classes declared per metric in measurements.json.
HEAD_TO_HEAD = "head_to_head"
WITHIN_SAME_MODEL = "within_same_model"
NOT_COMPARABLE = "not_comparable"
COMPARABILITY = (HEAD_TO_HEAD, WITHIN_SAME_MODEL, NOT_COMPARABLE)


# ------------------------------------------------------------------ helpers


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_source(source: str) -> Path:
    """Manifest-style resolution: absolute verbatim, relative from repo root."""
    candidate = Path(source)
    if candidate.is_absolute():
        return candidate
    return REPO_ROOT / candidate


def json_pointer(document, pointer: str):
    """RFC 6901 lookup; raises KeyError/IndexError/TypeError when absent."""
    if pointer in ("", "/"):
        return document
    current = document
    for raw in pointer.lstrip("/").split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current[token]
    return current


def apply_derivation(value, derivation):
    """Re-apply the declared transform from the raw source value."""
    if not derivation:
        return value
    operation = derivation["operation"]
    if operation == "identity":
        return value
    if operation == "divide":
        return value / derivation["operand"]
    if operation == "multiply":
        return value * derivation["operand"]
    raise ValueError(f"unknown derivation operation: {operation}")


def close_enough(left: float, right: float) -> bool:
    """Float re-read tolerance; JSON round-trips are exact, this is a guard."""
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= 1e-9 * scale


def verify_observation(observation: dict) -> dict:
    """Re-read the number from its declared on-disk source."""
    source = observation.get("source_path")
    pointer = observation.get("source_pointer")
    result = {"status": "unchecked", "detail": "no source declared"}
    if not source or pointer is None:
        return result
    path = resolve_source(source)
    if not path.is_file():
        return {"status": "source_missing", "detail": str(path)}
    try:
        raw = json_pointer(read_json(path), pointer)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return {"status": "pointer_failed", "detail": f"{pointer}: {exc!r}"}
    try:
        recomputed = apply_derivation(raw, observation.get("derivation"))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        return {"status": "derivation_failed", "detail": repr(exc)}
    if not isinstance(recomputed, (int, float)) or isinstance(recomputed, bool):
        return {"status": "not_numeric", "detail": repr(raw)}
    if close_enough(float(recomputed), float(observation["value"])):
        return {
            "status": "verified",
            "detail": f"{source}#{pointer}",
            "source_raw_value": raw,
        }
    return {
        "status": "MISMATCH",
        "detail": f"{source}#{pointer} -> {recomputed!r}, table says "
                  f"{observation['value']!r}",
        "source_raw_value": raw,
    }


# ---------------------------------------------------------------- the maths


def gap_pct(value: float, best: float, direction: str) -> float:
    """How far below the best arm, as a percentage of the best value.

    Uniform convention for both senses: 0.0 means "is the best arm", a
    positive number means "this much worse than the best arm, measured in
    percent of the best arm's value".
    """
    if best == 0:
        return 0.0
    shortfall = (best - value) if direction == "max" else (value - best)
    return shortfall / abs(best) * 100.0


def normalized_score(value: float, best: float, direction: str) -> float:
    """1.0 for the best arm, <1.0 otherwise; unitless, so it can be averaged."""
    if direction == "max":
        return value / best if best else 0.0
    return best / value if value else 0.0


def score_metric(metric: dict) -> dict:
    """Recompute best arm, gaps and ranks for one metric."""
    direction = metric["direction"]
    comparability = metric["comparability"]
    tolerance = float(metric.get("tie_abs_tolerance", 0.0))
    rows = []
    for observation in metric["observations"]:
        rows.append({
            "arm_id": observation["arm_id"],
            "is_declared_arm": bool(observation.get("is_declared_arm", True)),
            "value": observation["value"],
            "artifact_label": observation.get("artifact_label", ""),
            "source_path": observation.get("source_path", ""),
            "source_pointer": observation.get("source_pointer", ""),
            "source_check": verify_observation(observation),
        })

    if comparability == NOT_COMPARABLE:
        for row in rows:
            row["gap_vs_best_pct"] = None
            row["gap_vs_best_absolute"] = None
            row["normalized_score"] = None
            row["rank"] = None
            row["outcome"] = "not_comparable"
        return {
            "metric_id": metric["metric_id"],
            "problem_id": metric["problem_id"],
            "label": metric["label"],
            "unit": metric["unit"],
            "direction": direction,
            "comparability": comparability,
            "comparability_reason": metric["comparability_reason"],
            "scored": bool(metric.get("scored", False)),
            "best_arm_id": None,
            "best_value": None,
            "tie_abs_tolerance": tolerance,
            "observations": rows,
            "notes": metric.get("notes", []),
            "errata": metric.get("errata"),
        }

    picker = max if direction == "max" else min
    best_value = picker(row["value"] for row in rows)
    best_rows = [r for r in rows if r["value"] == best_value]
    best_arm_id = best_rows[0]["arm_id"]

    # Everything inside the tolerance of the best value is indistinguishable.
    # Only when that set has more than one member is it an actual tie; a lone
    # best arm stays "best" even though its own distance to itself is zero.
    tied = [r for r in rows if abs(r["value"] - best_value) <= tolerance]
    is_tie = len(tied) > 1

    for row in rows:
        row["gap_vs_best_pct"] = round(
            gap_pct(row["value"], best_value, direction), 10
        )
        row["gap_vs_best_absolute"] = round(abs(row["value"] - best_value), 12)
        row["normalized_score"] = round(
            normalized_score(row["value"], best_value, direction), 12
        )
        if abs(row["value"] - best_value) <= tolerance:
            row["outcome"] = "tie_within_tolerance" if is_tie else "best"
        else:
            row["outcome"] = "behind"

    # Rank by value; every arm inside the tie tolerance of the best shares rank 1.
    ordered = sorted(
        rows, key=lambda r: r["value"], reverse=(direction == "max")
    )
    for position, row in enumerate(ordered, start=1):
        row["rank"] = 1 if row["outcome"] != "behind" else position

    return {
        "metric_id": metric["metric_id"],
        "problem_id": metric["problem_id"],
        "label": metric["label"],
        "unit": metric["unit"],
        "direction": direction,
        "comparability": comparability,
        "comparability_reason": metric["comparability_reason"],
        "scored": bool(metric.get("scored", False)),
        "best_arm_id": best_arm_id,
        "best_value": best_value,
        "tie_abs_tolerance": tolerance,
        "observations": rows,
        "notes": metric.get("notes", []),
        "errata": metric.get("errata"),
    }


def arm_rows(arm_id: str, scored_metrics: list[dict]) -> list[tuple[dict, dict]]:
    """(metric, own observation) pairs for one declared arm."""
    pairs = []
    for metric in scored_metrics:
        for row in metric["observations"]:
            if row["arm_id"] == arm_id and row["is_declared_arm"]:
                pairs.append((metric, row))
    return pairs


def build_report(measurements: dict, judge_sha256: str) -> dict:
    metrics = [score_metric(m) for m in measurements["metrics"]]
    by_id = {m["metric_id"]: m for m in metrics}

    scored = [
        m for m in metrics
        if m["scored"] and m["comparability"] == HEAD_TO_HEAD
    ]

    arms_out: dict[str, dict] = {}
    for arm in measurements["arms"]:
        arm_id = arm["arm_id"]
        pairs = arm_rows(arm_id, scored)
        wins = [m["metric_id"] for m, r in pairs if r["outcome"] == "best"]
        ties = [
            m["metric_id"] for m, r in pairs
            if r["outcome"] == "tie_within_tolerance"
        ]
        losses = [m["metric_id"] for m, r in pairs if r["outcome"] == "behind"]
        scores = [r["normalized_score"] for _, r in pairs]
        aggregate = (
            round(sum(scores) / len(scores), 12) if scores else None
        )

        source = arm["objective_source"]
        if source["kind"] == "metric":
            metric = by_id[source["metric_id"]]
            own = next(
                r for r in metric["observations"]
                if r["arm_id"] == arm_id and r["is_declared_arm"]
            )
            objective_value = own["value"]
            objective_metric = metric["metric_id"]
            objective_unit = metric["unit"]
            gap_block = {
                "metric_id": metric["metric_id"],
                "best_arm_id": metric["best_arm_id"],
                "best_value": metric["best_value"],
                "gap_pct": own["gap_vs_best_pct"],
                "gap_absolute": own["gap_vs_best_absolute"],
                "unit": metric["unit"],
            }
            headline_outcome = own["outcome"]
        elif source["kind"] == "aggregate_normalized_score":
            objective_value = aggregate
            objective_metric = "aggregate_normalized_score"
            objective_unit = "unitless (1.0 = best arm on every scored metric)"
            gap_block = None
            headline_outcome = None
        else:
            raise ValueError(f"unknown objective_source kind: {source['kind']}")

        arms_out[arm_id] = {
            "arm_id": arm_id,
            "kind": arm["kind"],
            "problems_covered": arm["problems_covered"],
            "objective_value": objective_value,
            "objective_metric": objective_metric,
            "objective_unit": objective_unit,
            "objective_source_kind": source["kind"],
            "verdict": None,          # filled below
            "verdict_basis": None,    # filled below
            "verdict_detail": {
                "scored_metrics": len(pairs),
                "best_on": wins,
                "tied_on": ties,
                "behind_on": losses,
            },
            "aggregate_normalized_score": aggregate,
            "gap_vs_best_arm": gap_block,
            "_headline_outcome": headline_outcome,
            "notes": arm.get("notes", []),
        }

    # Verdicts.  Single-problem arms are judged on their own headline metric;
    # the multi-problem arm is judged on the unitless aggregate.
    aggregates = {
        arm_id: row["aggregate_normalized_score"]
        for arm_id, row in arms_out.items()
        if row["aggregate_normalized_score"] is not None
    }
    best_aggregate = max(aggregates.values()) if aggregates else None

    for arm_id, row in arms_out.items():
        detail = row["verdict_detail"]
        if detail["scored_metrics"] == 0:
            row["verdict"] = "not_scored"
            row["verdict_basis"] = "no scored head-to-head metric for this arm"
        elif row["objective_source_kind"] == "aggregate_normalized_score":
            agg = row["aggregate_normalized_score"]
            if best_aggregate is not None and agg == best_aggregate:
                row["verdict"] = "win"
            else:
                row["verdict"] = "loss"
            row["verdict_basis"] = (
                "aggregate normalized score over all scored head-to-head "
                "metrics this arm participates in"
            )
        else:
            outcome = row.pop("_headline_outcome")
            row["verdict"] = {
                "best": "win",
                "tie_within_tolerance": "tie",
                "behind": "loss",
            }[outcome]
            row["verdict_basis"] = (
                f"headline metric {row['objective_metric']}"
            )
        row.pop("_headline_outcome", None)

    # Per-problem head-to-head rankings.
    per_problem = {}
    for problem_id in measurements["problem_ids"]:
        problem_metrics = [m for m in metrics if m["problem_id"] == problem_id]
        headline_id = measurements["problem_headline_metric"].get(problem_id)
        headline = by_id.get(headline_id) if headline_id else None
        ranking = None
        if headline and headline["comparability"] != NOT_COMPARABLE:
            ranking = [
                {
                    "rank": r["rank"],
                    "arm_id": r["arm_id"],
                    "is_declared_arm": r["is_declared_arm"],
                    "value": r["value"],
                    "gap_vs_best_pct": r["gap_vs_best_pct"],
                }
                for r in sorted(
                    headline["observations"], key=lambda x: x["rank"]
                )
            ]
        per_problem[problem_id] = {
            "problem_id": problem_id,
            "headline_metric_id": headline_id,
            "headline_ranking": ranking,
            "comparable": bool(
                headline and headline["comparability"] == HEAD_TO_HEAD
            ),
            "comparability_summary":
                measurements["problem_comparability"][problem_id],
            "metrics": problem_metrics,
        }

    overall = sorted(
        (
            {
                "arm_id": arm_id,
                "aggregate_normalized_score": value,
            }
            for arm_id, value in aggregates.items()
        ),
        key=lambda item: item["aggregate_normalized_score"],
        reverse=True,
    )
    for position, item in enumerate(overall, start=1):
        item["rank"] = position

    checks = []
    for metric in metrics:
        for row in metric["observations"]:
            status = row["source_check"]["status"]
            if status != "verified":
                checks.append({
                    "metric_id": metric["metric_id"],
                    "arm_id": row["arm_id"],
                    "status": status,
                    "detail": row["source_check"]["detail"],
                })

    return {
        "schema": SCHEMA,
        "protocol_id": PROTOCOL_ID,
        "report_kind": "retrospective independent recomputation",
        "judge": {
            "worker": JUDGE_ID,
            "command": measurements["freeze"]["command"],
            "script_sha256": judge_sha256,
            "frozen_at": measurements["freeze"]["frozen_at"],
            "inputs_path": measurements["freeze"]["inputs_path"],
            "inputs_sha256": sha256_of(MEASUREMENTS_PATH),
            "pre_registered": False,
            "protocol_clause_d_satisfied": False,
        },
        "note": measurements["freeze"]["note"],
        "judging_method": measurements["judging_method"],
        "arms": arms_out,
        "per_problem": per_problem,
        "overall_ranking_by_aggregate": overall,
        "overall_ranking_caveat": measurements["overall_ranking_caveat"],
        "source_verification": {
            "observations_checked": sum(
                len(m["observations"]) for m in metrics
            ),
            "failures": checks,
            "all_verified": not checks,
        },
        "integrity_findings": measurements["integrity_findings"],
    }


# ---------------------------------------------------------------------- CLI


def serialize(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retrospective judge for " + PROTOCOL_ID
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="recompute and diff against report.json instead of writing it",
    )
    args = parser.parse_args(argv)

    if not MEASUREMENTS_PATH.is_file():
        print(f"input table missing: {MEASUREMENTS_PATH}")
        return 2
    measurements = read_json(MEASUREMENTS_PATH)
    for metric in measurements["metrics"]:
        if metric["comparability"] not in COMPARABILITY:
            print(f"bad comparability on {metric['metric_id']}")
            return 2

    report = build_report(measurements, sha256_of(Path(__file__).resolve()))
    payload = serialize(report)

    verification = report["source_verification"]
    print(f"judge      : {JUDGE_ID}")
    print(f"protocol   : {PROTOCOL_ID}")
    print(f"metrics    : {sum(len(p['metrics']) for p in report['per_problem'].values())}")
    print(
        "sources    : "
        f"{verification['observations_checked']} checked, "
        f"{len(verification['failures'])} not verified"
    )
    for failure in verification["failures"]:
        print(f"  ! {failure['metric_id']} / {failure['arm_id']}: "
              f"{failure['status']} - {failure['detail']}")
    for arm_id, row in report["arms"].items():
        gap = row["gap_vs_best_arm"]
        gap_text = "-" if gap is None else f"{gap['gap_pct']:.6f}%"
        print(
            f"  {arm_id:<22} objective={row['objective_value']!r:<22} "
            f"verdict={row['verdict']:<10} gap={gap_text}"
        )
    print("NOTE: judge parameters were frozen AFTER every arm finished; "
          "protocol clause (d) is NOT satisfied and this judgment carries "
          "no pre-registration value.")

    if args.check:
        if not REPORT_PATH.is_file():
            print(f"FAIL: {REPORT_PATH} does not exist")
            return 1
        on_disk = REPORT_PATH.read_text(encoding="utf-8")
        if on_disk == payload:
            print("CHECK OK: report.json is byte-identical to a fresh recompute")
            return 0
        print("CHECK FAILED: report.json drifted from a fresh recompute")
        return 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(payload, encoding="utf-8")
    print(f"wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
