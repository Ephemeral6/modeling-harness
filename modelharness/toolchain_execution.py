"""Execution facade enforcing reproducibility and reviewable validators."""
from __future__ import annotations

from pathlib import Path

from .checks import normalize_check
from .contracts import safe_relative
from .toolchain_execution_core import (
    ToolRunExecutor as _ToolRunExecutor,
    validate_validators,
)


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
        return super().run(**kwargs)

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


__all__ = ["ToolRunExecutor", "validate_validators"]
