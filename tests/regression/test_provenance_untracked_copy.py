import hashlib
import json

import pytest

pytestmark = pytest.mark.regression


def test_provenance_untracked_copy(regression_project, regression_api):
    defect = "与基线登记表逐字节相同的 .py 未在 import_manifest 登记仍未被识破"
    scan = regression_api(
        "modelharness.provenance_core", "scan_against_baselines", defect
    )
    root = regression_project("provenance_untracked_copy")
    errors = scan(root, root / "baselines")
    assert any(
        "unregistered cross-run copy" in error
        and "src/copied_solver.py" in error
        and "prior_run_2023d/src/solve_model.py" in error
        for error in errors
    ), defect

    digest = hashlib.sha256(
        (root / "src" / "copied_solver.py").read_bytes()
    ).hexdigest()
    manifest_path = root / "problem" / "import_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({
            "schema": 1,
            "imports": [{
                "path": "src/copied_solver.py",
                "sha256": digest,
                "source": "prior_run_2023d/src/solve_model.py",
            }],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert scan(root, root / "baselines") == [], defect
