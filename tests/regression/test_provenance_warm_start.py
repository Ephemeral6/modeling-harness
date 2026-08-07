import json

import pytest

pytestmark = pytest.mark.regression


def test_provenance_warm_start(regression_project, regression_api):
    defect = "results/q5_search.json 含 warm_start_sha256 却无 search_policy 声明仍被视为干净"
    audit = regression_api(
        "modelharness.provenance_core", "audit_warm_start_keys", defect
    )
    root = regression_project("provenance_warm_start")
    errors = audit(root)
    assert any(
        "results/q5_search.json" in error and "warm_start_sha256" in error
        for error in errors
    ), defect
    assert any(
        "results/q5_search.json" in error and "warm_start_role" in error
        for error in errors
    ), defect

    declared = json.loads(
        (root / "results" / "q5_search.json").read_text(encoding="utf-8")
    )["warm_start_sha256"]
    policy_path = root / "config" / "search_policy.json"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        json.dumps({
            "schema": 1,
            "warm_start": {
                "allowed": True,
                "source_sha256": [declared],
                "note": "prior local artifact declared as candidate only",
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert audit(root) == [], defect
