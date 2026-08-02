from __future__ import annotations

import json

import pytest

from modelharness.checks_core import evaluate_acceptance
from modelharness.claims import audit_claims, evaluate_claims, json_pointer
from modelharness.problem_graph import ProblemGraph
from modelharness.safeeval import evaluate
from modelharness.scaffold import create


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, str):
        path.write_text(value, encoding="utf-8")
    else:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _claim_project(tmp_path, *, tolerance=None):
    root = tmp_path
    _write(root / "results/source.json", {"value": 2.675, "input": 107})
    _write(root / "config/claim_bindings.json", {
        "schema": 1,
        "claims": {
            "Q.answer": {
                "requirement_ids": ["Q.answer"],
                "source": {
                    "artifact": "results/source.json",
                    "pointer": "/value",
                },
                "value_type": "number",
                "unit": "kg",
                "display": {"decimals": 2, "rounding": "half_up"},
                "derivation": {
                    "expr": "input / divisor",
                    "inputs": {
                        "input": {
                            "artifact": "results/source.json",
                            "pointer": "/input",
                        },
                        "divisor": {"const": 40},
                    },
                },
                "tolerance": tolerance or {"abs": 0, "rel": 0},
                "scenario_set": "report",
            }
        },
    })
    _write(root / "predictions/registered.json", {
        "claims": {"Q.answer": {"value": 2.675, "unit": "kg"}}
    })
    _write(root / "paper/final.md", "Q.answer = 2.68 kg\n")
    return root


def test_claim_rounding_and_tolerance_are_mechanical(tmp_path):
    root = _claim_project(tmp_path, tolerance={"abs": 0.001, "rel": 0})
    source = root / "results/source.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    data["value"] = 2.6755
    _write(source, data)
    _write(root / "predictions/registered.json", {
        "claims": {"Q.answer": {"value": 2.6755, "unit": "kg"}}
    })

    report = evaluate_claims(root)

    assert report["claims"]["Q.answer"]["status"] == "valid"
    assert report["claims"]["Q.answer"]["displayed"] == "2.68"
    assert audit_claims(root) == []


def test_claim_unit_mismatch_is_independent_failure(tmp_path):
    root = _claim_project(tmp_path)
    _write(root / "predictions/registered.json", {
        "claims": {"Q.answer": {"value": 2.675, "unit": "lb"}}
    })

    evaluate_claims(root)

    assert any("unit_mismatch" in error for error in audit_claims(root))


def test_claim_field_drift_fails_even_with_current_source_hash(tmp_path):
    root = _claim_project(tmp_path)
    _write(root / "predictions/registered.json", {
        "claims": {"Q.answer": {"value": 2.67, "unit": "kg"}}
    })

    report = evaluate_claims(root)

    assert report["claims"]["Q.answer"]["source"]["artifact_sha256"]
    assert any("value_drift" in error for error in audit_claims(root))


def test_safeeval_and_json_assert_share_restricted_numeric_semantics(tmp_path):
    assert evaluate("sqrt(x) + max(1, y)", {"x": 9, "y": 2}) == 5
    with pytest.raises(ValueError, match="not allowed"):
        evaluate("x.__class__", {"x": 1})
    assert json_pointer({"a/b": [{"~key": 4}]}, "/a~1b/0/~0key") == (True, 4)
    _write(tmp_path / "results/value.json", {"score": 4.0, "items": [1]})

    result = evaluate_acceptance(tmp_path, [
        {
            "kind": "json_assert",
            "path": "results/value.json",
            "field": "score",
            "op": "between",
            "value": [3.9, 4.1],
        },
        {
            "kind": "json_assert",
            "path": "results/value.json",
            "field": "items",
            "op": "nonempty",
        },
    ])

    assert result["ok"] is True
    assert result["records"][0]["actual"] == 4.0
    assert result["records"][0]["op"] == "between"


def test_claim_binding_hash_invalidates_s5_contract(tmp_path):
    root = create(tmp_path / "project", "claim contract")
    graph = ProblemGraph(root)
    before = graph.contract_hash("s5.decision")
    path = root / "config/claim_bindings.json"
    _write(path, {"schema": 1, "claims": {}})
    with_binding = graph.contract_hash("s5.decision")
    _write(path, {"schema": 1, "claims": {"Q.changed": {}}})

    assert with_binding != before
    assert graph.contract_hash("s5.decision") != with_binding
