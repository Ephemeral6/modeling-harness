"""3.1 extension commands layered on the stable tool CLI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .tool_cli import main as base_main
from .tool_cli import emit, _project
from .toolchain import ToolchainService


def _fallback(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="modelharness tool fallback",
        description="在工具失败后显式选择下一可用后端",
    )
    parser.add_argument("node_id")
    parser.add_argument("--failed-tool", action="append", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--allow-skip", action="store_true")
    parser.add_argument("--project", type=Path)
    args = parser.parse_args(argv)
    try:
        root = _project(args.project)
        emit(ToolchainService(root).fallback(
            args.node_id,
            args.failed_tool,
            args.reason,
            allow_skip=args.allow_skip,
        ))
        return 0
    except (
        ValueError,
        RuntimeError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        emit({
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        })
        return 1


def main(argv: list[str] | None = None) -> int:
    values = list(argv or [])
    if values and values[0] == "fallback":
        return _fallback(values[1:])
    return base_main(values)
