from pathlib import Path

import pytest

from modelharness.narrative import audit_paper
from modelharness.scaffold import create

pytestmark = pytest.mark.regression


def test_backward_audit(tmp_path: Path):
    root = create(tmp_path / "legacy-audit", "legacy")
    assert audit_paper(root) == [
        "交付 Profile 缺少 verified 证据: narrative.paper",
        "引用未知节点: result.baseline",
    ]
