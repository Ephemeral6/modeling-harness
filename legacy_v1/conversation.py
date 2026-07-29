from __future__ import annotations

import argparse
import json
from pathlib import Path

from .intake import intake
from .util import write_json


def run(title: str, prompt: str, files: list[str], root: Path | None = None) -> dict:
    harness_root = (root or Path.cwd()).resolve()
    result = intake(harness_root, title, prompt, [Path(x) for x in files])
    # The repository-level pointer created by the low-level intake is moved
    # under ignored projects/ so a conversation never dirties the source tree.
    old_pointer = harness_root / ".modelharness-current.json"
    if old_pointer.exists():
        data = json.loads(old_pointer.read_text(encoding="utf-8"))
        write_json(harness_root / "projects" / ".current.json", data)
        old_pointer.unlink()
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Create a Modeling Harness run directly from chat attachments."
    )
    ap.add_argument("--title", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--file", action="append", default=[])
    ap.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()
    try:
        print(json.dumps(
            run(args.title, args.prompt, args.file, args.root),
            ensure_ascii=False, indent=2,
        ))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
