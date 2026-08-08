"""分配分解基准实例的精确验证器（纯标准库，运行 < 30 秒）。

对两个静态实例分别执行：

* 精确求解 —— 实例 A 用 15^5 全枚举，实例 B 用 bitmask 精确 DP；
* canonical 贪心基线 —— 实例 A 用最大覆盖边际增益贪心，实例 B 取
  收益密度贪心与绝对收益贪心中的较优者；
* 断言 (optimal - greedy) / optimal >= 0.04，且实例文件中烘焙的
  ``certified`` 数值与实测完全一致。

直接运行会打印各实例的最优值、贪心值、差距百分比与耗时。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIN_GAP = 0.04


def _load(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- instance A
def _coverage_masks(data: dict) -> list[list[int]]:
    slots = int(data["slots_per_objective"])
    return [
        [
            sum(1 << (obj * slots + slot) for obj, slot in plan["covers"])
            for plan in resource["plans"]
        ]
        for resource in data["resources"]
    ]


def solve_coverage_exact(data: dict) -> int:
    """全枚举：每个资源恰选一个计划，最大化覆盖元素并集大小。"""
    masks = _coverage_masks(data)
    n = len(masks)
    best = -1

    def descend(level: int, union: int) -> None:
        nonlocal best
        if level == n - 1:
            for mask in masks[level]:
                value = (union | mask).bit_count()
                if value > best:
                    best = value
            return
        for mask in masks[level]:
            descend(level + 1, union | mask)

    descend(0, 0)
    return best


def solve_coverage_greedy(data: dict) -> int:
    """Canonical 最大覆盖贪心：反复选边际增益最大的（资源, 计划）。"""
    masks = _coverage_masks(data)
    chosen: dict[int, int] = {}
    union = 0
    while len(chosen) < len(masks):
        best_gain, pick = -1, None
        for res, plans in enumerate(masks):
            if res in chosen:
                continue
            for idx, mask in enumerate(plans):
                gain = (union | mask).bit_count() - union.bit_count()
                if gain > best_gain:
                    best_gain, pick = gain, (res, idx)
        res, idx = pick
        chosen[res] = idx
        union |= masks[res][idx]
    return union.bit_count()


# ---------------------------------------------------------------- instance B
def solve_gap_exact(data: dict) -> int:
    """Bitmask 精确 DP：逐资源枚举其承接的任务子集（容量内），任务可空置。"""
    dems = [int(job["demand"]) for job in data["jobs"]]
    caps = [int(agent["capacity"]) for agent in data["agents"]]
    profits = data["profits"]
    n_jobs = len(dems)
    full = (1 << n_jobs) - 1
    dsum = [0] * (1 << n_jobs)
    for mask in range(1, 1 << n_jobs):
        low = mask & -mask
        dsum[mask] = dsum[mask ^ low] + dems[low.bit_length() - 1]
    dp = [-1] * (1 << n_jobs)
    dp[0] = 0
    for agent, cap in enumerate(caps):
        row = profits[agent]
        psum = [0] * (1 << n_jobs)
        for mask in range(1, 1 << n_jobs):
            low = mask & -mask
            psum[mask] = psum[mask ^ low] + row[low.bit_length() - 1]
        ndp = dp[:]
        for mask in range(1 << n_jobs):
            base = dp[mask]
            if base < 0:
                continue
            comp = full & ~mask
            sub = comp
            while sub:
                if dsum[sub] <= cap:
                    value = base + psum[sub]
                    if value > ndp[mask | sub]:
                        ndp[mask | sub] = value
                sub = (sub - 1) & comp
        dp = ndp
    return max(dp)


def solve_gap_greedy(data: dict) -> int:
    """双贪心取优：按收益密度与绝对收益各扫一遍，取较高总收益。"""
    dems = [int(job["demand"]) for job in data["jobs"]]
    caps = [int(agent["capacity"]) for agent in data["agents"]]
    profits = data["profits"]
    n_agents, n_jobs = len(caps), len(dems)
    best = -1
    for mode in ("density", "profit"):
        pairs = [(i, j) for i in range(n_agents) for j in range(n_jobs)]
        if mode == "density":
            pairs.sort(
                key=lambda x: (
                    -profits[x[0]][x[1]] / dems[x[1]],
                    -profits[x[0]][x[1]], x[0], x[1],
                )
            )
        else:
            pairs.sort(key=lambda x: (-profits[x[0]][x[1]], x[0], x[1]))
        remaining = caps[:]
        assigned = [False] * n_jobs
        total = 0
        for i, j in pairs:
            if not assigned[j] and remaining[i] >= dems[j]:
                assigned[j] = True
                remaining[i] -= dems[j]
                total += profits[i][j]
        best = max(best, total)
    return best


# ------------------------------------------------------------------- harness
_INSTANCES = {
    "instance_a_max_coverage": (
        "instance_a_max_coverage.json",
        solve_coverage_exact,
        solve_coverage_greedy,
    ),
    "instance_b_generalized_assignment": (
        "instance_b_generalized_assignment.json",
        solve_gap_exact,
        solve_gap_greedy,
    ),
}


def verify_instance(name: str) -> dict:
    file_name, exact, greedy = _INSTANCES[name]
    data = _load(file_name)
    start = time.perf_counter()
    optimal = exact(data)
    greedy_value = greedy(data)
    elapsed = time.perf_counter() - start
    assert optimal > 0 and greedy_value > 0, f"{name}: 目标值必须为正"
    gap = (optimal - greedy_value) / optimal
    assert gap >= MIN_GAP, (
        f"{name}: 贪心差距 {gap:.4%} 低于 {MIN_GAP:.0%} 合同下限"
    )
    certified = data["certified"]
    certified_match = (
        certified["optimal_objective"] == optimal
        and certified["greedy_objective"] == greedy_value
        and certified["greedy_gap_fraction"] == round(gap, 6)
    )
    assert certified_match, (
        f"{name}: 烘焙值与实测不一致: certified={certified} "
        f"actual optimal={optimal} greedy={greedy_value}"
    )
    return {
        "method": certified["method"],
        "optimal": optimal,
        "greedy": greedy_value,
        "gap_fraction": gap,
        "elapsed_seconds": elapsed,
        "certified_match": certified_match,
    }


def verify_all() -> dict:
    return {name: verify_instance(name) for name in _INSTANCES}


def main() -> int:
    total_start = time.perf_counter()
    report = verify_all()
    for name, item in report.items():
        print(f"== {name} ==")
        print(f"  exact optimal = {item['optimal']} ({item['method']})")
        print(f"  greedy        = {item['greedy']}")
        print(f"  gap           = {item['gap_fraction']:.4%}")
        print(f"  elapsed       = {item['elapsed_seconds']:.2f} s")
        print(f"  certified ok  = {item['certified_match']}")
    total = time.perf_counter() - total_start
    print(f"TOTAL elapsed {total:.2f} s (< 30 s budget)")
    print("All assertions passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
