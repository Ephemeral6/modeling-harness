"""Shared mechanical check and task-acceptance execution."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .contracts import safe_relative
from .runtime_state import outcome
from .storage import read_json
from .util import sha256


def normalize_check(
    value: Any, *, default_timeout: int = 1800
) -> tuple[list[str] | str, bool, int, list[str]]:
    """Return argv, legacy-shell flag, timeout and expected artifacts."""
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return value, False, default_timeout, []
    if isinstance(value, dict):
        argv = value.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(x, str) and x for x in argv
        ):
            raise ValueError("check.argv must be a non-empty string array")
        timeout = int(value.get("timeout", default_timeout))
        if timeout <= 0:
            raise ValueError("check.timeout must be positive")
        expected = value.get("expected_artifacts", [])
        if not isinstance(expected, list) or not all(
            isinstance(x, str) and x for x in expected
        ):
            raise ValueError("check.expected_artifacts must be a string array")
        return argv, False, timeout, expected
    if isinstance(value, str) and value.strip():
        return value, True, default_timeout, []
    raise ValueError(f"invalid check command: {value!r}")


def run_check(root: Path, value: Any, *, default_timeout: int = 1800) -> dict:
    root = root.resolve()
    argv, legacy_shell, timeout, expected = normalize_check(
        value, default_timeout=default_timeout
    )
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            shell=legacy_shell,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        record = {
            "argv": argv,
            "legacy_shell": legacy_shell,
            "timeout": timeout,
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-4000:],
            "stderr_tail": (proc.stderr or "")[-4000:],
            "expected_artifacts": [],
        }
        state = outcome(
            "completed", "pass" if proc.returncode == 0 else "fail"
        )
    except subprocess.TimeoutExpired as exc:
        record = {
            "argv": argv,
            "legacy_shell": legacy_shell,
            "timeout": timeout,
            "returncode": 124,
            "stdout_tail": str(exc.stdout or "")[-4000:],
            "stderr_tail": "timeout",
            "expected_artifacts": [],
        }
        state = outcome("error", "inconclusive")
    missing = []
    for relative in expected:
        path = safe_relative(root, relative)
        item = {
            "path": relative,
            "exists": path.is_file(),
            "sha256": sha256(path) if path.is_file() else None,
        }
        record["expected_artifacts"].append(item)
        if not item["exists"]:
            missing.append(relative)
    if missing and state["execution_status"] == "completed":
        state = outcome("completed", "fail", freshness="missing")
        record["stderr_tail"] = (
            record["stderr_tail"] + f"\nmissing artifacts: {missing}"
        ).strip()
    record.update(state)
    return record


def run_checks(root: Path, values: list[Any]) -> list[dict]:
    return [run_check(root, value) for value in values]


def _json_path(value: Any, dotted: str) -> tuple[bool, Any]:
    current = value
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def _apply_op(
    operator: str, actual: Any, expected: Any, tolerance: float = 0
) -> bool:
    """Apply runtime JSON assertions via the toolchain numeric operator."""
    if operator == "nonempty":
        try:
            return actual is not None and len(actual) > 0
        except TypeError:
            return bool(actual)
    # Lazy import avoids checks -> executor -> checks import cycles.
    from .toolchain_execution_core import _operator

    numeric_ops = {
        "eq": "==",
        "ne": "!=",
        "ge": ">=",
        "gt": ">",
        "le": "<=",
        "lt": "<",
        "close_to": "==",
    }
    if operator == "between":
        if (
            not isinstance(expected, (list, tuple))
            or len(expected) != 2
        ):
            return False
        try:
            return _operator(
                float(actual), ">=", float(expected[0]), tolerance
            ) and _operator(
                float(actual), "<=", float(expected[1]), tolerance
            )
        except (TypeError, ValueError):
            return False
    if operator not in numeric_ops:
        return False
    try:
        return _operator(
            float(actual),
            numeric_ops[operator],
            float(expected),
            tolerance,
        )
    except (TypeError, ValueError):
        if operator == "eq":
            return actual == expected
        if operator == "ne":
            return actual != expected
        return False


def _record(kind: str, passed: bool, **fields) -> dict:
    freshness = fields.pop("freshness", "valid")
    return {
        "kind": kind,
        **fields,
        **outcome("completed", "pass" if passed else "fail", freshness=freshness),
    }


def _condition_active(root: Path, condition: Any) -> bool:
    if condition in {None, "", "always"}:
        return True
    if condition == "source_present":
        directory = root / "problem" / "data_raw"
        return directory.is_dir() and any(
            path.is_file() for path in directory.rglob("*")
        )
    if condition == "requirements_present":
        return (root / "problem" / "requirements.json").is_file()
    if condition == "scenario_sets_present":
        return (root / "results" / "scenario_sets.json").is_file()
    if condition == "assumptions_present":
        return (root / "docs" / "assumptions.json").is_file()
    if condition == "research_diagnostics_present":
        return (root / "results" / "research_diagnostics.json").is_file()
    return False


def evaluate_acceptance(
    root: Path, acceptance: list[Any], result: dict | None = None
) -> dict:
    """Evaluate acceptance without trusting a worker's summary.

    An empty acceptance list is explicitly NOT_RUN, never PASS.
    """
    root = root.resolve()
    records: list[dict] = []
    for raw in acceptance:
        if isinstance(raw, str):
            prefixes = ("产物存在:", "artifact exists:", "artifact_exists:")
            match = next(
                (x for x in prefixes if raw.lower().startswith(x.lower())),
                None,
            )
            if match:
                relative = raw[len(match):].strip()
                path = safe_relative(root, relative)
                exists = path.is_file()
                records.append(_record(
                    "artifact_exists",
                    exists,
                    path=relative,
                    sha256=sha256(path) if exists else None,
                    freshness="valid" if exists else "missing",
                ))
            else:
                records.append({
                    "kind": "legacy_note",
                    "value": raw,
                    "error": "legacy note is not executable acceptance",
                    **outcome("not_run", "inconclusive"),
                })
            continue
        if not isinstance(raw, dict):
            records.append({
                "kind": "invalid",
                "error": f"invalid acceptance item: {raw!r}",
                **outcome("not_run", "inconclusive"),
            })
            continue
        kind = raw.get("kind")
        condition = raw.get("when")
        if not _condition_active(root, condition):
            records.append(_record(
                str(kind or "conditional"),
                True,
                skipped=True,
                when=condition,
            ))
            continue
        if kind == "artifact_exists":
            relative = str(raw.get("path", ""))
            path = safe_relative(root, relative)
            exists = path.is_file()
            records.append(_record(
                kind,
                exists,
                path=relative,
                sha256=sha256(path) if exists else None,
                freshness="valid" if exists else "missing",
            ))
        elif kind == "json_fields":
            relative = str(raw.get("path", ""))
            fields = raw.get("fields", [])
            path = safe_relative(root, relative)
            data = read_json(path) if path.is_file() else None
            missing = [
                field for field in fields
                if not isinstance(field, str) or not _json_path(data, field)[0]
            ]
            records.append(_record(
                kind,
                path.is_file() and not missing,
                path=relative,
                fields=fields,
                missing=missing,
                freshness="valid" if path.is_file() else "missing",
            ))
        elif kind == "json_assert":
            relative = str(raw.get("path", ""))
            path = safe_relative(root, relative)
            data = read_json(path) if path.is_file() else None
            field = str(raw.get("field", ""))
            exists, actual = _json_path(data, field)
            operator = str(raw.get("op", "eq"))
            passed = exists and _apply_op(
                operator,
                actual,
                raw.get("value"),
                float(raw.get("tolerance", 0)),
            )
            records.append(_record(
                kind,
                passed,
                path=relative,
                field=field,
                actual=actual,
                op=operator,
                freshness="valid" if path.is_file() else "missing",
            ))
        elif kind == "requirement_coverage":
            from .requirements import (
                audit_requirement_extraction,
                audit_requirements,
            )

            errors = (
                audit_requirement_extraction(root)
                if raw.get("phase") == "extraction"
                else audit_requirements(root)
            )
            records.append(_record(
                kind,
                not errors,
                errors=errors,
                path="problem/requirements.json",
                freshness="valid" if not errors else "stale",
            ))
        elif kind == "profile_mandatory_outputs":
            profile = read_json(
                root / "config" / "delivery_profile.json", {}
            )
            required = (
                profile.get("mandatory_outputs", [])
                if isinstance(profile, dict) else []
            )
            graph = read_json(
                root / ".harness" / "evidence.json", {"nodes": {}}
            )
            nodes = (
                graph.get("nodes", {})
                if isinstance(graph, dict) else {}
            )

            def normalized(value: Any) -> str:
                return str(value).strip().casefold().replace("-", "_")

            def matches(output: str, node_id: str, node: Any) -> bool:
                if not isinstance(node, dict):
                    return False
                aliases = {
                    normalized(node_id),
                    normalized(node_id.rsplit(".", 1)[-1]),
                    normalized(node.get("kind", "")),
                    normalized(node.get("profile_output", "")),
                }
                profile_outputs = node.get("profile_outputs", [])
                if isinstance(profile_outputs, list):
                    aliases.update(normalized(item) for item in profile_outputs)
                return (
                    normalized(output) in aliases
                    and node.get("status") == "verified"
                    and node.get("freshness", "valid") == "valid"
                )

            missing = [
                output for output in required
                if not any(
                    matches(str(output), node_id, node)
                    for node_id, node in nodes.items()
                )
            ]
            records.append(_record(
                kind,
                isinstance(required, list) and not missing,
                required=required,
                missing=missing,
                path="config/delivery_profile.json",
                freshness="valid" if not missing else "missing",
            ))
        elif kind == "opportunity_scan":
            from .opportunities import (
                detect_infeasibility,
                detect_optimality_gap,
                detect_rank_flip,
                detect_unmaterialized_branches,
            )

            detector = str(raw.get("detector", ""))
            diag_path = root / "results" / "research_diagnostics.json"
            assumptions_path = root / "docs" / "assumptions.json"
            diag = read_json(diag_path, {})
            evidence = read_json(
                root / ".harness" / "evidence.json", {}
            )
            detectors = {
                "optimality_gap": lambda: detect_optimality_gap(
                    evidence or {}, diag or {}
                ),
                "rank_flip": lambda: detect_rank_flip(diag or {}),
                "infeasibility": lambda: detect_infeasibility(diag or {}),
                "assumption_branches": lambda: (
                    detect_unmaterialized_branches(
                        read_json(assumptions_path, {})
                    )
                ),
            }
            required_path = (
                assumptions_path
                if detector == "assumption_branches"
                else diag_path
            )
            passed = detector in detectors and required_path.is_file()
            opportunities = detectors[detector]() if passed else []
            records.append(_record(
                kind,
                passed,
                detector=detector,
                path=required_path.relative_to(root).as_posix(),
                opportunities=opportunities,
                freshness="valid" if passed else "missing",
            ))
        elif kind == "holdout_separation":
            from .claims import audit_holdout

            errors = audit_holdout(root)
            records.append(_record(
                kind,
                not errors,
                errors=errors,
                path="results/scenario_sets.json",
                freshness="valid" if not errors else "stale",
            ))
        elif kind == "evidence_exists":
            node_id = str(raw.get("id", ""))
            allowed = raw.get("statuses", ["candidate", "verified"])
            graph = read_json(
                root / ".harness" / "evidence.json", {"nodes": {}}
            )
            node = graph.get("nodes", {}).get(node_id)
            passed = isinstance(node, dict) and node.get("status") in allowed
            records.append(_record(
                kind,
                passed,
                id=node_id,
                status=node.get("status") if isinstance(node, dict) else None,
            ))
        elif kind == "result_fields":
            fields = raw.get("fields", [])
            missing = [
                field for field in fields
                if not isinstance(field, str)
                or not _json_path(result or {}, field)[0]
            ]
            records.append(_record(
                kind, not missing, fields=fields, missing=missing
            ))
        elif kind == "check":
            record = run_check(root, raw.get("check"))
            record["kind"] = kind
            records.append(record)
        else:
            records.append({
                "kind": kind or "invalid",
                "error": "unknown acceptance type",
                **outcome("not_run", "inconclusive"),
            })
    if not records:
        return outcome("not_run", "unassessed", records=[])
    passed = all(item.get("ok") is True for item in records)
    return outcome(
        "completed",
        "pass" if passed else "fail",
        records=records,
    )
