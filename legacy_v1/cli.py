from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evidence import EvidenceGraph
from .gates import invalidate, run_gate, status
from .narrative import audit_paper, build_brief
from .scaffold import create
from .util import project_root


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="modelharness")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("new", help="创建建模项目")
    p.add_argument("path")
    p.add_argument("--title", required=True)
    sub.add_parser("status", help="查看阶段和证据状态")
    p = sub.add_parser("evidence", help="管理证据图")
    es = p.add_subparsers(dest="action", required=True)
    add = es.add_parser("add")
    add.add_argument("id"); add.add_argument("--kind", required=True)
    add.add_argument("--statement", required=True); add.add_argument("--artifact", required=True)
    add.add_argument("--depends", default=""); add.add_argument("--check")
    verify = es.add_parser("verify"); verify.add_argument("id")
    revoke = es.add_parser("revoke"); revoke.add_argument("id")
    revoke.add_argument("--reason", required=True)
    es.add_parser("audit")
    p = sub.add_parser("gate"); p.add_argument("stage", choices=[f"s{i}" for i in range(7)])
    p = sub.add_parser("invalidate"); p.add_argument("stage", choices=[f"s{i}" for i in range(7)])
    ns = sub.add_parser("narrative")
    nss = ns.add_subparsers(dest="action", required=True)
    nss.add_parser("build")
    audit = nss.add_parser("audit"); audit.add_argument("--paper", default="paper/draft.md")
    return ap


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "new":
            print(create(Path(args.path), args.title))
            return 0
        root = project_root()
        graph = EvidenceGraph(root)
        if args.command == "status":
            emit({"stages": status(root), "evidence": {
                s: sum(n["status"] == s for n in graph.nodes.values())
                for s in ("candidate", "verified", "rejected", "revoked")
            }, "audit_errors": graph.audit()})
        elif args.command == "evidence":
            if args.action == "add":
                emit(graph.add(args.id, args.kind, args.statement, args.artifact,
                               [x for x in args.depends.split(",") if x], args.check))
            elif args.action == "verify":
                emit(graph.verify(args.id))
            elif args.action == "revoke":
                emit({"revoked": graph.revoke(args.id, args.reason)})
            else:
                errors = graph.audit(); emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
        elif args.command == "gate":
            emit(run_gate(root, args.stage))
        elif args.command == "invalidate":
            emit({"removed": invalidate(root, args.stage)})
        elif args.command == "narrative":
            if args.action == "build":
                print(build_brief(root))
            else:
                errors = audit_paper(root, args.paper)
                emit({"ok": not errors, "errors": errors})
                return int(bool(errors))
        return 0
    except (ValueError, RuntimeError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
