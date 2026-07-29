from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .evidence import EvidenceGraph
from .util import now, read_json, sha256, write_json

ORDER = ["s0", "s1", "s2", "s3", "s4", "s5", "s6"]


def load(root: Path) -> dict:
    return read_json(root / "config" / "stages.json", {})


def status(root: Path) -> list[dict]:
    graph = EvidenceGraph(root)
    config = load(root)
    result = []
    for stage in ORDER:
        stamp = root / ".harness" / "stamps" / f"{stage}.json"
        spec = config.get(stage, {})
        result.append({
            "stage": stage,
            "name": spec.get("name", stage),
            "stamped": stamp.exists(),
            "requires": spec.get("requires", []),
        })
    return result


def run_gate(root: Path, stage: str) -> dict:
    if stage not in ORDER:
        raise ValueError(stage)
    spec = load(root).get(stage)
    if not spec:
        raise ValueError(f"未配置阶段 {stage}")
    idx = ORDER.index(stage)
    if idx and not (root / ".harness" / "stamps" /
                    f"{ORDER[idx - 1]}.json").exists():
        raise RuntimeError(f"必须先通过 {ORDER[idx - 1]}")
    graph = EvidenceGraph(root)
    errors = graph.audit()
    for node_id in spec.get("requires", []):
        node = graph.nodes.get(node_id)
        if not node or node.get("status") != "verified":
            errors.append(f"缺少 verified 证据: {node_id}")
    reviews = []
    for review_path in spec.get("reviews", []):
        path = root / review_path
        if not path.is_file():
            errors.append(f"缺少独立审核: {review_path}")
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if str(data.get("verdict", "")).upper() != "APPROVE":
            errors.append(f"审核未通过: {review_path}")
        reviews.append({"path": review_path, "sha256": sha256(path)})
    checks = []
    for cmd in spec.get("checks", []):
        proc = subprocess.run(
            cmd, cwd=root, shell=True, text=True,
            capture_output=True, timeout=1800,
        )
        checks.append({
            "command": cmd, "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-1000:],
            "stderr_tail": (proc.stderr or "")[-1000:],
        })
        if proc.returncode:
            errors.append(f"机械检查失败: {cmd}")
    if errors:
        raise RuntimeError("\n".join(errors))
    record = {
        "stage": stage, "name": spec["name"], "time": now(),
        "evidence": [
            {"id": node_id,
             "sha256": graph.nodes[node_id]["artifact_sha256"]}
            for node_id in spec.get("requires", [])
        ],
        "reviews": reviews, "checks": checks,
    }
    write_json(root / ".harness" / "stamps" / f"{stage}.json", record)
    return record


def invalidate(root: Path, stage: str) -> list[str]:
    removed = []
    for item in ORDER[ORDER.index(stage):]:
        p = root / ".harness" / "stamps" / f"{item}.json"
        if p.exists():
            p.unlink()
            removed.append(item)
    return removed
