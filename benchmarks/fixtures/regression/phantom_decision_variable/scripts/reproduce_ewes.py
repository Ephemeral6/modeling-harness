"""Regenerate the Q2.ewes headline number from the solver artifact."""
from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    model = json.loads(
        (root / "results" / "solver_model.json").read_text(encoding="utf-8")
    )
    print(json.dumps({"ewes": model["ewes"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
