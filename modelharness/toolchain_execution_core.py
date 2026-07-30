"""Reproducible, non-shell computation runs and mechanical verification."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from .checks import run_check
from .contracts import safe_relative
from .problem_graph import canonical_hash
from .storage import atomic_write_json, read_json
from .toolchain_registry import ToolRegistry
from .util import now, sha256


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


def _all_finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, list):
        return all(_all_finite(item) for item in value)
    if isinstance(value, dict):
        return all(_all_finite(item) for item in value.values())
    return False


def _operator(left: float, operator: str, right: float, tolerance: float) -> bool:
    return {
        "<": left < right + tolerance,
        "<=": left <= right + tolerance,
        "==": abs(left - right) <= tolerance,
        "!=": abs(left - right) > tolerance,
        ">=": left + tolerance >= right,
        ">": left + tolerance > right,
    }.get(operator, False)


def validate_validators(values: Any) -> list[dict]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError("validators 必须是数组")
    allowed = {
        "file_nonempty", "json_finite", "json_fields",
        "numeric_assertion", "cross_artifact", "independent_check",
    }
    result = []
    for value in values:
        if not isinstance(value, dict) or value.get("kind") not in allowed:
            raise ValueError(f"非法 validator: {value!r}")
        result.append(value)
    return result


class ToolRunExecutor:
    def __init__(self, root: Path, registry: ToolRegistry | None = None):
        self.root = root.resolve()
        self.registry = registry or ToolRegistry(self.root)
        self.runs = self.root / ".harness" / "tool_runs"
        self.logs = self.root / "logs" / "tool_runs"

    def manifest_path(self, run_id: str) -> Path:
        if not run_id or any(x in run_id for x in ("/", "\\", "..")):
            raise ValueError(f"非法 tool run id: {run_id}")
        return self.runs / f"{run_id}.json"

    def list(self, node_id: str | None = None) -> list[dict]:
        records = []
        if not self.runs.is_dir():
            return records
        for path in sorted(self.runs.glob("*.json")):
            record = read_json(path)
            if isinstance(record, dict) and (
                node_id is None or record.get("node_id") == node_id
            ):
                records.append(record)
        return records

    @staticmethod
    def _runner_name(value: str) -> str:
        name = Path(value).name.casefold()
        return name[:-4] if name.endswith(".exe") else name

    def _validate_runner(self, argv: list[str], tool_ids: list[str]) -> None:
        if not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("tool run argv 必须是非空字符串数组")
        runner = self._runner_name(argv[0])
        allowed = set()
        for tool_id in tool_ids:
            tool = self.registry.get(tool_id)
            allowed.update(x.casefold() for x in tool.get("runners", []))
            if tool["kind"] == "command":
                allowed.add(tool["detect"]["command"].casefold())
        if runner not in allowed:
            raise ValueError(
                f"执行器 {runner} 未被所选工具允许；允许值={sorted(allowed)}"
            )

    def _artifact_records(
        self, paths: list[str], *, require_exists: bool
    ) -> list[dict]:
        records = []
        for relative in paths:
            path = safe_relative(self.root, relative)
            exists = path.is_file()
            if require_exists and not exists:
                raise ValueError(f"输入文件不存在: {relative}")
            records.append({
                "path": relative,
                "exists": exists,
                "size": path.stat().st_size if exists else None,
                "sha256": sha256(path) if exists else None,
            })
        return records

    def run(
        self,
        *,
        node_id: str,
        contract_hash: str,
        decision_hash: str,
        tool_ids: list[str],
        argv: list[str],
        inputs: list[str],
        outputs: list[str],
        validators: list[dict] | None = None,
        seed: int = 0,
        timeout: int = 1800,
    ) -> dict:
        autonomy = self.registry.autonomy
        if timeout <= 0 or timeout > autonomy["max_timeout_seconds"]:
            raise ValueError(
                f"timeout 必须位于 1..{autonomy['max_timeout_seconds']}"
            )
        if not tool_ids or len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tool_ids 不能为空或重复")
        selected = []
        allowed_risks = set(autonomy["autonomous_risks"])
        for tool_id in tool_ids:
            tool = self.registry.get(tool_id)
            probe = self.registry.probe(tool_id)
            if not probe["available"]:
                raise RuntimeError(f"计算工具不可用: {tool_id}")
            if tool.get("risk", "local") not in allowed_risks:
                raise RuntimeError(
                    f"工具风险未获自主授权: {tool_id}/{tool.get('risk')}"
                )
            selected.append({
                "id": tool_id,
                "version": probe["version"],
                "executable": probe["executable"],
                "risk": tool.get("risk", "local"),
                "warnings": probe["warnings"],
            })
        self._validate_runner(argv, tool_ids)
        validators = validate_validators(validators)
        input_records = self._artifact_records(inputs, require_exists=True)
        run_id = f"{int(time.time())}-{uuid.uuid4().hex[:12]}"
        self.logs.mkdir(parents=True, exist_ok=True)
        stdout_path = self.logs / f"{run_id}.stdout.txt"
        stderr_path = self.logs / f"{run_id}.stderr.txt"
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = str(seed)
        environment["MODEL_HARNESS_SEED"] = str(seed)
        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                argv,
                cwd=self.root,
                env=environment,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=False,
            )
            returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            stdout = str(exc.stdout or "")
            stderr = str(exc.stderr or "") + "\ntimeout"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        output_records = self._artifact_records(outputs, require_exists=False)
        record = {
            "schema": 1,
            "id": run_id,
            "node_id": node_id,
            "contract_hash": contract_hash,
            "decision_hash": decision_hash,
            "time": now(),
            "duration_seconds": round(time.monotonic() - started, 6),
            "seed": seed,
            "timeout": timeout,
            "timed_out": timed_out,
            "argv": argv,
            "cwd": ".",
            "tools": selected,
            "python": {
                "version": sys.version.split()[0],
                "executable": sys.executable,
            },
            "inputs": input_records,
            "outputs": output_records,
            "logs": {
                "stdout": stdout_path.relative_to(self.root).as_posix(),
                "stdout_sha256": sha256(stdout_path),
                "stderr": stderr_path.relative_to(self.root).as_posix(),
                "stderr_sha256": sha256(stderr_path),
            },
            "returncode": returncode,
            "validators": validators,
        }
        record["run_hash"] = canonical_hash({
            key: record[key] for key in (
                "node_id", "contract_hash", "decision_hash", "seed", "argv",
                "tools", "inputs", "outputs", "returncode", "validators",
            )
        })
        record["verification"] = self._verify_record(record, validators)
        atomic_write_json(self.manifest_path(run_id), record)
        return record

    def _verify_record(
        self, record: dict, validators: list[dict]
    ) -> dict:
        checks = [{
            "kind": "process_exit",
            "returncode": record.get("returncode"),
            "ok": record.get("returncode") == 0,
        }]
        for output in record.get("outputs", []):
            path = safe_relative(self.root, output["path"])
            current_hash = sha256(path) if path.is_file() else None
            checks.append({
                "kind": "output_integrity",
                "path": output["path"],
                "expected_sha256": output.get("sha256"),
                "current_sha256": current_hash,
                "ok": (
                    output.get("exists") is True
                    and current_hash == output.get("sha256")
                ),
            })
        for validator in validators:
            checks.append(self._run_validator(validator))
        return {
            "time": now(),
            "status": (
                "verified" if all(item.get("ok") is True for item in checks)
                else "failed"
            ),
            "checks": checks,
        }

    def _run_validator(self, validator: dict) -> dict:
        kind = validator["kind"]
        if kind == "independent_check":
            result = run_check(self.root, validator.get("check"))
            return {"kind": kind, **result}
        relative = str(validator.get("path", ""))
        path = safe_relative(self.root, relative)
        if kind == "file_nonempty":
            return {
                "kind": kind,
                "path": relative,
                "ok": path.is_file() and path.stat().st_size > 0,
            }
        if not path.is_file():
            return {
                "kind": kind, "path": relative, "ok": False,
                "error": "文件不存在",
            }
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {
                "kind": kind, "path": relative, "ok": False,
                "error": str(exc),
            }
        if kind == "json_finite":
            return {"kind": kind, "path": relative, "ok": _all_finite(data)}
        if kind == "json_fields":
            fields = validator.get("fields", [])
            missing = [field for field in fields if not _json_path(data, field)[0]]
            return {
                "kind": kind, "path": relative, "fields": fields,
                "missing": missing, "ok": not missing,
            }
        if kind == "numeric_assertion":
            field = str(validator.get("field", ""))
            exists, value = _json_path(data, field)
            try:
                number = float(value)
                target = float(validator["value"])
                tolerance = float(validator.get("tolerance", 0))
                ok = exists and math.isfinite(number) and _operator(
                    number, str(validator.get("operator", "==")),
                    target, tolerance,
                )
            except (TypeError, ValueError, KeyError):
                ok = False
                number = value
            return {
                "kind": kind, "path": relative, "field": field,
                "actual": number, "operator": validator.get("operator", "=="),
                "expected": validator.get("value"),
                "tolerance": validator.get("tolerance", 0), "ok": ok,
            }
        if kind == "cross_artifact":
            other_relative = str(validator.get("other_path", ""))
            other_path = safe_relative(self.root, other_relative)
            try:
                other_data = json.loads(other_path.read_text(encoding="utf-8"))
                left_ok, left = _json_path(data, str(validator.get("field", "")))
                right_ok, right = _json_path(
                    other_data, str(validator.get("other_field", ""))
                )
                left_number, right_number = float(left), float(right)
                abs_tol = float(validator.get("abs_tolerance", 0))
                rel_tol = float(validator.get("rel_tolerance", 0))
                difference = abs(left_number - right_number)
                limit = abs_tol + rel_tol * abs(right_number)
                ok = left_ok and right_ok and difference <= limit
            except (
                OSError, UnicodeDecodeError, json.JSONDecodeError,
                TypeError, ValueError,
            ) as exc:
                return {
                    "kind": kind, "path": relative,
                    "other_path": other_relative, "ok": False,
                    "error": str(exc),
                }
            return {
                "kind": kind, "path": relative,
                "other_path": other_relative,
                "difference": difference, "limit": limit, "ok": ok,
            }
        return {"kind": kind, "ok": False, "error": "未知 validator"}

    def verify(self, run_id: str) -> dict:
        path = self.manifest_path(run_id)
        record = read_json(path)
        if not isinstance(record, dict):
            raise ValueError(f"tool run 不存在: {run_id}")
        for log_key in ("stdout", "stderr"):
            relative = record.get("logs", {}).get(log_key)
            expected = record.get("logs", {}).get(f"{log_key}_sha256")
            log_path = safe_relative(self.root, relative or "")
            if not log_path.is_file() or sha256(log_path) != expected:
                record["verification"] = {
                    "time": now(),
                    "status": "failed",
                    "checks": [{
                        "kind": "log_integrity", "path": relative, "ok": False,
                    }],
                }
                atomic_write_json(path, record)
                return record
        record["verification"] = self._verify_record(
            record, validate_validators(record.get("validators", []))
        )
        atomic_write_json(path, record)
        return record
