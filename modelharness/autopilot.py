from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import validate_review
from .evidence import EvidenceGraph
from .playbooks import PLAYBOOKS
from .stages import StageService
from .storage import read_json
from .workflow import WorkflowEngine


def resolve_project(root: Path, explicit: Path | None = None) -> Path:
    if explicit:
        project = explicit.resolve()
    elif (root / "modeling-project.json").is_file():
        project = root.resolve()
    else:
        pointer = read_json(root / "projects" / ".current.json")
        if not isinstance(pointer, dict) or "project" not in pointer:
            raise ValueError("找不到当前项目；请先执行 intake")
        project = (root / pointer["project"]).resolve()
        if not project.is_relative_to(root.resolve()):
            raise ValueError("当前项目指针越出 Harness 根目录")
    if not (project / "modeling-project.json").is_file():
        raise ValueError(f"项目无效: {project}")
    return project


def next_packet(project: Path) -> dict:
    project = project.resolve()
    stages = StageService(project)
    stage = stages.current()
    engine = WorkflowEngine(project)
    recovered = engine.reconcile()
    if stage is None:
        engine.event("workflow.completed", {"project": str(project)})
        return {
            "continue": False, "terminal": "completed", "stage": None,
            "recovered_leases": recovered,
            "instruction": "S0–S6 印章链有效。运行最终完整性审计并交付。",
        }
    spec = stages.config[stage]
    graph = EvidenceGraph(project)
    nodes = graph.nodes
    missing = [
        node_id for node_id in spec["requires"]
        if nodes.get(node_id, {}).get("status") != "verified"
    ]
    reviews = []
    for relative in spec["reviews"]:
        path = project / relative
        verdict = None
        error = None
        if path.is_file():
            try:
                verdict = validate_review(read_json(path), path)["verdict"].upper()
            except ValueError as exc:
                error = str(exc)
        reviews.append({
            "path": relative, "exists": path.is_file(),
            "verdict": verdict, "error": error,
        })
    rejected = [x for x in reviews if x["verdict"] == "REJECT" or x["error"]]
    absent = [x for x in reviews if not x["exists"]]
    if missing:
        phase = "build_evidence"
    elif rejected:
        phase = "repair"
    elif absent:
        phase = "review"
    else:
        phase = "gate"
    planned = []
    if phase == "build_evidence":
        for role, description, owns in PLAYBOOKS[stage]["workers"]:
            try:
                task = engine.ensure_task(
                    f"{stage}:{role}", stage, role, description, owns,
                    inputs=["problem/statement.md", "modeling-project.json"],
                    acceptance=[f"产物存在: {item}" for item in owns],
                    budget={"max_attempts": 3},
                )
            except ValueError as exc:
                task = {"role": role, "status": "conflict", "error": str(exc)}
            planned.append(task)
    active = engine.list_tasks(stage)
    instructions = {
        "build_evidence": "claim pending 任务并创建对应 subagent；完成后验证实际产物。",
        "repair": "依据审核 finding 撤销受影响证据和下游印章，创建修复任务并冷启动复审。",
        "review": f"创建无状态 {PLAYBOOKS[stage]['reviewer']}，只提供正式输入和产物。",
        "gate": f"运行 `modelharness gate {stage}`，成功后立即再次运行 autopilot next。",
    }
    packet = {
        "continue": True, "terminal": None, "project": str(project),
        "stage": stage, "valid_stage_prefix": stages.valid_prefix(),
        "phase": phase, "objective": PLAYBOOKS[stage]["objective"],
        "missing_verified_evidence": missing, "reviews": reviews,
        "independent_reviewer": PLAYBOOKS[stage]["reviewer"],
        "tasks": active or planned, "recovered_leases": recovered,
        "instruction": instructions[phase],
        "stop_policy": (
            "仅 S6 完成、需要新授权/用户输入，或同一阻断连续三轮时停止。"
            "阶段完成、单次失败或 subagent 完成不是停止条件。"
        ),
    }
    engine.event("workflow.tick", {
        "stage": stage, "phase": phase,
        "missing": missing, "recovered": recovered,
    })
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description="鲁棒 S0–S6 工作流控制器")
    sub = parser.add_subparsers(dest="command", required=True)
    nxt = sub.add_parser("next")
    nxt.add_argument("--project", type=Path)
    args = parser.parse_args()
    try:
        project = resolve_project(Path.cwd().resolve(), args.project)
        print(json.dumps(next_packet(project), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
