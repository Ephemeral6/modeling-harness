import json

import pytest

pytestmark = pytest.mark.regression


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_self_imposed_drift(regression_project, regression_api):
    defect = (
        "同题第二次 run 把自设最小面积口径从 20% 悄悄改成 10%、并丢掉管理分散度上限，"
        "冻结合同仍判为干净"
    )
    audit = regression_api("modelharness.calibration", "audit_freeze", defect)
    root = regression_project("self_imposed_drift")

    errors = audit(root)
    assert any(
        "calib.min_planting_area_fraction" in error for error in errors
    ), defect
    assert any(
        "calib.management_group_caps" in error for error in errors
    ), defect

    # 明确写下 superseded_reason 的条目是有意改口径，放行；未写的仍被否决。
    freeze_path = root / "config" / "calibration_freeze.json"
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    for entry in freeze["entries"]:
        if entry["id"] == "calib.management_group_caps":
            entry["superseded_reason"] = (
                "S4 扫描证明分散度上限不可辨识，本 run 显式撤下并在论文披露。"
            )
    _write(freeze_path, freeze)

    errors = audit(root)
    assert not any(
        "calib.management_group_caps" in error for error in errors
    ), defect
    assert any(
        "calib.min_planting_area_fraction" in error for error in errors
    ), defect

    # results 的实际取值回到冻结口径后归零。
    results_path = root / "results" / "model_calibration.json"
    results = json.loads(results_path.read_text(encoding="utf-8"))
    results["min_planting_area_fraction"] = 0.2
    _write(results_path, results)

    assert audit(root) == [], defect
