from __future__ import annotations

import argparse
import json
from pathlib import Path

from .intake import intake


def run(title: str, prompt: str, files: list[str],
        root: Path | None = None) -> dict:
    return intake(
        (root or Path.cwd()).resolve(), title, prompt,
        [Path(item) for item in files],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="从对话附件创建隔离建模项目")
    parser.add_argument("--title", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        print(json.dumps(
            run(args.title, args.prompt, args.file, args.root),
            ensure_ascii=False, indent=2,
        ))
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
