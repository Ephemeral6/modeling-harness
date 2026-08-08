"""Harness arm solver written independently of the external baseline."""


def solve(capacity: float, demand: float) -> float:
    return min(capacity, demand) * 0.93
