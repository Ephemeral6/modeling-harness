import hashlib
import json

import pytest

pytestmark = pytest.mark.regression


def test_comparison_leaked_baseline(regression_project, regression_api):
    defect = "harness 臂内文件与登记基线同哈希且无 import 登记时对照仍被判独立"
    audit = regression_api(
        "modelharness.comparison", "audit_comparison", defect
    )
    root = regression_project("comparison_leaked_baseline")
    manifest = root / "manifest.json"

    errors = audit(manifest)
    assert any(
        "baseline leak" in error
        and "harness_a" in error
        and "src/copied_from_baseline.py" in error
        and "fixture_codex/optimize.py" in error
        for error in errors
    ), defect
    # 泄漏是唯一缺陷：其余四查在该清单上应当全部通过。
    assert all("baseline leak" in error for error in errors), defect

    # 补登 import_manifest（如实承认复用）后，泄漏项转为已登记，审计归零。
    leaked = root / "arms" / "harness_a" / "src" / "copied_from_baseline.py"
    digest = hashlib.sha256(leaked.read_bytes()).hexdigest()
    registry = root / "arms" / "harness_a" / "problem" / "import_manifest.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps({
            "schema": 1,
            "imports": [{
                "index": 0,
                "stored_as": "src/copied_from_baseline.py",
                "stored_sha256": digest,
                "source_sha256": digest,
                "method": "copy",
                "reason": "declared reuse of the external baseline optimizer",
            }],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    assert audit(manifest) == [], defect
