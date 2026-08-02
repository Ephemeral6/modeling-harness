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
