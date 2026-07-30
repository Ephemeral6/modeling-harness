"""Backward-compatible Modeling Harness 3.1 command router."""
from __future__ import annotations

import sys

from . import cli as legacy_cli
from .tool_cli_ext import main as tool_main
from .toolchain_registry import ToolRegistry


_legacy_doctor = legacy_cli.doctor


def _doctor_with_tools(project):
    result = _legacy_doctor(project)
    profile = result.get("profile") or "general"
    tool_report = ToolRegistry(project).doctor(profile)
    result["toolchain"] = tool_report
    result["ok"] = bool(result["ok"] and tool_report["ok"])
    return result


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "tool":
        return tool_main(sys.argv[2:])
    if len(sys.argv) == 2 and sys.argv[1] == "--version":
        print("modelharness 3.1.0")
        return 0
    legacy_cli.doctor = _doctor_with_tools
    return legacy_cli.main()
