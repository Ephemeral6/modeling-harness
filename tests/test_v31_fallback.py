from pathlib import Path

from modelharness.scaffold import create
from modelharness.toolchain import ToolchainService


def test_failed_primary_tool_can_be_replaced_by_ranked_local_fallback(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "fallback")
    service = ToolchainService(root)
    service.decide(
        "s3.solver_validation",
        "use",
        "首选 NumPy 正式求解",
        tools=["numpy"],
    )
    revised = service.fallback(
        "s3.solver_validation",
        ["numpy"],
        "主后端在该实例上发生数值失败",
    )
    selected = {item["id"] for item in revised["selected_tools"]}
    assert revised["revision"] == 2
    assert "numpy" not in selected
    assert "scipy" in selected
    assert revised["history"][-1]["action"] == "use"
