"""Backward-compatible command router for the adaptive harness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli as legacy_cli
from .coverage import audit_explanation_coverage, initialize_coverage_matrix
from .delivery_core import freeze_delivery, verify_freeze
from .opportunities import render_assumptions
from .optimization import (
    assess_optimization, audit_optimization, build_result_provenance,
    build_review_packet, initialize_constraint_ledger,
    render_constraint_ledger,
)
from .paper import audit_render, render_pdf
from .paper_content import audit_paper_content, initialize_content_coverage
from .paper_ir import compile_paper
from .profiles import ProfileService
from .proposals import ProposalService
from .provenance_core import audit_warm_start_keys, scan_against_baselines
from .repairs import begin_repair, verify_repair
from .requirements import audit_requirements, extract_sources
from .sanitize import render_final
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


def _requirements(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness requirements")
    sub = parser.add_subparsers(dest="action", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--passes", type=int, default=2)
    extract.add_argument("--project", type=Path)
    audit = sub.add_parser("audit")
    audit.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    root = _root(args.project)
    if args.action == "extract":
        _emit(extract_sources(root, passes=args.passes))
        return 0
    errors = audit_requirements(root)
    _emit({"ok": not errors, "errors": errors})
    return int(bool(errors))


def _assumptions(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness assumptions")
    sub = parser.add_subparsers(dest="action", required=True)
    render = sub.add_parser("render")
    render.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    output = render_assumptions(_root(args.project))
    _emit({"ok": True, "path": output.relative_to(_root(args.project)).as_posix()})
    return 0


def _assurance(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness assurance")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in (
        "init", "constraints", "provenance", "review-packet",
        "coverage-init", "status", "audit",
    ):
        command = sub.add_parser(action)
        command.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    root = _root(args.project)
    if args.action == "init":
        _emit(initialize_constraint_ledger(root))
    elif args.action == "constraints":
        output = render_constraint_ledger(root)
        errors = audit_optimization(root, "model")
        _emit({"ok": not errors, "errors": errors, "path": output.relative_to(root).as_posix()})
        return int(bool(errors))
    elif args.action == "provenance":
        _emit(build_result_provenance(root))
    elif args.action == "review-packet":
        _emit(build_review_packet(root))
    elif args.action == "coverage-init":
        _emit(initialize_coverage_matrix(root))
    elif args.action == "status":
        report = assess_optimization(root)
        _emit(report)
        return int(report.get("machine_status") == "BLOCKED")
    else:
        errors = [
            *audit_optimization(root),
            *audit_explanation_coverage(root),
        ]
        _emit({"ok": not errors, "errors": sorted(set(errors))})
        return int(bool(errors))
    return 0


def _provenance(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness provenance")
    sub = parser.add_subparsers(dest="action", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--project", type=Path)
    scan = sub.add_parser("scan")
    scan.add_argument("--root", type=Path, required=True)
    scan.add_argument("--baselines", type=Path, required=True)
    args = parser.parse_args(values)
    if args.action == "audit":
        errors = audit_warm_start_keys(_root(args.project))
    else:
        errors = scan_against_baselines(args.root, args.baselines)
    _emit({"ok": not errors, "errors": errors})
    return int(bool(errors))


def _paper(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness paper")
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source")
    build.add_argument("--output")
    build.add_argument("--template")
    build.add_argument("--timeout", type=int, default=300)
    build.add_argument("--project", type=Path)
    audit = sub.add_parser("audit")
    audit.add_argument("--project", type=Path)
    contract_init = sub.add_parser("contract-init")
    contract_init.add_argument("--project", type=Path)
    content_audit = sub.add_parser("content-audit")
    content_audit.add_argument("--project", type=Path)
    compile_cmd = sub.add_parser("compile")
    compile_cmd.add_argument("--check", action="store_true")
    compile_cmd.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    root = _root(args.project)
    if args.action == "compile":
        report = compile_paper(root, check=args.check)
        _emit(report)
        return int(bool(report.get("errors")))
    if args.action == "contract-init":
        _emit(initialize_content_coverage(root))
        return 0
    if args.action == "content-audit":
        errors = audit_paper_content(root)
        _emit({"ok": not errors, "errors": errors})
        return int(bool(errors))
    if args.action == "audit":
        profile = ProfileService(root).active
        errors = audit_render(root) if profile.get("paper_delivery") else []
        if profile.get("paper_content_contract"):
            errors.extend(audit_paper_content(root))
        _emit({"ok": not errors, "errors": errors})
        return int(bool(errors))
    report = render_pdf(
        root,
        source=args.source,
        output=args.output,
        template=args.template,
        timeout=args.timeout,
    )
    _emit(report)
    return int(report.get("verdict") != "PASS")


def _repair(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness repair")
    sub = parser.add_subparsers(dest="action", required=True)
    begin = sub.add_parser("begin")
    begin.add_argument("--finding", required=True)
    begin.add_argument("--scope", action="append", required=True)
    begin.add_argument("--project", type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("--repair", required=True)
    verify.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    root = _root(args.project)
    if args.action == "begin":
        record = begin_repair(root, args.finding, args.scope)
        _emit({
            "ok": True,
            "id": record["id"],
            "finding": record["finding"],
            "scope": record["scope"],
            "snapshot_files": len(record["snapshot"]),
            "guardrail_baseline": len(record["guardrail_baseline"]),
        })
        return 0
    report = verify_repair(root, args.repair)
    _emit(report)
    return int(not report.get("ok"))


def _deliver(values: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="modelharness deliver")
    sub = parser.add_subparsers(dest="action", required=True)
    render = sub.add_parser("render")
    render.add_argument("--preview", action="store_true")
    render.add_argument("--project", type=Path)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--reason", required=True)
    freeze.add_argument("--project", type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("--project", type=Path)
    args = parser.parse_args(values)
    root = _root(args.project)
    if args.action == "render":
        target = render_final(root, preview=args.preview)
        _emit({
            "ok": True,
            "preview": args.preview,
            "path": target.relative_to(root).as_posix(),
        })
        return 0
    if args.action == "freeze":
        manifest = freeze_delivery(root, args.reason)
        _emit({
            "ok": True,
            "status": "delivered",
            "manifest": "paper/delivery_freeze.json",
            "files": len(manifest["files"]),
            "terminal_review": manifest["terminal_review"]["path"],
        })
        return 0
    report = verify_freeze(root)
    _emit(report)
    return int(not report.get("ok"))


def main() -> int:
    try:
        if len(sys.argv) >= 2 and sys.argv[1] == "tool":
            return tool_main(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "state":
            return _state(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "proposal":
            return _proposal(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "requirements":
            return _requirements(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "assumptions":
            return _assumptions(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "paper":
            return _paper(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "assurance":
            return _assurance(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "provenance":
            return _provenance(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "repair":
            return _repair(sys.argv[2:])
        if len(sys.argv) >= 2 and sys.argv[1] == "deliver":
            return _deliver(sys.argv[2:])
        if len(sys.argv) >= 3 and sys.argv[1] == "task":
            result = _task_extension(sys.argv[2:])
            if result is not None:
                return result
        if len(sys.argv) == 2 and sys.argv[1] == "--version":
            print("modelharness 4.3.0")
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
