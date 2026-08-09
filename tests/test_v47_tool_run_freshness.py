"""4.7 tool-run freshness: self-pinning runs and idempotent re-runs.

Two coupled defects made every optimization project deadlock in S3:

* a child process could not learn the id of the tool run executing it, so a
  script that must pin its own telemetry to that run had to be patched after
  the run finished — which instantly invalidated the manifest hash;
* ``audit_decision`` rejected every run whose recorded output hash no longer
  matched disk, so re-running a node made the *previous* run permanently
  stale and blocked the node forever.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from modelharness.scaffold import create
from modelharness.toolchain import ToolchainService


NODE = "s0.problem_definition"

TELEMETRY = """import json
import os
from pathlib import Path

Path("results/telemetry.json").write_text(
    json.dumps({
        "tool_run_id": os.environ.get("MODEL_HARNESS_TOOL_RUN_ID", ""),
        "objective": 4.0,
    }, ensure_ascii=False),
    encoding="utf-8",
)
"""

TELEMETRY_AND_TRACE = """import json
import os
from pathlib import Path

run_id = os.environ.get("MODEL_HARNESS_TOOL_RUN_ID", "")
Path("results/telemetry.json").write_text(
    json.dumps({"tool_run_id": run_id, "objective": 4.0}, ensure_ascii=False),
    encoding="utf-8",
)
Path("results/trace.json").write_text(
    json.dumps({"tool_run_id": run_id, "nodes": 12}, ensure_ascii=False),
    encoding="utf-8",
)
"""


def _project(tmp_path: Path, name: str, script: str = TELEMETRY):
    root = create(tmp_path / name, name)
    (root / "src" / "telemetry.py").write_text(script, encoding="utf-8")
    service = ToolchainService(root)
    service.decide(
        NODE, "use", "用本地 Python 产出可复现的搜索遥测", tools=["python"]
    )
    return root, service


def _run(
    service: ToolchainService,
    *,
    script: str = "src/telemetry.py",
    outputs: list[str] | None = None,
) -> dict:
    outputs = outputs or ["results/telemetry.json"]
    return service.run(
        NODE,
        argv=[sys.executable, script],
        inputs=[script],
        outputs=outputs,
        validators=[
            {"kind": "json_finite", "path": outputs[0]},
            {
                "kind": "json_fields",
                "path": outputs[0],
                "fields": ["tool_run_id"],
            },
        ],
        tool_ids=["python"],
        seed=11,
        timeout=120,
    )


def _read(root: Path, relative: str) -> dict:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def test_tool_run_publishes_its_own_id_to_the_child_process(tmp_path: Path):
    root, service = _project(tmp_path, "selfpin")
    record = _run(service)
    assert record["verification"]["status"] == "verified"
    telemetry = _read(root, "results/telemetry.json")
    assert telemetry["tool_run_id"] == record["id"], (
        "子进程必须能通过 MODEL_HARNESS_TOOL_RUN_ID 拿到自身 run id，"
        "否则只能跑完再改文件，manifest 哈希立刻陈旧"
    )
    assert service.audit_decision(NODE) == []


def test_rerunning_the_same_output_does_not_block_the_node(tmp_path: Path):
    root, service = _project(tmp_path, "rerun")
    first = _run(service)
    second = _run(service)
    assert first["id"] != second["id"]
    assert first["outputs"][0]["sha256"] != second["outputs"][0]["sha256"]
    assert _read(root, "results/telemetry.json")["tool_run_id"] == second["id"]
    assert service.audit_decision(NODE) == [], (
        "同一节点对同一输出跑两次后，被覆盖的旧 run 应记为 superseded，"
        "而不是让该节点永久 BLOCKED"
    )


def test_superseded_run_is_labelled_not_failed(tmp_path: Path):
    _root, service = _project(tmp_path, "label")
    first = _run(service)
    second = _run(service)
    runs = [first, second]
    verification = service.executor.live_verification(first, runs)
    assert verification["status"] == "verified"
    assert verification["superseded_outputs"] == ["results/telemetry.json"]
    assert any(
        item.get("kind") == "output_integrity" and item.get("superseded")
        for item in verification["checks"]
    )
    assert service.executor.superseded_outputs(second, runs) == []


def test_editing_the_current_artifact_by_hand_is_still_blocked(
    tmp_path: Path,
):
    root, service = _project(tmp_path, "tamper")
    _run(service)
    _run(service)
    (root / "results" / "telemetry.json").write_text(
        json.dumps({"tool_run_id": "forged", "objective": 99.0}),
        encoding="utf-8",
    )
    errors = service.audit_decision(NODE)
    assert any("当前产物验证失败" in error for error in errors), (
        "没有任何 run 的清单哈希与磁盘一致时，必须仍然判定为篡改"
    )


def test_partially_superseded_run_still_owns_its_untouched_output(
    tmp_path: Path,
):
    root, service = _project(tmp_path, "partial", TELEMETRY_AND_TRACE)
    (root / "src" / "telemetry_only.py").write_text(
        TELEMETRY, encoding="utf-8"
    )
    wide = _run(
        service, outputs=["results/telemetry.json", "results/trace.json"]
    )
    _run(service, script="src/telemetry_only.py")
    assert service.audit_decision(NODE) == []
    assert service.executor.superseded_outputs(
        wide, service.executor.list(NODE)
    ) == ["results/telemetry.json"]

    (root / "results" / "trace.json").write_text(
        json.dumps({"tool_run_id": "forged", "nodes": 0}), encoding="utf-8"
    )
    errors = service.audit_decision(NODE)
    assert any(
        "当前产物验证失败" in error and wide["id"] in error
        for error in errors
    ), "没有后续 run 覆盖的输出被手改时，原 run 仍必须报错"
