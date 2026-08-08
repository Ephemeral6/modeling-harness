"""External baseline artifact registered in baseline_artifacts."""


def optimize(rows):
    best = None
    for row in rows:
        if best is None or row["value"] > best["value"]:
            best = row
    return best
