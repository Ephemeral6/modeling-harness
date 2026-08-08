import json
import pytest

pytestmark = pytest.mark.regression


def test_search_starvation(regression_project, regression_api):
    defect = "MIP 180 秒仅探 2 节点留 1.1% gap 却无搜索饥饿 Opportunity"
    build = regression_api("modelharness.opportunities", "build_ledger", defect)
    root = regression_project("search_starvation")
    found = [
        item for item in build(root) if item["kind"] == "search_starvation"
    ]
    assert found, defect
    assert all(item["severity"] == "high" for item in found), defect
    assert [
        item["search_id"] for item in found
    ] == ["Q1.open_grain_mip"], defect
    summary = found[0]["summary"]
    assert "Q1.open_grain_mip" in summary, defect
    assert "nodes_explored=2" in summary, defect
    assert "0.011" in summary, defect


def test_search_starvation_requires_budget(regression_project, regression_api):
    defect = "缺少 budget 块的旧项目 search 条目被误报搜索饥饿"
    detect = regression_api(
        "modelharness.opportunities", "detect_search_starvation", defect
    )
    root = regression_project("search_starvation")
    path = root / "results/research_diagnostics.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for search in data["searches"].values():
        search.pop("budget", None)
    assert detect(data) == [], defect
