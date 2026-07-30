"""Shared mechanical check and task-acceptance execution."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .contracts import safe_relative
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
            raise ValueError("check.argv 必须是非空字符串数组")
        timeout = int(value.get("timeout", default_timeout))
        if timeout <= 0:
            raise ValueError("check.timeout 必须为正数")
        expected = value.get("expected_artifacts", [])
        if not isinstance(expected, list) or not all(
            isinstance(x, str) and x for x in expected
        ):
            raise ValueError("check.expected_artifacts 必须是字符串数组")
        return argv, False, timeout, expected
    # V2 compatibility only. New contracts must use argv arrays.
    if isinstance(value, str) and value.strip():
        return value, True, default_timeout, []
    raise ValueError(f"非法检查命令: {value!r}")


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
    record["ok"] = record["returncode"] == 0 and not missing
    if missing:
        record["stderr_tail"] = (
            record["stderr_tail"] + f"\nmissing artifacts: {missing}"
        ).strip()
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


def evaluate_acceptance(
    root: Path, acceptance: list[Any], result: dict | None = None
) -> dict:
    """Evaluate durable-task acceptance without trusting the worker summary."""
    root = root.resolve()
    records: list[dict] = []
    for raw in acceptance:
        if isinstance(raw, str):
            prefixes = ("产物存在:", "artifact exists:", "artifact_exists:")
            match = next((x for x in prefixes if raw.lower().startswith(x.lower())), None)
            if match:
                relative = raw[len(match):].strip()
                path = safe_relative(root, relative)
                records.append({
                    "kind": "artifact_exists",
                    "path": relative,
                    "ok": path.is_file(),
                    "sha256": sha256(path) if path.is_file() else None,
                })
            else:
                records.append({
                    "kind": "legacy_note",
                    "value": raw,
                    "ok": False,
                    "error": "不可执行的旧式验收说明",
                })
            continue
        if not isinstance(raw, dict):
            records.append({
                "kind": "invalid",
                "ok": False,
                "error": f"非法验收项: {raw!r}",
            })
            continue
        kind = raw.get("kind")
        if kind == "artifact_exists":
            relative = str(raw.get("path", ""))
            path = safe_relative(root, relative)
            records.append({
                "kind": kind,
                "path": relative,
                "ok": path.is_file(),
                "sha256": sha256(path) if path.is_file() else None,
            })
        elif kind == "json_fields":
            relative = str(raw.get("path", ""))
            fields = raw.get("fields", [])
            path = safe_relative(root, relative)
            data = read_json(path) if path.is_file() else None
            missing = [
                field for field in fields
                if not isinstance(field, str) or not _json_path(data, field)[0]
            ]
            records.append({
                "kind": kind,
                "path": relative,
                "fields": fields,
                "missing": missing,
                "ok": path.is_file() and not missing,
            })
        elif kind == "evidence_exists":
            node_id = str(raw.get("id", ""))
            allowed = raw.get("statuses", ["candidate", "verified"])
            graph = read_json(
                root / ".harness" / "evidence.json",
                {"nodes": {}},
            )
            node = graph.get("nodes", {}).get(node_id)
            records.append({
                "kind": kind,
                "id": node_id,
                "status": node.get("status") if isinstance(node, dict) else None,
                "ok": isinstance(node, dict) and node.get("status") in allowed,
            })
        elif kind == "result_fields":
            fields = raw.get("fields", [])
            missing = [
                field for field in fields
                if not isinstance(field, str)
                or not _json_path(result or {}, field)[0]
            ]
            records.append({
                "kind": kind,
                "fields": fields,
                "missing": missing,
                "ok": not missing,
            })
        elif kind == "check":
            check = raw.get("check")
            record = run_check(root, check)
            record["kind"] = kind
            records.append(record)
        else:
            records.append({
                "kind": kind or "invalid",
                "ok": False,
                "error": "未知验收类型",
            })
    return {
        "ok": all(item.get("ok") is True for item in records),
        "records": records,
    }

