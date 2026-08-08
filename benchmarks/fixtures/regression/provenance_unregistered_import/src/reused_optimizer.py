"""Greedy pen-assignment optimizer copied verbatim from a prior blind run."""
from __future__ import annotations


def assign_pens(batches, capacity):
    schedule = []
    used = 0
    for batch in sorted(batches, key=lambda item: item["arrival"]):
        if used + batch["size"] <= capacity:
            schedule.append(batch["id"])
            used += batch["size"]
    return schedule
