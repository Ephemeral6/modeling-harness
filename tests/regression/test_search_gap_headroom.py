import json
import pytest

pytestmark = pytest.mark.regression


def test_search_gap_headroom(regression_project, regression_api):
    defect = "MIP 收工仍留 8% gap 却无提升求解质量的 Opportunity"
    build = regression_api("modelharness.opportunities", "build_ledger", defect)
    root = regression_project("search_gap_headroom")
    ledger = build(root)
    found = [
        item for item in ledger if item["kind"] == "search_gap_headroom"
    ]
    assert found, defect
    assert all(item["severity"] == "high" for item in found), defect
    assert [
        item["search_id"] for item in found
    ] == ["Q2.route_mip"], defect
    assert found[0]["gap"] == pytest.approx(0.08), defect
    summary = found[0]["summary"]
    assert "Q2.route_mip" in summary, defect
    assert "0.08" in summary, defect
    assert not [
        item for item in ledger if item["kind"] == "search_starvation"
    ], "节点充足且 incumbent 新鲜的搜索被误报 search_starvation"


def test_search_gap_headroom_requires_budget(regression_project, regression_api):
    defect = "缺少 budget 块的旧项目 search 条目被误报 gap 余量"
    detect = regression_api(
        "modelharness.opportunities", "detect_gap_headroom", defect
    )
    root = regression_project("search_gap_headroom")
    path = root / "results/research_diagnostics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for search in data["searches"].values():
        search.pop("budget", None)
    assert detect(data) == [], defect


def test_gap_below_threshold_not_flagged(regression_project, regression_api):
    defect = "已收敛（gap<=5%）且未饥饿的搜索被误报 gap 余量"
    detect = regression_api(
        "modelharness.opportunities", "detect_gap_headroom", defect
    )
    root = regression_project("search_gap_headroom")
    path = root / "results/research_diagnostics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["searches"]["Q2.route_mip"]["budget"]["final_gap_fraction"] = 0.03
    assert detect(data) == [], defect
