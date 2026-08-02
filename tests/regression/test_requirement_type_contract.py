import json
import pytest

pytestmark = pytest.mark.regression


def test_requirement_type_contract(regression_project, regression_api):
    defect = "Q1.output_range 可绑单点值或另一分支，类型与条件锚定未执行"
    audit = regression_api(
        "modelharness.requirements", "audit_requirements", defect
    )
    root = regression_project("requirement_type_contract")
    errors = audit(root)
    assert any(
        "Q1.output_range" in error and "kind" in error for error in errors
    ), defect
    assert any(
        "Q1.output_range" in error and "scenario" in error for error in errors
    ), defect
    path = root / "config/claim_bindings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["claims"]["claim.output_range"].update(
        value_type="interval", scenario_id="baseline"
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    path = root / "results/claim_values.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["claims"]["claim.output_range"]["source_value"] = {
        "lower": 1280, "upper": 1360
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not [
        error for error in audit(root) if "Q1.output_range" in error
    ], defect
