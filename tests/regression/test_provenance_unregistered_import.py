import pytest

pytestmark = pytest.mark.regression


def test_provenance_unregistered_import(regression_project, regression_api):
    defect = (
        "跨次运行拷贝进 src/ 的 .py 未在 import_manifest 登记，"
        "audit_provenance 仍视为干净"
    )
    audit = regression_api(
        "modelharness.provenance_core", "audit_provenance", defect
    )
    register = regression_api(
        "modelharness.provenance_core", "register_import", defect
    )
    root = regression_project("provenance_unregistered_import")
    errors = audit(root, root / "baselines")
    assert any(
        "unregistered cross-run copy" in error
        and "src/reused_optimizer.py" in error
        and "prior_run_2025a/optimize.py" in error
        for error in errors
    ), defect

    register(
        root,
        root / "prior_run" / "optimize.py",
        "src/reused_optimizer.py",
        "copy",
        "harness run reused prior blind-run optimizer as reference implementation",
    )
    assert audit(root, root / "baselines") == [], defect
