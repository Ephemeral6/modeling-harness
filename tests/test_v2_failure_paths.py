from pathlib import Path

import pytest

from modelharness.evidence import EvidenceGraph
from modelharness.scaffold import create


def test_failed_verification_is_persisted(tmp_path: Path):
    root = create(tmp_path / "case", "demo")
    artifact = root / "results" / "bad.txt"
    artifact.write_text("bad", encoding="utf-8")
    graph = EvidenceGraph(root)
    graph.add(
        "result.bad", "result", "must fail", "results/bad.txt",
        check='python -c "raise SystemExit(7)"',
    )
    with pytest.raises(RuntimeError):
        graph.verify("result.bad")
    assert EvidenceGraph(root).nodes["result.bad"]["status"] == "rejected"
