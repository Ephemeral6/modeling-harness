"""Coarse grid search for pen allocation, carried over from a prior run."""
from __future__ import annotations


def allocate(pens: int, sheep: int, area: float) -> dict:
    per_pen = sheep // pens
    remainder = sheep % pens
    density = sheep / area
    return {
        "per_pen": per_pen,
        "remainder": remainder,
        "density": density,
    }


def objective(density: float, target: float = 2.5) -> float:
    return abs(density - target)


if __name__ == "__main__":
    print(allocate(12, 240, 96.0))
