from __future__ import annotations

import subprocess
from pathlib import Path

from modelharness.paper import audit_render, render_pdf
from modelharness.profiles import ProfileService
from modelharness.scaffold import create
from modelharness.sanitize import sanitize_report


def _ready_project(tmp_path: Path) -> Path:
    root = create(tmp_path / "paper", "PDF 交付")
    ProfileService(root).use("cumcm")
    (root / "paper" / "final.md").write_text(
        "---\ntitle: 测试论文\nabstract: 摘要。\n"
        "keywords: [建模, 验证]\n---\n\n# 问题重述\n\n正文。\n",
        encoding="utf-8",
    )
    return root


def test_cumcm_scaffold_contains_single_source_pdf_delivery(tmp_path: Path):
    root = _ready_project(tmp_path)
    profile = ProfileService(root).active
    assert profile["paper_delivery"] == {
        "source": "paper/final.md",
        "output": "paper/final.pdf",
        "template": "paper/cumcm-template.tex",
        "pdf_engine": "xelatex",
    }
    template = (root / "paper" / "cumcm-template.tex").read_text(
        encoding="utf-8"
    )
    assert "摘\\quad 要" in template
    assert "\\captionsetup[table]{position=top}" in template
    assert "\\fancyfoot[C]{\\thepage}" in template
    delivery_check = (root / "checks" / "check_delivery.py").read_text(
        encoding="utf-8"
    )
    assert "audit_render" in delivery_check
    missing = {
        item.get("section") for item in sanitize_report(root)["violations"]
        if item["kind"] == "missing_section"
    }
    assert "摘要" not in missing


def test_render_pdf_records_pass_and_detects_staleness(
    tmp_path: Path, monkeypatch
):
    root = _ready_project(tmp_path)
    monkeypatch.setattr(
        "modelharness.paper.shutil.which", lambda name: f"/tools/{name}"
    )

    def fake_run(command, **_kwargs):
        output = Path(next(
            arg.split("=", 1)[1] for arg in command
            if arg.startswith("--output=")
        ))
        output.write_bytes(b"%PDF-1.7\nfixture")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("modelharness.paper.subprocess.run", fake_run)
    report = render_pdf(root)
    assert report["execution_status"] == "COMPLETED"
    assert report["verdict"] == "PASS"
    assert report["authority"] == "MACHINE"
    assert audit_render(root) == []
    (root / "paper" / "final.md").write_text("changed", encoding="utf-8")
    assert audit_render(root) == ["PDF 渲染输入已变化: paper/final.md"]
    render_pdf(root)
    (root / "paper" / "figures" / "new.svg").write_text(
        "<svg/>", encoding="utf-8"
    )
    assert audit_render(root) == [
        "PDF 渲染输入已变化: paper/figures/new.svg"
    ]


def test_render_pdf_separates_not_run_and_recovery_pending(
    tmp_path: Path, monkeypatch
):
    root = _ready_project(tmp_path)
    monkeypatch.setattr("modelharness.paper.shutil.which", lambda _name: None)
    report = render_pdf(root)
    assert report["execution_status"] == "NOT_RUN"
    assert report["verdict"] == "INCONCLUSIVE"

    monkeypatch.setattr(
        "modelharness.paper.shutil.which", lambda name: f"/tools/{name}"
    )

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["pandoc"], 1)

    monkeypatch.setattr("modelharness.paper.subprocess.run", timeout)
    report = render_pdf(root, timeout=1)
    assert report["execution_status"] == "RECOVERY_PENDING"
    assert report["verdict"] == "INCONCLUSIVE"
