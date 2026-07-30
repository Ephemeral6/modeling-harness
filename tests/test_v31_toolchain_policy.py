from __future__ import annotations

import sys
from pathlib import Path

import pytest

from modelharness.scaffold import create
from modelharness.toolchain import ToolchainService


def test_autonomous_run_cannot_use_exit_code_as_only_validation(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "validation policy")
    service = ToolchainService(root)
    service.decide(
        "s0.problem_definition",
        "use",
        "验证强制机械检查策略",
        tools=["python"],
    )
    with pytest.raises(ValueError, match="validator"):
        service.run(
            "s0.problem_definition",
            argv=[sys.executable, "-c", "print('not enough')"],
            inputs=[],
            outputs=["results/not-created.json"],
            validators=[],
            tool_ids=["python"],
            seed=0,
            timeout=30,
        )
