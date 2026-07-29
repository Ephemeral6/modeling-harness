from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evidence import EvidenceGraph
from .gates import ORDER, load
from .util import now, read_json, write_json


PLAYBOOKS = {
    "s0": {
        "objective": "解析题面、附件、决策问题、成功标准和失效出口",
        "parallel": [
            {"role": "problem-architect", "owns": ["docs/success_criteria.md"],
             "task": "独立形式化问题、目标、约束和交付物"},
            {"role": "data-scout", "owns": ["docs/data_inventory.md"],
             "task": "只读扫描附件、字段、单位、缺失与数据风险"},
        ],
        "reviewer": "red-team",
    },
    "s1": {
        "objective": "让结构不同的候选模型竞争并形成正式规格",
        "parallel": [
            {"role": "modeler-a", "owns": ["docs/candidates/cand_a.md"],
             "task": "提出最简机制模型与可证伪预测"},
            {"role": "modeler-b", "owns": ["docs/candidates/cand_b.md"],
             "task": "提出统计/数据驱动候选与可辨识性分析"},
            {"role": "modeler-c", "owns": ["docs/candidates/cand_c.md"],
             "task": "提出优化/仿真候选与复杂度分析"},
        ],
        "reviewer": "referee",
    },
    "s2": {
        "objective": "完成数据血缘、可复现清洗和参数估计",
        "parallel": [
            {"role": "data-engineer", "owns": ["src/data_prep.py", "data/processed/"],
             "task": "实现可复现数据管线，不修改原始数据"},
            {"role": "estimation-engineer", "owns": ["src/estimate.py", "results/parameters.json"],
             "task": "实现估计、诊断可辨识性并输出机器结果"},
        ],
        "reviewer": "data-auditor",
    },
    "s3": {
        "objective": "实现求解器并通过 toy-first 分层数值验证",
        "parallel": [
            {"role": "solver-engineer", "owns": ["src/solver.py", "results/nominal.json"],
             "task": "实现唯一正式求解器、检查点和确定性种子"},
            {"role": "toy-designer", "owns": ["checks/l2_toy.py", "results/toy.json"],
             "task": "构造已知解、极限情形和独立对拍"},
        ],
        "reviewer": "numerics-auditor",
    },
    "s4": {
        "objective": "完成留出验证、UQ、稳健性、基线和反事实红队",
        "parallel": [
            {"role": "uq-worker", "owns": ["src/uq.py", "results/uq.json"],
             "task": "传播参数与随机不确定性，报告区间而非仅点值"},
            {"role": "baseline-worker", "owns": ["src/baselines.py", "results/baselines.json"],
             "task": "独立实现简单强基线和留出比较"},
            {"role": "sensitivity-worker", "owns": ["src/sensitivity.py", "results/sensitivity.json"],
             "task": "搜索结论翻转边界、失效域和反例"},
        ],
        "reviewer": "red-team",
    },
    "s5": {
        "objective": "把验证结果变成带条件的决策并锁定",
        "parallel": [
            {"role": "decision-analyst", "owns": ["docs/decision_answer.md"],
             "task": "综合收益、风险、基线、稳定范围和限制"},
            {"role": "replication-worker", "owns": ["results/replication.json"],
             "task": "用独立种子复跑入围方案并检查排序稳定性"},
        ],
        "reviewer": "red-team",
    },
    "s6": {
        "objective": "从 verified 证据生成论文并逐句复核",
        "parallel": [
            {"role": "narrative-writer", "owns": ["paper/draft.md"],
             "task": "按问题—困难—机制—证据—决策—边界写作"},
            {"role": "figure-editor", "owns": ["paper/figures/"],
             "task": "只从 results 生成回答明确问题的图表"},
        ],
        "reviewer": "paper-verifier",
    },
}


def resolve_project(root: Path, explicit: Path | None) -> Path:
    if explicit:
        return explicit.resolve()
    pointer = root / "projects" / ".current.json"
    if pointer.exists():
        data = read_json(pointer, {})
        return (root / data["project"]).resolve()
    if (root / "modeling-project.json").exists():
        return root
    raise ValueError("找不到当前项目；请先执行对话式 Intake")


def state_path(project: Path) -> Path:
    return project / ".harness" / "autopilot.json"


def load_state(project: Path) -> dict:
    return read_json(state_path(project), {
        "schema": 1, "mode": "autonomous", "started_at": now(),
        "last_tick": None, "loops": {}, "events": [],
    })


def save_event(project: Path, event: str, detail: str = "") -> dict:
    state = load_state(project)
    state["last_tick"] = now()
    state["events"].append({"time": now(), "event": event, "detail": detail})
    state["events"] = state["events"][-100:]
    write_json(state_path(project), state)
    return state


def current_stage(project: Path) -> str | None:
    for stage in ORDER:
        if not (project / ".harness" / "stamps" / f"{stage}.json").exists():
            return stage
    return None


def next_packet(project: Path) -> dict:
    stage = current_stage(project)
    if stage is None:
        return {
            "continue": False, "terminal": "completed", "stage": None,
            "instruction": "S0–S6 全部通过。运行最终完整性检查并向用户交付。",
        }
    graph = EvidenceGraph(project)
    spec = load(project)[stage]
    missing = [
        node_id for node_id in spec.get("requires", [])
        if graph.nodes.get(node_id, {}).get("status") != "verified"
    ]
    review_status = []
    for item in spec.get("reviews", []):
        path = project / item
        data = read_json(path, {}) if path.exists() else {}
        review_status.append({
            "path": item, "exists": path.exists(),
            "verdict": str(data.get("verdict", "")).upper() or None,
        })
    rejected = [x for x in review_status if x["verdict"] == "REJECT"]
    absent = [x for x in review_status if not x["exists"]]
    if missing:
        phase = "build_evidence"
        instruction = "并行完成缺失证据；工作者不得共享写入文件。完成后逐项 verify。"
    elif rejected:
        phase = "repair_loop"
        instruction = "按独立审核 findings 修复，撤销受影响证据，再冷启动复审。"
    elif absent:
        phase = "independent_review"
        instruction = "创建无状态审核 subagent；只给题面、规格、产物和检查，不给生成过程。"
    else:
        phase = "gate"
        instruction = f"运行机械检查与 `python -m modelharness gate {stage}`；通过后立即进入下一阶段。"
    packet = {
        "continue": True, "terminal": None, "project": str(project),
        "stage": stage, "phase": phase, "objective": PLAYBOOKS[stage]["objective"],
        "missing_verified_evidence": missing, "reviews": review_status,
        "parallel_agent_plan": PLAYBOOKS[stage]["parallel"],
        "independent_reviewer": PLAYBOOKS[stage]["reviewer"],
        "instruction": instruction,
        "persistence_rule": (
            "不要在本阶段结束后向用户交还控制权；若 gate 通过，立即再次调用 "
            "autopilot next 并继续。仅 S6 完成、需要用户授权或连续三轮同一阻塞时停止。"
        ),
    }
    save_event(project, "tick", f"{stage}:{phase}")
    return packet


def main() -> int:
    ap = argparse.ArgumentParser(description="S0–S6 autonomous control loop")
    sub = ap.add_subparsers(dest="command", required=True)
    nxt = sub.add_parser("next")
    nxt.add_argument("--project", type=Path)
    rec = sub.add_parser("record")
    rec.add_argument("event")
    rec.add_argument("--detail", default="")
    rec.add_argument("--project", type=Path)
    args = ap.parse_args()
    try:
        project = resolve_project(Path.cwd().resolve(), args.project)
        result = (next_packet(project) if args.command == "next"
                  else save_event(project, args.event, args.detail))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
