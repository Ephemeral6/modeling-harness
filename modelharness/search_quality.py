"""Search-quality assurance for optimization-heavy modeling tasks (v4.5).

Three purely-mechanical, read-only audits over existing on-disk artifacts:

* ``audit_search_budget`` — every ``searches[].budget`` telemetry block in
  ``results/research_diagnostics.json`` must be pinned to a real tool run
  under ``.harness/tool_runs/`` whose outputs cover the diagnostics file,
  carry a complete field set, and stay consistent with its own time limit.
* ``audit_incumbent_dominance`` — hard combinatorial problems must publish
  cheap constructive baselines (``results/incumbent_dominance_audit.json``)
  and none of the checker-passed baselines may strictly beat the incumbent.
* ``audit_search_portfolio`` — ``results/search_portfolio.json`` governance:
  the selected arm must be the portfolio optimum, declared warm starts need
  a cold-start control arm, and starved searches need an escalation attempt.

Everything is gated on :func:`modelharness.optimization.optimization_relevant`
plus a ``search_quality.mode`` escape hatch inside
``config/optimization_assurance.json``, so legacy and non-optimization
projects (for example runs that store solver telemetry under
``cases[].component_solver``) always yield zero errors.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from .contracts import safe_relative
from .opportunities import detect_search_starvation
from .optimization import optimization_relevant
from .storage import read_json
from .util import sha256


BASELINE_KINDS = {"greedy", "lp_rounding", "local_search", "prior_feasible"}
ARM_KINDS = {"cold_start", "warm_start", "seed_variant", "alt_method"}
TERMINATIONS = {
    "optimal",
    "time_limit",
    "node_limit",
    "memory_limit",
    "gap_limit",
    "iteration_limit",
    "solution_limit",
    "infeasible",
    "unbounded",
    "interrupted",
    "error",
}
BUDGET_NUMERIC_FIELDS = (
    "time_limit_seconds",
    "wall_time_seconds",
    "nodes_explored",
    "final_gap_fraction",
)
DEFAULT_WALL_TIME_OVERRUN_FACTOR = 1.5
RELATIVE_TOLERANCE = 1e-9
_COMBINATORIAL_RE = re.compile(
    r"整数|0-1|0/1|二元|布尔|组合|指派|排列|匹配|背包|选址|"
    r"integer|binary|boolean|combinatorial|assignment|permutation|matching|"
    r"knapsack|\bmip\b|\bilp\b|(?:in|∈)\s*\{\s*0\s*,\s*1\s*\}",
    re.IGNORECASE,
)


def _config(root: Path) -> dict[str, Any]:
    value = read_json(root / "config" / "optimization_assurance.json", {})
    section = value.get("search_quality") if isinstance(value, dict) else None
    return section if isinstance(section, dict) else {}


def _contract_declared(root: Path) -> bool:
    """True only when the project generation declared the 4.5 config section.

    Demands for files that pre-4.5 generations never produced (the cheap
    baseline audit and the search portfolio) are gated on this declaration:
    projects scaffolded from the current templates carry the
    ``search_quality`` section in ``config/optimization_assurance.json``,
    while real legacy runs do not and therefore never owe the new contracts.
    Files that do exist are still validated regardless of this flag.
    """
    value = read_json(root / "config" / "optimization_assurance.json", {})
    return isinstance(value, dict) and isinstance(
        value.get("search_quality"), dict
    )


def search_quality_enabled(root: Path) -> bool:
    """Activate only for optimization tasks; projects may override the mode."""
    root = root.resolve()
    mode = str(_config(root).get("mode", "auto")).lower()
    if mode == "disabled":
        return False
    if mode == "required":
        return True
    return optimization_relevant(root)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    return None


def _artifact_hash(root: Path, relative: str) -> str | None:
    try:
        path = safe_relative(root, relative)
    except ValueError:
        return None
    return sha256(path) if path.is_file() else None


def _searches(diag: Any) -> list[tuple[str, dict]]:
    searches = diag.get("searches", {}) if isinstance(diag, dict) else {}
    if not isinstance(searches, dict):
        return []
    return [
        (str(search_id), search)
        for search_id, search in searches.items()
        if isinstance(search, dict)
    ]


def _diagnostics(root: Path) -> dict:
    value = read_json(root / "results" / "research_diagnostics.json", {})
    return value if isinstance(value, dict) else {}


def _strictly_better(sense: str, value: float, reference: float) -> bool:
    tolerance = RELATIVE_TOLERANCE * max(abs(reference), 1.0)
    if sense == "min":
        return value < reference - tolerance
    return value > reference + tolerance


def _tool_run_record(root: Path, run_id: str) -> dict | None:
    if not run_id or any(item in run_id for item in ("/", "\\", "..")):
        return None
    path = root / ".harness" / "tool_runs" / f"{run_id}.json"
    if not path.is_file():
        return None
    record = read_json(path)
    return record if isinstance(record, dict) else None


def _run_covers(record: dict, relative: str) -> bool:
    outputs = record.get("outputs", [])
    if not isinstance(outputs, list):
        return False
    target = relative.replace("\\", "/")
    return any(
        isinstance(item, dict)
        and str(item.get("path", "")).replace("\\", "/") == target
        for item in outputs
    )


def audit_search_budget(root: Path) -> list[str]:
    """Require every declared search budget to be pinned tool-run evidence."""
    root = root.resolve()
    if not search_quality_enabled(root):
        return []
    errors: list[str] = []
    section = _config(root).get("search_budget")
    factor = (
        _number(section.get("wall_time_overrun_factor"))
        if isinstance(section, dict) else None
    )
    if factor is None or factor <= 0:
        factor = DEFAULT_WALL_TIME_OVERRUN_FACTOR
    for search_id, search in _searches(_diagnostics(root)):
        budget = search.get("budget")
        if not isinstance(budget, dict):
            continue
        run_id = budget.get("tool_run_id")
        if not isinstance(run_id, str) or not run_id:
            errors.append(
                f"search budget 缺少 tool_run_id，预算遥测未钉扎到 tool run: "
                f"{search_id}"
            )
        else:
            record = _tool_run_record(root, run_id)
            if record is None:
                errors.append(
                    f"search budget tool_run_id 未指向 .harness/tool_runs 下"
                    f"真实存在的 tool run: {search_id}: {run_id}"
                )
            elif record.get("id") != run_id:
                errors.append(
                    f"search budget tool run 清单 id 与 tool_run_id 不一致: "
                    f"{search_id}: {run_id}"
                )
            elif not _run_covers(record, "results/research_diagnostics.json"):
                errors.append(
                    f"search budget tool run 的 outputs 未覆盖 "
                    f"results/research_diagnostics.json: {search_id}: {run_id}"
                )
        for field in BUDGET_NUMERIC_FIELDS:
            if _number(budget.get(field)) is None:
                errors.append(
                    f"search budget 缺少或非法 {field}: {search_id}"
                )
        termination = budget.get("termination")
        if termination not in TERMINATIONS:
            errors.append(
                f"search budget termination 枚举非法: {search_id}: "
                f"{termination!r}"
            )
        time_limit = _number(budget.get("time_limit_seconds"))
        wall_time = _number(budget.get("wall_time_seconds"))
        if (
            time_limit is not None
            and wall_time is not None
            and time_limit > 0
            and wall_time > factor * time_limit
        ):
            errors.append(
                f"search budget wall_time_seconds 显著超出 "
                f"time_limit_seconds（>{factor}x）: {search_id}"
            )
    return sorted(set(errors))


def _hard_combinatorial(root: Path) -> bool:
    """Detect hard combinatorial problems from the ledger, plus task types.

    The constraint ledger (a 4.2+ artifact) must exist before a project can
    be classified: legacy runs that predate the ledger never owe the 4.5
    cheap-baseline contract, so they can never false-positive.  For
    ledger-instrumented projects the ledger text is the primary signal and
    routing/scheduling task types act as a fallback clue.
    """
    if not (root / "problem" / "constraint_ledger.json").is_file():
        return False
    ledger = read_json(root / "problem" / "constraint_ledger.json", {})
    constraints = ledger.get("constraints", {}) if isinstance(ledger, dict) else {}
    for item in constraints.values() if isinstance(constraints, dict) else []:
        if not isinstance(item, dict) or not item.get("hard", True):
            continue
        if item.get("status") == "NOT_APPLICABLE":
            continue
        text = " ".join(
            str(item.get(field, ""))
            for field in ("statement", "mathematical_form")
        )
        if _COMBINATORIAL_RE.search(text):
            return True
    graph = read_json(root / ".harness" / "problem_graph.json", {})
    nodes = graph.get("nodes", {}) if isinstance(graph, dict) else {}
    for node in nodes.values() if isinstance(nodes, dict) else []:
        if isinstance(node, dict) and str(node.get("task_type", "")).lower() in {
            "routing", "scheduling",
        }:
            return True
    return False


def audit_incumbent_dominance(root: Path) -> list[str]:
    """Cheap constructive baselines must exist and never beat the incumbent."""
    root = root.resolve()
    if not search_quality_enabled(root):
        return []
    path = root / "results" / "incumbent_dominance_audit.json"
    if not path.is_file():
        if _contract_declared(root) and _hard_combinatorial(root):
            return [
                "hard 组合类问题无廉价对照: 缺少 "
                "results/incumbent_dominance_audit.json"
            ]
        return []
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["incumbent_dominance_audit 损坏或 schema 不是 1"]
    errors: list[str] = []
    sense = data.get("sense")
    if sense not in {"min", "max"}:
        errors.append("incumbent_dominance_audit.sense 必须是 min 或 max")
    incumbent = _number(data.get("incumbent_objective"))
    if incumbent is None:
        errors.append(
            "incumbent_dominance_audit.incumbent_objective 缺失或非有限数"
        )
    baselines = data.get("baselines")
    if not isinstance(baselines, list):
        errors.append("incumbent_dominance_audit.baselines 必须是列表")
        baselines = []
    if not baselines and _hard_combinatorial(root):
        errors.append("hard 组合类问题无廉价对照: baselines 为空")
    for index, item in enumerate(baselines):
        if not isinstance(item, dict):
            errors.append(f"baseline 不是对象: index {index}")
            continue
        baseline_id = item.get("id")
        label = (
            baseline_id
            if isinstance(baseline_id, str) and baseline_id
            else f"index {index}"
        )
        if not isinstance(baseline_id, str) or not baseline_id:
            errors.append(f"baseline id 缺失: {label}")
        if item.get("kind") not in BASELINE_KINDS:
            errors.append(f"baseline kind 非法: {label}")
        if not isinstance(item.get("checker_pass"), bool):
            errors.append(f"baseline checker_pass 必须是布尔值: {label}")
        objective = _number(item.get("objective"))
        if objective is None:
            errors.append(f"baseline objective 缺失或非有限数: {label}")
        relative = str(item.get("artifact", ""))
        actual = _artifact_hash(root, relative) if relative else None
        if actual is None:
            errors.append(f"baseline artifact 缺失: {label}: {relative}")
        elif item.get("sha256") != actual:
            errors.append(f"baseline artifact sha256 失配: {label}: {relative}")
        if (
            item.get("checker_pass") is True
            and objective is not None
            and incumbent is not None
            and sense in {"min", "max"}
            and _strictly_better(sense, objective, incumbent)
        ):
            errors.append(
                f"incumbent 被简单构造解支配: baseline {label} "
                f"objective={objective:g} 优于 incumbent={incumbent:g}"
                f"（sense={sense}）"
            )
    return sorted(set(errors))


def _warm_start_declared(root: Path) -> bool:
    policy = read_json(root / "config" / "search_policy.json")
    warm = policy.get("warm_start") if isinstance(policy, dict) else None
    return isinstance(warm, dict) and warm.get("allowed") is True


def _portfolio_sense(root: Path, data: dict, diag: dict) -> str | None:
    sense = data.get("sense")
    if sense in {"min", "max"}:
        return sense
    search_id = str(data.get("search_id", ""))
    for candidate_id, search in _searches(diag):
        if candidate_id == search_id and search.get("sense") in {"min", "max"}:
            return search.get("sense")
    dominance = read_json(
        root / "results" / "incumbent_dominance_audit.json", {}
    )
    if isinstance(dominance, dict) and dominance.get("sense") in {"min", "max"}:
        return dominance.get("sense")
    return None


def audit_search_portfolio(root: Path) -> list[str]:
    """Govern the search portfolio: selection, cold arms and escalation."""
    root = root.resolve()
    if not search_quality_enabled(root):
        return []
    diag = _diagnostics(root)
    starved = detect_search_starvation(diag)
    warm_declared = _warm_start_declared(root)
    path = root / "results" / "search_portfolio.json"
    if not path.is_file():
        if not _contract_declared(root):
            return []
        errors = []
        if warm_declared:
            errors.append(
                "声明 warm start 但 arms 缺少 cold_start 冷启动臂: 缺少 "
                "results/search_portfolio.json"
            )
        if starved:
            starved_ids = sorted(
                str(item.get("search_id")) for item in starved
            )
            errors.append(
                f"搜索饥饿且无升级尝试: searches={starved_ids}, arms=0"
            )
        return sorted(set(errors))
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["search_portfolio 损坏或 schema 不是 1"]
    errors: list[str] = []
    arms = data.get("arms")
    if not isinstance(arms, list):
        errors.append("search_portfolio.arms 必须是列表")
        arms = []
    valid_arms: dict[str, dict] = {}
    for index, item in enumerate(arms):
        if not isinstance(item, dict):
            errors.append(f"arm 不是对象: index {index}")
            continue
        arm_id = item.get("id")
        label = (
            arm_id
            if isinstance(arm_id, str) and arm_id
            else f"index {index}"
        )
        if not isinstance(arm_id, str) or not arm_id:
            errors.append(f"arm id 缺失: {label}")
        elif arm_id in valid_arms:
            errors.append(f"arm id 重复: {label}")
        else:
            valid_arms[arm_id] = item
        if item.get("kind") not in ARM_KINDS:
            errors.append(f"arm kind 非法: {label}")
        if not str(item.get("method", "")).strip():
            errors.append(f"arm method 缺失: {label}")
        if _number(item.get("incumbent_objective")) is None:
            errors.append(f"arm incumbent_objective 缺失或非有限数: {label}")
        run_ids = item.get("tool_run_ids", [])
        if not isinstance(run_ids, list):
            errors.append(f"arm tool_run_ids 必须是列表: {label}")
            run_ids = []
        for run_id in run_ids:
            if (
                not isinstance(run_id, str)
                or _tool_run_record(root, run_id) is None
            ):
                errors.append(
                    f"arm tool_run_id 未指向真实 tool run: {label}: {run_id!r}"
                )
    selected_id = data.get("selected_arm")
    selected = (
        valid_arms.get(selected_id)
        if isinstance(selected_id, str) else None
    )
    if selected is None:
        errors.append(f"selected_arm 不在 arms 中: {selected_id!r}")
    sense = _portfolio_sense(root, data, diag)
    if selected is not None and sense in {"min", "max"}:
        selected_value = _number(selected.get("incumbent_objective"))
        best_id, best_value = None, None
        for arm_id, item in valid_arms.items():
            value = _number(item.get("incumbent_objective"))
            if value is None:
                continue
            if best_value is None or _strictly_better(sense, value, best_value):
                best_id, best_value = arm_id, value
        if (
            selected_value is not None
            and best_value is not None
            and _strictly_better(sense, best_value, selected_value)
        ):
            errors.append(
                f"portfolio 最优未被采用: 最优臂 {best_id} "
                f"objective={best_value:g} 优于 selected {selected_id} "
                f"objective={selected_value:g}（sense={sense}）"
            )
    if warm_declared and not any(
        item.get("kind") == "cold_start" for item in valid_arms.values()
    ):
        errors.append("声明 warm start 但 arms 缺少 cold_start 冷启动臂")
    if starved and len(valid_arms) < 2:
        starved_ids = sorted(str(item.get("search_id")) for item in starved)
        errors.append(
            f"搜索饥饿且无升级尝试: searches={starved_ids}, "
            f"arms={len(valid_arms)}"
        )
    return sorted(set(errors))


def audit_search_quality(root: Path, phase: str = "all") -> list[str]:
    """Aggregate the search-quality audits behind the optimization gate."""
    root = root.resolve()
    if not search_quality_enabled(root):
        return []
    errors = list(audit_search_budget(root))
    if phase in {"solver", "decision", "all"}:
        errors.extend(audit_incumbent_dominance(root))
        errors.extend(audit_search_portfolio(root))
    return sorted(set(errors))
