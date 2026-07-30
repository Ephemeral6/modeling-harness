import json
from pathlib import Path

from modelharness.evidence import EvidenceGraph
from modelharness.method_packs import MethodPackRegistry
from modelharness.problem_graph import ProblemGraph
from modelharness.scaffold import create
from modelharness.stages import StageService


def test_relocking_changed_method_pack_cannot_reuse_old_evidence(
    tmp_path: Path,
):
    root = create(tmp_path / "case", "demo")
    evidence = EvidenceGraph(root)
    for node_id, artifact in (
        ("problem.statement", "problem/statement.md"),
        ("problem.success", "docs/success_criteria.md"),
    ):
        evidence.add(node_id, "problem", node_id, artifact)
        evidence.verify(node_id)
    graph = ProblemGraph(root)
    assert graph.completion("s0.problem_definition", evidence.nodes)
    StageService(root).gate("s0")
    pack_path = (
        root / "config" / "method_packs" / "problem-framing.json"
    )
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    pack["protocol"].append("新增的强制检查")
    pack_path.write_text(json.dumps(pack), encoding="utf-8")
    MethodPackRegistry(root).refresh_lock()
    assert not graph.completion("s0.problem_definition", evidence.nodes)
    assert StageService(root).current() == "s0"
