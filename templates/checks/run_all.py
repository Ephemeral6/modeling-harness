from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-level", type=int, default=5)
    args = ap.parse_args()
    records = []
    for path in sorted((ROOT / "checks").glob("l[0-5]_*.py")):
        level = int(path.name[1])
        if level > args.max_level:
            continue
        run = subprocess.run([sys.executable, str(path)], cwd=ROOT,
                             text=True, capture_output=True)
        try:
            record = json.loads(run.stdout.strip().splitlines()[-1])
        except Exception:
            record = {"check_id": path.stem, "level": level, "status": "FAIL",
                      "details": (run.stderr or run.stdout)[-1000:]}
        records.append(record)
    print(json.dumps({"checks": records}, ensure_ascii=False, indent=2))
    return int(any(x.get("status") != "PASS" for x in records))


if __name__ == "__main__":
    raise SystemExit(main())

