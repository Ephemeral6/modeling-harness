import pytest

pytestmark = pytest.mark.regression


def test_lifecycle_stale_active(regression_project, regression_api):
    defect = (
        "半成品项目 status=active、S5 任务租约早已过期且 s6 未落章，"
        "project audit 却未把它列为 stale_active"
    )
    audit = regression_api(
        "modelharness.lifecycle", "audit_projects", defect
    )
    root = regression_project("lifecycle_stale_active")
    report = audit(root, hours=24)
    assert report["ok"] is False, defect
    stale = report["stale_active"]
    assert [item["project"] for item in stale] == ["demo_stale"], defect
    assert stale[0]["status"] == "active", defect
    assert stale[0]["s6_stamped"] is False, defect
    summary = {
        item["project"]: item["status"] for item in report["projects"]
    }
    assert summary == {"demo_stale": "active"}, defect


def test_lifecycle_stale_active_reconcile_flips(
    regression_project, regression_api
):
    defect = (
        "过期 claimed 租约在 reconcile_status 后未把 active 翻成 "
        "interrupted"
    )
    reconcile = regression_api(
        "modelharness.lifecycle", "reconcile_status", defect
    )
    root = regression_project("lifecycle_stale_active")
    report = reconcile(root / "demo_stale")
    assert report["flipped"] is True, defect
    assert report["status"] == "interrupted", defect
    assert report["recovered_leases"], defect
