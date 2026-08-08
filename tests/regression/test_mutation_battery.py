import json
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.regression


BATTERY = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "fixtures"
    / "regression"
    / "mutation_battery"
)
DEFECT = (
    "红队 21 条变异未常驻化：v11 实测 14 层护栏总检出率仅 7/21=33%，"
    "篡改交付数字类（M06/M09/M10/M11）检出率 0%"
)
HEADLINE_CLASS = "headline_tamper"


def _specs() -> list[dict]:
    return json.loads(
        (BATTERY / "mutations.json").read_text(encoding="utf-8")
    )


def _hit_detectors(spec: dict, findings: list[str]) -> set[str]:
    return {
        detector
        for detector in spec["expected_detectors"]
        if any(item.startswith(detector + ":") for item in findings)
    }


def test_pristine_battery_project_is_clean(
    regression_project, regression_api
):
    defect = f"{DEFECT}（基线未注入时 detect 必须为空，否则检出率无意义）"
    detect = regression_api("modelharness.mutation_core", "detect", defect)
    root = regression_project("mutation_battery") / "project"
    assert detect(root) == [], defect


@pytest.mark.parametrize(
    "spec", _specs(), ids=[spec["id"] for spec in _specs()]
)
def test_each_mutation_is_detected(spec, regression_project, regression_api):
    apply_mutation = regression_api(
        "modelharness.mutation_core", "apply_mutation", DEFECT
    )
    detect = regression_api("modelharness.mutation_core", "detect", DEFECT)
    if spec.get("known_gap"):
        pytest.skip(f"known_gap（已在 mutations.json 登记）: {spec['known_gap']}")
    root = regression_project("mutation_battery") / "project"
    apply_mutation(root, spec)
    findings = detect(root)
    assert _hit_detectors(spec, findings), (
        f"{DEFECT}；{spec['id']}（{spec['note']}）期望 "
        f"{spec['expected_detectors']} 至少命中其一，实际发现: {findings}"
    )


def test_battery_detection_rates(
    regression_project, regression_api, tmp_path
):
    apply_mutation = regression_api(
        "modelharness.mutation_core", "apply_mutation", DEFECT
    )
    detect = regression_api("modelharness.mutation_core", "detect", DEFECT)
    battery = regression_project("mutation_battery")
    pristine = battery / "project"
    specs = json.loads(
        (battery / "mutations.json").read_text(encoding="utf-8")
    )
    assert len(specs) >= 21, "电池必须完整收录 v11 的 21 条变异"
    known_gaps = [spec for spec in specs if spec.get("known_gap")]
    assert len(known_gaps) <= 2, (
        f"known_gap 条数不得超过 2: {[spec['id'] for spec in known_gaps]}"
    )
    counted = [spec for spec in specs if not spec.get("known_gap")]

    missed: list[str] = []
    for spec in counted:
        run = tmp_path / f"run_{spec['id']}"
        shutil.copytree(pristine, run)
        apply_mutation(run, spec)
        if not _hit_detectors(spec, detect(run)):
            missed.append(spec["id"])

    rate = (len(counted) - len(missed)) / len(counted)
    assert rate >= 0.90, (
        f"{DEFECT}；总检出率 {rate:.2%} < 90%，漏网条目: {missed}"
    )

    headline = [
        spec for spec in counted if spec["class"] == HEADLINE_CLASS
    ]
    headline_missed = [
        spec["id"] for spec in headline if spec["id"] in set(missed)
    ]
    assert headline, "电池必须包含 headline_tamper 类变异（M06/M09/M10/M11）"
    assert not headline_missed, (
        f"{DEFECT}；篡改交付数字类检出率必须 100%，漏网条目: {headline_missed}"
    )
