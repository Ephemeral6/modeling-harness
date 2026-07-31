from __future__ import annotations

import argparse
import json
from pathlib import Path

from .autopilot import next_packet, resolve_project
from .contracts import STAGES
from .engines import VALID as ENGINE_CHOICES, detect as detect_engines
from .evidence import EvidenceGraph
from .intake import intake
from .integration import audit_integration
from .method_packs import MethodPackRegistry
from .narrative import audit_paper, build_brief
from .problem_graph import ProblemGraph, validate_problem_graph
from .profiles import ProfileService
from .scaffold import create
from .stages import StageService
from .storage import (
    CorruptStateError,
    LockTimeout,
    atomic_write_json,
    read_json,
)
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
    parser.add_argument("--version", action="version", version="%(prog)s 3.0.0")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="创建空白建模项目")
    new.add_argument("path", type=Path)
    new.add_argument("--title", required=True)
    new.add_argument("--engine", choices=ENGINE_CHOICES, default="auto")

    take = sub.add_parser("intake", help="从对话附件创建隔离项目")
    take.add_argument("--title", required=True)
    take.add_argument("--prompt", required=True)
    take.add_argument("--file", action="append", default=[])
    take.add_argument("--root", type=Path, default=Path.cwd())
    take.add_argument("--engine", choices=ENGINE_CHOICES, default="auto")

    for name, help_text in (
        ("status", "汇总问题图、阶段、证据、任务和完整性"),
        ("doctor", "执行只读完整性诊断"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        add_project_option(cmd)

    auto = sub.add_parser("autopilot", help="自治工作流")
    auto_sub = auto.add_subparsers(dest="action", required=True)
    nxt = auto_sub.add_parser("next")
    add_project_option(nxt)

    work = sub.add_parser("work", help="局部研究工作前沿")
    work_sub = work.add_subparsers(dest="action", required=True)
    work_next = work_sub.add_parser("next")
    add_project_option(work_next)

    plan = sub.add_parser("plan", help="管理 Problem Graph")
    plan_sub = plan.add_subparsers(dest="action", required=True)
    for action in ("show", "status", "init"):
        command = plan_sub.add_parser(action)
        add_project_option(command)
    validate = plan_sub.add_parser("validate")
    validate.add_argument("file", type=Path)
    apply = plan_sub.add_parser("apply")
    apply.add_argument("file", type=Path)
    apply.add_argument("--reason", required=True)
    add_project_option(apply)

    ev = sub.add_parser("evidence", help="管理证据图")
    ev_sub = ev.add_subparsers(dest="action", required=True)
    ev_add = ev_sub.add_parser("add")
    ev_add.add_argument("id")
    ev_add.add_argument("--kind", required=True)
    ev_add.add_argument("--statement", required=True)
    ev_add.add_argument("--artifact", required=True)
    ev_add.add_argument("--depends", default="")
    ev_add.add_argument("--check")
    ev_add.add_argument("--checks-json")
    ev_add.add_argument("--obligation-hash")
    ev_add.add_argument("--review", action="append", default=[])
    ev_add.add_argument("--producer-task")
    add_project_option(ev_add)
    ev_revise = ev_sub.add_parser("revise")
    ev_revise.add_argument("id")
    ev_revise.add_argument("--statement")
    ev_revise.add_argument("--artifact")
    ev_revise.add_argument("--depends")
    ev_revise.add_argument("--checks-json")
    ev_revise.add_argument("--obligation-hash")
    ev_revise.add_argument("--review", action="append")
    ev_revise.add_argument("--reason", required=True)
    add_project_option(ev_revise)
    ev_verify = ev_sub.add_parser("verify")
    ev_verify.add_argument("id")
    add_project_option(ev_verify)
    ev_revoke = ev_sub.add_parser("revoke")
    ev_revoke.add_argument("id")
    ev_revoke.add_argument("--reason", required=True)
    add_project_option(ev_revoke)
    ev_audit = ev_sub.add_parser("audit")
    add_project_option(ev_audit)

    gate = sub.add_parser("gate", help="签发里程碑印章")
    gate.add_argument("stage", choices=list(STAGES))
    add_project_option(gate)
    inv = sub.add_parser("invalidate", help="归档并失效下游印章")
    inv.add_argument("stage", choices=list(STAGES))
    inv.add_argument("--reason", required=True)
    add_project_option(inv)

    task = sub.add_parser("task", help="管理 durable 局部研究任务")
    task_sub = task.add_subparsers(dest="action", required=True)
    task_list = task_sub.add_parser("list")
    task_list.add_argument("--work-item")
    add_project_option(task_list)
    claim = task_sub.add_parser("claim")
    claim.add_argument("id")
    claim.add_argument("--worker", required=True)
    claim.add_argument("--lease", type=int, default=900)
    add_project_option(claim)
    beat = task_sub.add_parser("heartbeat")
    beat.add_argument("id")
    beat.add_argument("--worker", required=True)
    beat.add_argument("--lease", type=int, default=900)
    add_project_option(beat)
    finish = task_sub.add_parser("finish")
    finish.add_argument("id")
    finish.add_argument("--worker", required=True)
    finish.add_argument("--failed", action="store_true")
    finish.add_argument("--result", default="{}")
    add_project_option(finish)
    retry = task_sub.add_parser("retry")
    retry.add_argument("id")
    add_project_option(retry)

    profile = sub.add_parser("profile", help="管理交付 Profile")
    profile_sub = profile.add_subparsers(dest="action", required=True)
    for action in ("list", "show"):
        command = profile_sub.add_parser(action)
        add_project_option(command)
    use = profile_sub.add_parser("use")
    use.add_argument("name")
    add_project_option(use)

    pack = sub.add_parser("pack", help="管理方法包")
    pack_sub = pack.add_subparsers(dest="action", required=True)
    for action in ("list", "audit", "lock"):
        command = pack_sub.add_parser(action)
        add_project_option(command)

    narrative = sub.add_parser("narrative")
    narrative_sub = narrative.add_subparsers(dest="action", required=True)
    build = narrative_sub.add_parser("build")
    add_project_option(build)
    audit = narrative_sub.add_parser("audit")
    audit.add_argument("--paper", default="paper/draft.md")
    add_project_option(audit)
    return parser


def doctor(project: Path) -> dict:
    stages = StageService(project)
    graph = EvidenceGraph(project)
    stamp_errors = {
        stage: stages.validate_stamp(stage)
        for stage in STAGES
        if stages.stamp_path(stage).is_file()
    }
    evidence_errors = graph.audit()
    workflow = WorkflowEngine(project)
    problem = ProblemGraph(project)
    problem_states = (
        problem.states(graph.nodes, workflow.list_tasks())
        if problem.exists else {}
    )
    pack_errors = (
        MethodPackRegistry(project).audit() if problem.exists else []
    )
    integration_errors = (
        audit_integration(project) if problem.exists else []
    )
    all_errors = [
        *evidence_errors,
        *pack_errors,
        *integration_errors,
        *[item for values in stamp_errors.values() for item in values],
    ]
    meta = read_json(project / "modeling-project.json")
    return {
        "ok": not all_errors,
        "project": str(project),
        "engine": (
            meta.get("engine", "codex") if isinstance(meta, dict) else "codex"
        ),
        "engines_detected": detect_engines(),
        "profile": (
            ProfileService(project).active["name"] if problem.exists else None
        ),
        "problem_revision": problem.data["revision"] if problem.exists else None,
        "problem_states": problem_states,
        "evidence_errors": evidence_errors,
        "pack_errors": pack_errors,
        "integration_errors": integration_errors,
        "stamp_errors": stamp_errors,
        "valid_stage_prefix": stages.valid_prefix(),
        "current_stage": stages.current(),
        "tasks": workflow.list_tasks(),
    }


def _plan_init(project: Path) -> dict:
    graph = ProblemGraph(project)
    if graph.exists:
        return graph.data
    seed = (
        Path(__file__).parent.parent / "templates" /
        "config" / "problem_graph.seed.json"
    )
    proposal = validate_problem_graph(read_json(seed))
    atomic_write_json(graph.path, proposal)
    MethodPackRegistry(project).refresh_lock()
    return proposal


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "new":
            print(create(args.path, args.title, engine=args.engine))
            return 0
        if args.command == "intake":
            emit(intake(
                args.root, args.title, args.prompt,
                [Path(x) for x in args.file],
                engine=args.engine,
            ))
            return 0
        if args.command == "plan" and args.action == "validate":
            emit(validate_problem_graph(read_json(args.file.resolve())))
            return 0
        project = resolve(args)
        if args.command in {"autopilot", "work"}:
            emit(next_packet(project))
        elif args.command in {"status", "doctor"}:
            result = doctor(project)
            emit(result)
            if args.command == "doctor":
                return int(not result["ok"])
        elif args.command == "plan":
            problem = ProblemGraph(project)
            if args.action == "init":
                emit(_plan_init(project))
            elif args.action == "show":
                emit(problem.data)
            elif args.action == "status":
                workflow = WorkflowEngine(project)
                evidence = EvidenceGraph(project)
                emit({
                    "revision": problem.data["revision"],
                    "states": problem.states(
                        evidence.nodes, workflow.list_tasks()
                    ),
                    "frontier": problem.frontier(
                        evidence.nodes,
                        workflow.list_tasks(),
                        StageService(project).current() or "s6",
                    ),
                })
            elif args.action == "apply":
                proposal = read_json(args.file.resolve())
                result = problem.replace(proposal, args.reason)
                workflow = WorkflowEngine(project)
                for node_id in result["changed_nodes"]:
                    current = problem.nodes.get(node_id)
                    workflow.supersede(
                        node_id,
                        problem.contract_hash(node_id) if current else None,
                    )
                evidence = EvidenceGraph(project)
                affected = []
                for node_id in result["impacted_evidence"]:
                    if (
                        node_id in evidence.nodes
                        and evidence.nodes[node_id]["status"] != "revoked"
                    ):
                        affected.extend(evidence.revoke(
                            node_id, f"problem graph revised: {args.reason}"
                        ))
                affected = sorted(set(affected))
                invalidated = (
                    StageService(project).invalidate_for_evidence(
                        affected, f"problem graph revised: {args.reason}"
                    ) if affected else []
                )
                emit({
                    **result,
                    "revoked": affected,
                    "invalidated": invalidated,
                })
        elif args.command == "evidence":
            graph = EvidenceGraph(project)
            if args.action == "add":
                checks = (
                    json.loads(args.checks_json)
                    if args.checks_json else None
                )
                emit(graph.add(
                    args.id,
                    args.kind,
                    args.statement,
                    args.artifact,
                    [x for x in args.depends.split(",") if x],
                    args.check,
                    checks=checks,
                    obligation_hash=args.obligation_hash,
                    reviews=args.review,
                    producer_task_id=args.producer_task,
                ))
            elif args.action == "revise":
                depends = (
                    [x for x in args.depends.split(",") if x]
                    if args.depends is not None else None
                )
                checks = (
                    json.loads(args.checks_json)
                    if args.checks_json else None
                )
                emit(graph.revise(
                    args.id,
                    statement=args.statement,
                    artifact=args.artifact,
                    depends=depends,
                    checks=checks,
                    obligation_hash=args.obligation_hash,
                    reviews=args.review,
                    reason=args.reason,
                ))
            elif args.action == "verify":
                emit(graph.verify(args.id))
            elif args.action == "revoke":
                affected = graph.revoke(args.id, args.reason)
                emit({
                    "revoked": affected, "cascade": graph.last_cascade,
                })
            else:
                errors = graph.audit()
                emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
        elif args.command == "gate":
            emit(StageService(project).gate(args.stage))
        elif args.command == "invalidate":
            emit({
                "invalidated": StageService(project).invalidate(
                    args.stage, args.reason
                )
            })
        elif args.command == "task":
            engine = WorkflowEngine(project)
            if args.action == "list":
                emit(engine.list_tasks(work_item_id=args.work_item))
            elif args.action == "claim":
                emit(engine.claim(args.id, args.worker, args.lease))
            elif args.action == "heartbeat":
                emit(engine.heartbeat(args.id, args.worker, args.lease))
            elif args.action == "retry":
                emit(engine.retry(args.id))
            else:
                result = json.loads(args.result)
                emit(engine.finish(
                    args.id, args.worker, not args.failed, result
                ))
        elif args.command == "profile":
            service = ProfileService(project)
            if args.action == "list":
                emit(service.list())
            elif args.action == "show":
                emit(service.active)
            else:
                before = service.content_hash()
                selected = service.use(args.name)
                invalidated = (
                    StageService(project).invalidate(
                        "s0", f"delivery profile changed: {args.name}"
                    ) if service.content_hash() != before else []
                )
                emit({"profile": selected, "invalidated": invalidated})
        elif args.command == "pack":
            registry = MethodPackRegistry(project)
            if args.action == "list":
                emit(registry.list())
            elif args.action == "audit":
                errors = registry.audit()
                emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
            else:
                emit(registry.refresh_lock())
        elif args.command == "narrative":
            if args.action == "build":
                print(build_brief(project))
            else:
                errors = audit_paper(project, args.paper)
                emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
        return 0
    except (
        ValueError,
        RuntimeError,
        OSError,
        json.JSONDecodeError,
        CorruptStateError,
        LockTimeout,
    ) as exc:
        emit({
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
