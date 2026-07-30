"""Backward-compatible command router for the adaptive harness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli as legacy_cli
from .proposals import ProposalService
from .supervisor import StateCapsule
from .tool_cli_ext import main as tool_main
from .toolchain_registry import ToolRegistry
from .util import project_root
from .workflow import WorkflowEngine


_legacy_doctor = legacy_cli.doctor


def _doctor_with_tools(project):
    result = _legacy_doctor(project)
    profile = result.get("profile") or "general"
    tool_report = ToolRegistry(project).doctor(profile)
    result["toolchain"] = tool_report
    result["ok"] = bool(result["ok"] and tool_report["ok"])
    return result


def _root(value: Path | None) -> Path:
    return project_root(value or Path.cwd())


def _emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _task_extension(values: list[str]) -> int | None:
    if not values or values[0] not in {
        "recover", "recovery-pending"
    }:
        return None
    action = values[0]
    parser = argparse.ArgumentParser(
        prog=f"modelharness task {action}"
    )
    parser.add_argument("id")
    parser.add_argument("--project", type=Path)
    if action == "recover":
        parser.add_argument(
            "--outcome",
            required=True,
            choices=[
                "recovered_success", "confirmed_failed",
                "safe_to_retry", "human_required",
            ],
        )
        parser.add_argument("--note", required=True)
        parser.add_argument(
            "--authority",
            choices=["machine", "human", "hybrid"],
            default="human",
        )
    else:
        parser.add_argument("--worker", required=True)
        parser.add_argument("--note", required=True)
        parser.add_argument("--result", default="{}")
    args = parser.parse_args(values[1:])
    root = _root(args.project)
    engine = WorkflowEngine(root)
    if action == "recover":
        _emit(engine.resolve_recovery(
            args.id, args.outcome, args.note, args.authority
        ))
    else:
        _emit(engine.mark_recovery_pending(
            args.id, args.worker, json.loads(args.result), args.note
        ))
    return 0


def _state(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness state")
    parser.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    _emit(StateCapsule(_root(args.project)).build())
    return 0


def _proposal(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness proposal")
    sub = parser.add_subparsers(dest="action", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--project", type=Path)
    show = sub.add_parser("show")
    show.add_argument("id")
    show.add_argument("--project", type=Path)
    submit = sub.add_parser("submit")
    submit.add_argument("--action", required=True)
    submit.add_argument("--actor", default="agent")
    submit.add_argument("--reason", required=True)
    submit.add_argument("--target-json", default="{}")
    submit.add_argument(
        "--risk",
        choices=[
            "local", "network", "commercial",
            "installation", "external_write", "destructive",
        ],
        default="local",
    )
    submit.add_argument("--side-effect", default="project_local")
    submit.add_argument("--expected-artifact", action="append", default=[])
    submit.add_argument("--verification-json", default="[]")
    submit.add_argument("--user-authorized", action="store_true")
    submit.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    service = ProposalService(_root(args.project))
    if args.action == "list":
        _emit(service.list())
    elif args.action == "show":
        _emit(service.get(args.id))
    else:
        _emit(service.submit(
            action=args.action,
            actor=args.actor,
            reason=args.reason,
            target=json.loads(args.target_json),
            risk=args.risk,
            side_effect=args.side_effect,
            expected_artifacts=args.expected_artifact,
            verification=json.loads(args.verification_json),
            user_authorized=args.user_authorized,
        ))
    return 0


def main() -> int:
    try:
        if len(sys.argv) >= 2 and sys.argv[1] == "tool":
            return tool_main(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "state":
            return _state(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "proposal":
            return _proposal(sys.argv[2:])
        if len(sys.argv) >= 3 and sys.argv[1] == "task":
            result = _task_extension(sys.argv[2:])
            if result is not None:
                return result
        if len(sys.argv) == 2 and sys.argv[1] == "--version":
            print("modelharness 4.0.0")
            return 0
        legacy_cli.doctor = _doctor_with_tools
        return legacy_cli.main()
    except (
        ValueError, RuntimeError, OSError, json.JSONDecodeError
    ) as exc:
        _emit({
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
