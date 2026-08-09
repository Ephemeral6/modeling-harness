"""Re-running a node must not brick it.

The fixture holds two tool runs of the same node under the same decision.
Both declare ``results/search_telemetry.json``; the second overwrote what the
first produced, so the first manifest's recorded hash no longer matches the
file on disk.  Before the fix ``audit_decision`` read that as tampering and
the node stayed BLOCKED forever — re-running, the one obvious recovery, made
it strictly worse.
"""
import hashlib
import json

import pytest

pytestmark = pytest.mark.regression

NODE = "s0.problem_definition"
ARTIFACT = "results/search_telemetry.json"
FIRST = "1730000000-aaaa0000aaa0"
SECOND = "1730000600-bbbb1111bbb1"


def _digest(root, relative):
    return hashlib.sha256((root / relative).read_bytes()).hexdigest()


def _recorded(root, run_id):
    manifest = json.loads(
        (root / ".harness" / "tool_runs" / f"{run_id}.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        item["path"]: item.get("sha256") for item in manifest["outputs"]
    }


def _audit(regression_api, root, defect):
    service = regression_api(
        "modelharness.toolchain", "ToolchainService", defect
    )
    return service(root).audit_decision(NODE)


def test_rerunning_the_same_output_does_not_block_the_node(
    regression_project, regression_api
):
    defect = (
        "同一节点对同一输出跑两次 tool run 后，被第二次覆盖的第一次 run 被"
        "判为产物篡改，该节点永久 BLOCKED"
    )
    root = regression_project("tool_run_rerun_stale")
    current = _digest(root, ARTIFACT)
    assert _recorded(root, FIRST)[ARTIFACT] != current, "夹具未复现覆盖场景"
    assert _recorded(root, SECOND)[ARTIFACT] == current, "夹具未复现覆盖场景"
    assert json.loads(
        (root / ARTIFACT).read_text(encoding="utf-8")
    )["tool_run_id"] == SECOND, "夹具未复现自钉扎产物"

    errors = _audit(regression_api, root, defect)
    assert not [
        error for error in errors if "当前产物验证失败" in error
    ], defect


def test_hand_edited_artifact_is_still_blocked(
    regression_project, regression_api
):
    defect = (
        "没有任何 tool run 的清单哈希与磁盘一致时，产物被手工改写仍被放行"
    )
    root = regression_project("tool_run_rerun_stale")
    (root / ARTIFACT).write_text(
        json.dumps(
            {"schema": 1, "tool_run_id": SECOND, "incumbent_objective": 99.0},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    errors = _audit(regression_api, root, defect)
    stale = [error for error in errors if "当前产物验证失败" in error]
    assert len(stale) == 2, defect
    assert any(FIRST in error for error in stale), defect
    assert any(SECOND in error for error in stale), defect
