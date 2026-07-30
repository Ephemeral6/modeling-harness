from __future__ import annotations

import shutil
from pathlib import Path

from modelharness.autopilot import next_packet
from modelharness.cli import _plan_init
from modelharness.method_packs import MethodPackRegistry
from modelharness.problem_graph import ProblemGraph
from modelharness.profiles import ProfileService
from modelharness.scaffold import create


def test_plan_init_seeds_missing_v3_catalogs_for_v2_project(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    (root / ".harness" / "problem_graph.json").unlink()
    shutil.rmtree(root / "config" / "method_packs")
    shutil.rmtree(root / "config" / "profiles")
    (root / "config" / "delivery_profile.json").unlink()
    _plan_init(root)
    assert ProblemGraph(root).exists
    assert MethodPackRegistry(root).list()
    assert not MethodPackRegistry(root).audit()
    assert ProfileService(root).active["name"] == "general"
    packet = next_packet(root)
    assert packet["frontier"][0]["id"] == "s0.problem_definition"
