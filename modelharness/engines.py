"""Agent engine registry: run the harness on Codex or Claude Code.

The Python state machine (Problem Graph / Evidence / Workflow / Toolchain)
is engine-agnostic. Engine coupling lives only in the conversation-contract
layer: which entry file the agent reads on session start, and how a headless
review/worker session is spawned. This module is the single source of truth
for those differences.
"""
from __future__ import annotations

import shutil

ENGINES: dict[str, dict] = {
    "codex": {
        "name": "codex",
        "display": "Codex (Desktop / CLI)",
        # Codex reads AGENTS.md automatically on session start.
        "entry_files": ["AGENTS.md"],
        "cli_candidates": ["codex", "codex.cmd"],
        # Headless one-shot invocation; prompt goes through stdin because
        # multi-line argv arguments get truncated on Windows.
        "headless_argv": ["exec", "--full-auto", "-"],
        "prompt_via": "stdin",
    },
    "claude-code": {
        "name": "claude-code",
        "display": "Claude Code (CLI / Desktop)",
        # Claude Code reads CLAUDE.md automatically on session start.
        "entry_files": ["CLAUDE.md", "AGENTS.md"],
        "cli_candidates": ["claude", "claude.cmd"],
        "headless_argv": [
            "-p", "--output-format", "json",
            "--permission-mode", "bypassPermissions",
        ],
        "prompt_via": "stdin",
    },
}

VALID = sorted(ENGINES) + ["auto"]


def find_cli(engine: str) -> str | None:
    """Return the resolved executable for an engine, or None."""
    for candidate in ENGINES[engine]["cli_candidates"]:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def detect() -> dict[str, dict]:
    """Availability report for every known engine."""
    report = {}
    for key, spec in ENGINES.items():
        cli = find_cli(key)
        report[key] = {
            "display": spec["display"],
            "available": cli is not None,
            "cli": cli,
            "entry_files": spec["entry_files"],
        }
    return report


def resolve(engine: str = "auto") -> str:
    """Resolve an engine name; 'auto' prefers whichever CLI is installed.

    Auto-resolution never fails hard: with neither CLI on PATH the project
    is still usable interactively, so we fall back to 'codex' (the
    historical default) and let doctor report the missing CLI.
    """
    if engine in ENGINES:
        return engine
    if engine != "auto":
        raise ValueError(
            f"未知引擎: {engine}（可选: {', '.join(VALID)}）"
        )
    for key in ("codex", "claude-code"):
        if find_cli(key):
            return key
    return "codex"


def headless_command(engine: str) -> list[str] | None:
    """Full argv to spawn a one-shot headless session, or None if the
    engine CLI is not installed. The caller feeds the prompt via stdin."""
    cli = find_cli(engine)
    if not cli:
        return None
    return [cli, *ENGINES[engine]["headless_argv"]]
