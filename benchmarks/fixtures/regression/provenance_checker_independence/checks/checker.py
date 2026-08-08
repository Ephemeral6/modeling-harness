"""Feasibility solver for the lamb-pen schedule (blind-run origin)."""
from __future__ import annotations


def max_occupancy(schedule, horizon):
    peak = 0
    for day in range(horizon):
        active = sum(1 for start, end in schedule if start <= day < end)
        peak = max(peak, active)
    return peak


def is_feasible(schedule, horizon, capacity):
    return max_occupancy(schedule, horizon) <= capacity
