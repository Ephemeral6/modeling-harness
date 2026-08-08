import pytest

pytestmark = pytest.mark.regression


def test_provenance_checker_independence(regression_project, regression_api):
    defect = (
        "声称独立的 checker 与被检 solver 同源"
        "（import_manifest 中 method=copy 且 source_sha256 相同）仍未被识破"
    )
    audit = regression_api(
        "modelharness.provenance_core", "audit_provenance", defect
    )
    register = regression_api(
        "modelharness.provenance_core", "register_import", defect
    )
    root = regression_project("provenance_checker_independence")
    errors = audit(root)
    assert any(
        "checker provenance violates independence" in error
        and "checks/checker.py" in error
        for error in errors
    ), defect

    independent = (
        '"""Independent feasibility recheck written from scratch."""\n'
        "def recheck(schedule, horizon, capacity):\n"
        "    days = [0] * horizon\n"
        "    for start, end in schedule:\n"
        "        for day in range(start, min(end, horizon)):\n"
        "            days[day] += 1\n"
        "    return max(days) <= capacity\n"
    )
    origin = root / "origin" / "independent_checker.py"
    origin.parent.mkdir(parents=True, exist_ok=True)
    origin.write_text(independent, encoding="utf-8")
    (root / "checks" / "checker.py").write_text(independent, encoding="utf-8")
    register(
        root,
        origin,
        "checks/checker.py",
        "copy",
        "checker rebuilt from an independently written implementation",
    )
    assert audit(root) == [], defect
