"""Execution facade adding reproducibility, freshness, and recovery."""
from __future__ import annotations

from pathlib import Path

from .checks import normalize_check
from .contracts import safe_relative
from .runtime_state import outcome
from .storage import atomic_write_json, read_json
from .toolchain_execution_core import (
    ToolRunExecutor as _ToolRunExecutor,
    validate_validators,
)
from .util import now, sha256


class ToolRunExecutor(_ToolRunExecutor):
    def run(self, **kwargs) -> dict:
        autonomy = self.registry.autonomy
        validators = validate_validators(kwargs.get("validators"))
        if autonomy["require_validation"] and not validators:
            raise ValueError("自主工具运行至少需要一个机械 validator")
        if (
            autonomy["require_reproducible_run"]
            and not kwargs.get("outputs")
        ):
            raise ValueError("自主工具运行至少需要一个声明输出")
        kwargs["validators"] = validators
        record = super().run(**kwargs)
        record["schema"] = 2
        state = (
            outcome("recovery_pending", "inconclusive")
            if record.get("timed_out")
            else outcome(
                "completed",
                "pass" if record.get("returncode") == 0 else "fail",
            )
        )
        record.update({
            key: state[key]
            for key in (
                "execution_status", "verdict", "authority", "freshness"
            )
        })
        atomic_write_json(self.manifest_path(record["id"]), record)
        return record

    def _run_validator(self, validator: dict) -> dict:
        if validator.get("kind") != "independent_check":
            return super()._run_validator(validator)
        try:
            argv, legacy, _timeout, _expected = normalize_check(
                validator.get("check")
            )
            if legacy or not isinstance(argv, list) or len(argv) < 2:
                raise ValueError("独立验证必须使用结构化 argv")
            runner = self._runner_name(argv[0])
            if runner not in {"python", "python3", "py"}:
                raise ValueError("独立验证目前只允许 Python 检查器")
            relative = Path(argv[1].replace("\\", "/")).as_posix()
            if not relative.startswith("checks/"):
                raise ValueError("独立验证脚本必须位于 checks/")
            path = safe_relative(self.root, relative)
            if not path.is_file():
                raise ValueError(f"独立验证脚本不存在: {relative}")
        except ValueError as exc:
            return {
                "kind": "independent_check",
                "ok": False,
                "error": str(exc),
            }
        return super()._run_validator(validator)

    def _verify_record(
        self, record: dict, validators: list[dict]
    ) -> dict:
        if (
            record.get("timed_out")
            or record.get("execution_status") == "recovery_pending"
        ):
            return {
                "time": now(),
                "status": "recovery_pending",
                **outcome("recovery_pending", "inconclusive"),
                "checks": [{
                    "kind": "process_outcome",
                    "returncode": record.get("returncode"),
                    "ok": False,
                    "reason": "execution outcome is not known",
                }],
            }
        verification = super()._verify_record(record, validators)
        input_checks = []
        for item in record.get("inputs", []):
            path = safe_relative(self.root, item["path"])
            current_hash = sha256(path) if path.is_file() else None
            input_checks.append({
                "kind": "input_freshness",
                "path": item["path"],
                "expected_sha256": item.get("sha256"),
                "current_sha256": current_hash,
                "freshness": (
                    "valid"
                    if current_hash == item.get("sha256")
                    else "missing" if not path.is_file() else "stale"
                ),
                "ok": (
                    path.is_file()
                    and current_hash == item.get("sha256")
                ),
            })
        checks = [*input_checks, *verification.get("checks", [])]
        passed = all(item.get("ok") is True for item in checks)
        freshness = "valid"
        if any(
            item.get("freshness") == "missing" for item in input_checks
        ):
            freshness = "missing"
        elif any(not item.get("ok") for item in input_checks):
            freshness = "stale"
        elif any(
            item.get("kind") == "output_integrity" and not item.get("ok")
            for item in checks
        ):
            freshness = "tampered"
        verification.update({
            "status": "verified" if passed else "failed",
            **outcome(
                "completed",
                "pass" if passed else "fail",
                freshness=freshness,
            ),
            "checks": checks,
        })
        return verification

    def verify(self, run_id: str) -> dict:
        record = super().verify(run_id)
        verification = record.get("verification", {})
        if "execution_status" not in verification:
            status = verification.get("status")
            verification.update(outcome(
                "completed",
                "pass" if status == "verified" else "fail",
                freshness=(
                    "tampered" if status == "failed" else "valid"
                ),
            ))
        record["verification"] = verification
        if verification.get("freshness") in {
            "stale", "missing", "tampered"
        }:
            record["freshness"] = verification["freshness"]
        atomic_write_json(self.manifest_path(run_id), record)
        return record

    def recover(
        self,
        run_id: str,
        outcome_name: str,
        note: str,
        authority: str = "human",
    ) -> dict:
        path = self.manifest_path(run_id)
        record = read_json(path)
        if not isinstance(record, dict):
            raise ValueError(f"tool run does not exist: {run_id}")
        if record.get("execution_status") != "recovery_pending":
            raise ValueError("tool run is not recovery_pending")
        choices = {
            "confirmed_failed": ("error", "fail", "failed"),
            "safe_to_retry": ("error", "inconclusive", "safe_to_retry"),
            "human_required": (
                "recovery_pending", "inconclusive", "recovery_pending"
            ),
            "recovered_success": (
                "completed", "inconclusive", "human_recovered"
            ),
        }
        if outcome_name not in choices:
            raise ValueError(f"unknown recovery outcome: {outcome_name}")
        execution, verdict, status = choices[outcome_name]
        record.update({
            "execution_status": execution,
            "verdict": verdict,
            "authority": authority,
            "recovery": {
                "time": now(),
                "outcome": outcome_name,
                "note": note,
                "authority": authority,
            },
        })
        record.setdefault("verification", {}).update({
            "status": status,
            "execution_status": execution,
            "verdict": verdict,
            "authority": authority,
        })
        atomic_write_json(path, record)
        return record


__all__ = ["ToolRunExecutor", "validate_validators"]
