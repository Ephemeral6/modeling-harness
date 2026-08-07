import json

import pytest

pytestmark = pytest.mark.regression


def test_search_budget_unpinned(regression_project, regression_api):
    defect = "searches[].budget 缺少 tool_run_id 的求解遥测未被判定为不可信"
    audit = regression_api(
        "modelharness.search_quality", "audit_search_budget", defect
    )
    root = regression_project("search_budget_unpinned")
    errors = audit(root)
    assert any(
        "Q1.master_mip" in error and "tool_run_id" in error for error in errors
    ), defect


def test_pinning_budget_to_real_tool_run_clears_audit(
    regression_project, regression_api
):
    defect = "budget 钉扎到覆盖 research_diagnostics 的真实 tool run 后仍误报"
    audit = regression_api(
        "modelharness.search_quality", "audit_search_budget", defect
    )
    root = regression_project("search_budget_unpinned")
    run_id = "1730000000-deadbeef0001"
    manifest = {
        "schema": 1,
        "id": run_id,
        "node_id": "s3.solver_validation",
        "returncode": 0,
        "outputs": [
            {
                "path": "results/research_diagnostics.json",
                "exists": True,
                "sha256": "recorded-at-run-time",
            }
        ],
    }
    runs = root / ".harness" / "tool_runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / f"{run_id}.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    diag_path = root / "results/research_diagnostics.json"
    diag = json.loads(diag_path.read_text(encoding="utf-8"))
    diag["searches"]["Q1.master_mip"]["budget"]["tool_run_id"] = run_id
    diag_path.write_text(
        json.dumps(diag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    assert audit(root) == [], defect
