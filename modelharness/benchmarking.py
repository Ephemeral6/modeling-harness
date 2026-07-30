"""Zero-dependency hidden-rubric scorer for completed harness projects."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from .evaluation import score_project
from .storage import read_json
from .util import project_root


def _json_path(value: Any, dotted: str) -> tuple[bool, Any]:
    current = value
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _score_item(root: Path, item: dict, evaluation: dict) -> dict:
    kind = item.get("kind")
    passed = False
    actual: Any = None
    if kind == "artifact_exists":
        path = root / str(item.get("path", ""))
        passed, actual = path.is_file(), path.is_file()
    elif kind == "evidence_status":
        evidence = read_json(root / ".harness" / "evidence.json", {})
        node = evidence.get("nodes", {}).get(str(item.get("id", "")))
        actual = node.get("status") if isinstance(node, dict) else None
        passed = actual in item.get("statuses", ["verified"])
    elif kind == "json_numeric":
        path = root / str(item.get("path", ""))
        data = read_json(path) if path.is_file() else None
        exists, actual = _json_path(data, str(item.get("field", "")))
        target = float(item["target"])
        tolerance = float(item.get("tolerance", 0))
        try:
            passed = exists and abs(float(actual) - target) <= tolerance
        except (TypeError, ValueError):
            passed = False
    elif kind == "integrity":
        actual = evaluation.get("integrity", {}).get("ok")
        passed = actual is bool(item.get("expected", True))
    elif kind == "task_status_absent":
        status = str(item.get("status", "recovery_pending"))
        actual = evaluation.get("workflow", {}).get(
            "status_counts", {}
        ).get(status, 0)
        passed = actual == 0
    else:
        actual = f"unknown rubric kind: {kind}"
    return {
        "kind": kind,
        "name": item.get("name", kind),
        "weight": float(item.get("weight", 1)),
        "passed": passed,
        "actual": actual,
    }


def score_benchmark(project: Path, manifest_path: Path) -> dict:
    root = project_root(project)
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("schema") != 1:
        raise ValueError("benchmark manifest schema must be 1")
    evaluation = score_project(root)
    records = [
        _score_item(root, item, evaluation)
        for item in manifest.get("rubric", [])
    ]
    denominator = sum(item["weight"] for item in records)
    numerator = sum(
        item["weight"] for item in records if item["passed"]
    )
    score = numerator / denominator if denominator else 0.0
    threshold = float(manifest.get("pass_threshold", 1.0))
    return {
        "schema": 1,
        "benchmark_id": manifest.get("id"),
        "score": score,
        "pass_threshold": threshold,
        "passed": score >= threshold,
        "rubric": records,
        "evaluation": evaluation,
    }


def pass_all_k(reports: list[dict], k: int) -> float | None:
    """Empirical probability that k runs all pass, without replacement."""
    n = len(reports)
    passed = sum(bool(item.get("passed")) for item in reports)
    if k <= 0 or k > n:
        return None
    return (
        math.comb(passed, k) / math.comb(n, k)
        if passed >= k else 0.0
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score a completed project with a hidden JSON rubric"
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    report = score_benchmark(args.project, args.manifest)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        args.json.write_text(text + "\n", encoding="utf-8")
    print(text)
    return int(not report["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
