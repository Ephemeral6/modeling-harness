"""v4.5 mechanism 3: decomposition-matheuristic method pack + L1 allocation benchmark."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from modelharness.method_packs_core import (
    MethodPackRegistry,
    validate_method_pack,
)

REPO = Path(__file__).parents[1]
TEMPLATES = REPO / "templates"
PACK_NAME = "decomposition-matheuristic"
PACK_PATH = TEMPLATES / "config" / "method_packs" / f"{PACK_NAME}.json"
INSTANCE_DIR = REPO / "benchmarks" / "fixtures" / "allocation_instances"
FIXTURE_PATH = REPO / "benchmarks" / "fixtures" / "l1_allocation_decomposition.json"


def _registry_without_pack(tmp_path: Path) -> MethodPackRegistry:
    """Red-green control: a registry seeded from templates minus the new pack."""
    root = tmp_path / "no-pack"
    directory = root / "config" / "method_packs"
    directory.mkdir(parents=True)
    for path in (TEMPLATES / "config" / "method_packs").glob("*.json"):
        if path.name != f"{PACK_NAME}.json":
            shutil.copy2(path, directory / path.name)
    return MethodPackRegistry(root)


def test_registry_without_pack_does_not_contain_it(tmp_path: Path):
    registry = _registry_without_pack(tmp_path)
    assert PACK_NAME not in {item["name"] for item in registry.list()}
    with pytest.raises(ValueError):
        registry.get(PACK_NAME)


def test_templates_registry_contains_valid_pack():
    registry = MethodPackRegistry(TEMPLATES)
    assert PACK_NAME in {item["name"] for item in registry.list()}
    pack = validate_method_pack(
        json.loads(PACK_PATH.read_text(encoding="utf-8")), PACK_PATH
    )
    assert pack["name"] == PACK_NAME
    for task_type in (
        "allocation", "assignment", "set_covering", "combinatorial_optimization",
    ):
        assert task_type in pack["task_types"]
        assert registry.match(task_type)["name"] == PACK_NAME
    assert len(pack["protocol"]) == 5
    structured = [
        item for item in pack["required_tests"] if isinstance(item, dict)
    ]
    assert structured, "required_tests 必须包含结构化条目"
    for item in structured:
        assert isinstance(item["acceptance"], dict)
    assert "mixed_integer_optimization" in (
        pack["tool_policy"]["preferred_capabilities"]
    )


def test_lock_audit_and_closure_hash(tmp_path: Path):
    root = tmp_path / "project"
    directory = root / "config" / "method_packs"
    directory.mkdir(parents=True)
    for path in (TEMPLATES / "config" / "method_packs").glob("*.json"):
        shutil.copy2(path, directory / path.name)
    registry = MethodPackRegistry(root)
    registry.refresh_lock()
    assert registry.audit() == []
    digest = registry.closure_hash({PACK_NAME})
    assert isinstance(digest, str) and len(digest) == 64
    int(digest, 16)


def test_seed_graph_offers_pack_on_s3():
    seed = json.loads(
        (TEMPLATES / "config" / "problem_graph.seed.json").read_text(
            encoding="utf-8"
        )
    )
    node = seed["nodes"]["s3.solver_validation"]
    assert PACK_NAME in node["method_pack_options"]
    assert node["method_pack"] in node["method_pack_options"]


def _load_verifier():
    spec = importlib.util.spec_from_file_location(
        "verify_instances", INSTANCE_DIR / "verify_instances.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_allocation_instances_expose_greedy_gap():
    verifier = _load_verifier()
    report = verifier.verify_all()
    assert set(report) == {
        "instance_a_max_coverage", "instance_b_generalized_assignment",
    }
    for name, item in report.items():
        assert item["optimal"] > item["greedy"] > 0, name
        gap = (item["optimal"] - item["greedy"]) / item["optimal"]
        assert gap >= 0.04, f"{name}: greedy 差距不足 4%: {gap:.4f}"
        assert item["gap_fraction"] == pytest.approx(gap)
        assert item["certified_match"] is True, name


def test_fixture_targets_are_baked_from_verifier():
    verifier = _load_verifier()
    report = verifier.verify_all()
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["schema"] == 1
    numeric = {
        item["field"]: item
        for item in fixture["rubric"]
        if item["kind"] == "json_numeric"
    }
    assert len(numeric) == 2
    for name, item in report.items():
        field = f"instances.{name}.objective.value"
        rubric = numeric[field]
        assert rubric["path"] == "results/optimality.json"
        assert rubric["target"] == item["optimal"]
        assert rubric["tolerance"] == pytest.approx(0.01 * item["optimal"])
