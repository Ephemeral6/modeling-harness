"""CLI surface for autonomous mathematical-computation tools."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .autopilot import resolve_project
from .profiles import ProfileService
from .toolchain import ToolchainService
from .toolchain_registry import ToolRegistry
from .util import project_root


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _project(value: Path | None) -> Path:
    if value:
        return project_root(value)
    try:
        return project_root()
    except ValueError:
        return resolve_project(Path.cwd().resolve())


def _add_project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path)


def _load_json(
    root: Path, inline: str | None, file: Path | None, default
):
    if file:
        path = file if file.is_absolute() else root / file
        return json.loads(path.read_text(encoding="utf-8"))
    if inline is not None:
        return json.loads(inline)
    return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modelharness tool",
        description="自主计算工具注册、选择、运行和审计",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("list", "audit", "lock", "capabilities"):
        command = sub.add_parser(name)
        _add_project(command)
        if name == "audit":
            command.add_argument("--environment", action="store_true")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--profile")
    _add_project(doctor)
    env = sub.add_parser("env")
    env_sub = env.add_subparsers(dest="env_action", required=True)
    env_list = env_sub.add_parser("list")
    _add_project(env_list)
    env_show = env_sub.add_parser("show")
    env_show.add_argument("name")
    _add_project(env_show)
    recommend = sub.add_parser("recommend")
    recommend.add_argument("node_id")
    recommend.add_argument("--capability", action="append", default=[])
    _add_project(recommend)
    decide = sub.add_parser("decide")
    decide.add_argument("node_id")
    decide.add_argument(
        "--action", dest="decision_action",
        choices=("auto", "use", "skip"), required=True,
    )
    decide.add_argument("--reason", required=True)
    decide.add_argument("--tool", action="append", default=[])
    decide.add_argument("--capability", action="append", default=[])
    _add_project(decide)
    run = sub.add_parser("run")
    run.add_argument("node_id")
    argv_group = run.add_mutually_exclusive_group(required=True)
    argv_group.add_argument("--argv-json")
    argv_group.add_argument("--argv-file", type=Path)
    run.add_argument("--tool", action="append", default=[])
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--output", action="append", default=[])
    validator_group = run.add_mutually_exclusive_group()
    validator_group.add_argument("--validators-json")
    validator_group.add_argument("--validators-file", type=Path)
    run.add_argument("--seed", type=int, default=0)
    run.add_argument("--timeout", type=int, default=1800)
    _add_project(run)
    runs = sub.add_parser("runs")
    runs.add_argument("--node")
    _add_project(runs)
    verify = sub.add_parser("verify")
    verify.add_argument("run_id")
    _add_project(verify)
    audit_node = sub.add_parser("audit-node")
    audit_node.add_argument("node_id")
    _add_project(audit_node)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _project(args.project)
        registry = ToolRegistry(root)
        if args.action == "list":
            emit([
                {**tool, "probe": registry.probe(tool["id"])}
                for tool in registry.list()
            ])
        elif args.action == "capabilities":
            result: dict[str, list[str]] = {}
            for tool in registry.list():
                for capability in tool.get("capabilities", []):
                    result.setdefault(capability, []).append(tool["id"])
            emit({
                key: sorted(value) for key, value in sorted(result.items())
            })
        elif args.action == "doctor":
            profile = args.profile or ProfileService(root).active["name"]
            report = registry.doctor(profile)
            emit(report)
            return int(not report["ok"])
        elif args.action == "audit":
            errors = registry.audit_catalog()
            drift = registry.environment_drift() if args.environment else []
            emit({
                "ok": not errors and not drift,
                "catalog_errors": errors,
                "environment_drift": drift,
            })
            return int(bool(errors or drift))
        elif args.action == "lock":
            emit({
                "catalog": registry.refresh_catalog_lock(),
                "environment": registry.lock_environment(),
            })
        elif args.action == "env":
            if args.env_action == "list":
                emit(registry.environment_profiles())
            else:
                emit(registry.environment_profile(args.name))
        else:
            service = ToolchainService(root)
            if args.action == "recommend":
                emit(service.recommend(
                    args.node_id,
                    extra_capabilities=args.capability,
                    persist=True,
                ))
            elif args.action == "decide":
                emit(service.decide(
                    args.node_id,
                    args.decision_action,
                    args.reason,
                    tools=args.tool or None,
                    extra_capabilities=args.capability,
                ))
            elif args.action == "run":
                argv_value = _load_json(
                    root, args.argv_json, args.argv_file, None
                )
                validators = _load_json(
                    root, args.validators_json, args.validators_file, []
                )
                if not isinstance(argv_value, list):
                    raise ValueError("argv 必须解析为数组")
                record = service.run(
                    args.node_id,
                    argv=argv_value,
                    inputs=args.input,
                    outputs=args.output,
                    validators=validators,
                    tool_ids=args.tool or None,
                    seed=args.seed,
                    timeout=args.timeout,
                )
                emit(record)
                return int(
                    record.get("verification", {}).get("status") != "verified"
                )
            elif args.action == "runs":
                emit(service.executor.list(args.node))
            elif args.action == "verify":
                record = service.executor.verify(args.run_id)
                emit(record)
                return int(
                    record.get("verification", {}).get("status") != "verified"
                )
            elif args.action == "audit-node":
                errors = service.audit_decision(args.node_id, required=True)
                emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
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
