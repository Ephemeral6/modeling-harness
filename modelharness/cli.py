from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .autopilot import next_packet, resolve_project
from .evidence import EvidenceGraph
from .intake import intake
from .narrative import audit_paper, build_brief
from .scaffold import create
from .stages import StageService
from .storage import CorruptStateError, LockTimeout
from .util import project_root
from .workflow import WorkflowEngine


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def add_project_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path)


def resolve(args) -> Path:
    if getattr(args, "project", None):
        return project_root(args.project)
    try:
        return project_root()
    except ValueError:
        return resolve_project(Path.cwd().resolve())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="modelharness")
    parser.add_argument("--version", action="version", version="%(prog)s 2.0.0")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="创建空白建模项目")
    new.add_argument("path", type=Path)
    new.add_argument("--title", required=True)

    take = sub.add_parser("intake", help="从对话附件创建隔离项目")
    take.add_argument("--title", required=True)
    take.add_argument("--prompt", required=True)
    take.add_argument("--file", action="append", default=[])
    take.add_argument("--root", type=Path, default=Path.cwd())

    status = sub.add_parser("status", help="汇总阶段、证据、任务和完整性")
    add_project_option(status)
    doctor = sub.add_parser("doctor", help="执行只读完整性诊断")
    add_project_option(doctor)

    auto = sub.add_parser("autopilot", help="自治工作流")
    auto_sub = auto.add_subparsers(dest="action", required=True)
    nxt = auto_sub.add_parser("next")
    add_project_option(nxt)

    ev = sub.add_parser("evidence", help="管理证据图")
    ev_sub = ev.add_subparsers(dest="action", required=True)
    ev_add = ev_sub.add_parser("add")
    ev_add.add_argument("id"); ev_add.add_argument("--kind", required=True)
    ev_add.add_argument("--statement", required=True)
    ev_add.add_argument("--artifact", required=True)
    ev_add.add_argument("--depends", default="")
    ev_add.add_argument("--check")
    add_project_option(ev_add)
    ev_verify = ev_sub.add_parser("verify")
    ev_verify.add_argument("id"); add_project_option(ev_verify)
    ev_revoke = ev_sub.add_parser("revoke")
    ev_revoke.add_argument("id"); ev_revoke.add_argument("--reason", required=True)
    add_project_option(ev_revoke)
    ev_audit = ev_sub.add_parser("audit"); add_project_option(ev_audit)

    gate = sub.add_parser("gate", help="签发阶段印章")
    gate.add_argument("stage", choices=[f"s{i}" for i in range(7)])
    add_project_option(gate)
    inv = sub.add_parser("invalidate", help="归档并失效下游印章")
    inv.add_argument("stage", choices=[f"s{i}" for i in range(7)])
    inv.add_argument("--reason", required=True)
    add_project_option(inv)

    task = sub.add_parser("task", help="管理 durable subagent 任务")
    task_sub = task.add_subparsers(dest="action", required=True)
    task_list = task_sub.add_parser("list"); add_project_option(task_list)
    claim = task_sub.add_parser("claim")
    claim.add_argument("id"); claim.add_argument("--worker", required=True)
    claim.add_argument("--lease", type=int, default=900); add_project_option(claim)
    beat = task_sub.add_parser("heartbeat")
    beat.add_argument("id"); beat.add_argument("--worker", required=True)
    beat.add_argument("--lease", type=int, default=900); add_project_option(beat)
    finish = task_sub.add_parser("finish")
    finish.add_argument("id"); finish.add_argument("--worker", required=True)
    finish.add_argument("--failed", action="store_true")
    finish.add_argument("--result", default="{}"); add_project_option(finish)

    narrative = sub.add_parser("narrative")
    narrative_sub = narrative.add_subparsers(dest="action", required=True)
    build = narrative_sub.add_parser("build"); add_project_option(build)
    audit = narrative_sub.add_parser("audit")
    audit.add_argument("--paper", default="paper/draft.md"); add_project_option(audit)
    return parser


def doctor(project: Path) -> dict:
    stages = StageService(project)
    graph = EvidenceGraph(project)
    stamp_errors = {
        stage: stages.validate_stamp(stage)
        for stage in stages.valid_prefix()
    }
    evidence_errors = graph.audit()
    workflow = WorkflowEngine(project)
    return {
        "ok": not evidence_errors and not any(stamp_errors.values()),
        "project": str(project), "evidence_errors": evidence_errors,
        "stamp_errors": stamp_errors,
        "valid_stage_prefix": stages.valid_prefix(),
        "current_stage": stages.current(),
        "tasks": workflow.list_tasks(),
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "new":
            print(create(args.path, args.title)); return 0
        if args.command == "intake":
            emit(intake(args.root, args.title, args.prompt,
                        [Path(x) for x in args.file])); return 0
        project = resolve(args)
        if args.command == "autopilot":
            emit(next_packet(project))
        elif args.command == "status":
            emit(doctor(project))
        elif args.command == "doctor":
            result = doctor(project); emit(result); return int(not result["ok"])
        elif args.command == "evidence":
            graph = EvidenceGraph(project)
            if args.action == "add":
                emit(graph.add(
                    args.id, args.kind, args.statement, args.artifact,
                    [x for x in args.depends.split(",") if x], args.check,
                ))
            elif args.action == "verify":
                emit(graph.verify(args.id))
            elif args.action == "revoke":
                affected = graph.revoke(args.id, args.reason)
                # Conservative: evidence revocation invalidates all signed stages.
                removed = StageService(project).invalidate("s0", f"evidence revoke: {args.id}")
                emit({"revoked": affected, "invalidated": removed})
            else:
                errors = graph.audit(); emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
        elif args.command == "gate":
            emit(StageService(project).gate(args.stage))
        elif args.command == "invalidate":
            emit({"invalidated": StageService(project).invalidate(
                args.stage, args.reason
            )})
        elif args.command == "task":
            engine = WorkflowEngine(project)
            if args.action == "list":
                emit(engine.list_tasks())
            elif args.action == "claim":
                emit(engine.claim(args.id, args.worker, args.lease))
            elif args.action == "heartbeat":
                emit(engine.heartbeat(args.id, args.worker, args.lease))
            else:
                result = json.loads(args.result)
                emit(engine.finish(args.id, args.worker, not args.failed, result))
        elif args.command == "narrative":
            if args.action == "build":
                print(build_brief(project))
            else:
                errors = audit_paper(project, args.paper)
                emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
        return 0
    except (
        ValueError, RuntimeError, OSError, json.JSONDecodeError,
        CorruptStateError, LockTimeout,
    ) as exc:
        emit({"ok": False, "error": type(exc).__name__, "message": str(exc)})
        return 1
