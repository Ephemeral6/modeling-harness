"""Profile-aware PDF rendering for delivery papers."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from .contracts import safe_relative
from .storage import atomic_write_json, read_json
from .util import now, sha256


def _pdf_page_count(path: Path) -> int | None:
    """Best-effort page count for locally rendered, unencrypted PDFs."""
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    count = len(re.findall(rb"/Type\s*/Page\b", payload))
    return count or None


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _render_inputs(root: Path, source: Path, template: Path) -> dict[str, str]:
    paths = {source, template}
    figures = root / "paper" / "figures"
    if figures.is_dir():
        paths.update(path for path in figures.rglob("*") if path.is_file())
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(paths, key=lambda item: item.as_posix())
    }


def _write_log(path: Path, command: list[str], stdout: str, stderr: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "command:\n" + "\n".join(command) + "\n\nstdout:\n" + stdout
        + "\n\nstderr:\n" + stderr,
        encoding="utf-8", newline="\n",
    )


def render_pdf(
    root: Path,
    source: str | None = None,
    output: str | None = None,
    template: str | None = None,
    timeout: int = 300,
) -> dict:
    """Render one project-local Markdown source to PDF without a shell."""
    root = root.resolve()
    profile = read_json(root / "config" / "delivery_profile.json", {}) or {}
    delivery = profile.get("paper_delivery", {})
    if not isinstance(delivery, dict):
        raise ValueError("delivery profile.paper_delivery 必须是对象")
    source_path = safe_relative(
        root, source or delivery.get("source", "paper/final.md")
    )
    output_path = safe_relative(
        root, output or delivery.get("output", "paper/final.pdf")
    )
    template_path = safe_relative(
        root, template or delivery.get("template", "paper/cumcm-template.tex")
    )
    engine_name = str(delivery.get("pdf_engine", "xelatex"))
    if output_path.suffix.casefold() != ".pdf":
        raise ValueError("PDF 输出路径必须以 .pdf 结尾")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout 必须是正整数")

    report_path = root / "paper" / "render_report.json"
    log_path = root / "logs" / "paper_render.log"
    report = {
        "schema": 1, "created_at": now(), "profile": profile.get("name"),
        "source": source_path.relative_to(root).as_posix(),
        "output": output_path.relative_to(root).as_posix(),
        "template": template_path.relative_to(root).as_posix(),
        "execution_status": "NOT_RUN", "verdict": "INCONCLUSIVE",
        "authority": "MACHINE", "returncode": None,
        "input_hashes": {}, "output_sha256": None,
        "page_count": None,
        "log": log_path.relative_to(root).as_posix(),
    }
    missing = [
        path.relative_to(root).as_posix()
        for path in (source_path, template_path) if not path.is_file()
    ]
    pandoc = shutil.which("pandoc")
    engine = shutil.which(engine_name)
    unavailable = [
        name for name, executable in (("pandoc", pandoc), (engine_name, engine))
        if executable is None
    ]
    if missing or unavailable:
        report["missing_files"] = missing
        report["missing_tools"] = unavailable
        _write_log(log_path, [], "", "未执行：" + "; ".join(missing + unavailable))
        atomic_write_json(report_path, report)
        return report

    report["input_hashes"] = _render_inputs(root, source_path, template_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(
        f".{output_path.stem}.rendering-{uuid.uuid4().hex}.pdf"
    )
    resource_path = os.pathsep.join((
        str(root), str(root / "paper"), str(root / "paper" / "figures")
    ))
    command = [
        str(pandoc), source_path.relative_to(root).as_posix(),
        "--from=markdown+raw_tex+tex_math_dollars", "--standalone",
        "--number-sections", "--listings", f"--template={template_path}",
        f"--pdf-engine={engine}", f"--resource-path={resource_path}",
        f"--output={temporary}",
    ]
    report["command"] = command
    try:
        completed = subprocess.run(
            command, cwd=root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        report["execution_status"] = "RECOVERY_PENDING"
        report["temporary_output"] = temporary.relative_to(root).as_posix()
        _write_log(
            log_path, command, _text(exc.stdout),
            _text(exc.stderr) + f"\n渲染超过 {timeout} 秒，结果待对账。",
        )
        atomic_write_json(report_path, report)
        return report
    except OSError as exc:
        report["execution_status"] = "ERROR"
        report["error"] = str(exc)
        _write_log(log_path, command, "", str(exc))
        atomic_write_json(report_path, report)
        return report

    report["execution_status"] = "COMPLETED"
    report["returncode"] = completed.returncode
    pdf_header = b""
    if temporary.is_file():
        with temporary.open("rb") as handle:
            pdf_header = handle.read(5)
    valid_pdf = (
        completed.returncode == 0 and temporary.is_file()
        and temporary.stat().st_size > 4 and pdf_header == b"%PDF-"
    )
    if valid_pdf:
        os.replace(temporary, output_path)
        report["verdict"] = "PASS"
        report["output_sha256"] = sha256(output_path)
        report["output_bytes"] = output_path.stat().st_size
        report["page_count"] = _pdf_page_count(output_path)
    else:
        report["verdict"] = "FAIL"
        temporary.unlink(missing_ok=True)
    _write_log(log_path, command, completed.stdout, completed.stderr)
    atomic_write_json(report_path, report)
    return report


def audit_render(root: Path) -> list[str]:
    """Check that the current PDF and its render inputs match the report."""
    root = root.resolve()
    report = read_json(root / "paper" / "render_report.json")
    if not isinstance(report, dict):
        return ["PDF 渲染报告缺失"]
    if report.get("execution_status") != "COMPLETED":
        return [f"PDF 渲染未完成: {report.get('execution_status')}"]
    if report.get("verdict") != "PASS":
        return [f"PDF 渲染未通过: {report.get('verdict')}"]
    output = safe_relative(root, str(report.get("output", "")))
    if not output.is_file():
        return ["PDF 交付物缺失"]
    if sha256(output) != report.get("output_sha256"):
        return ["PDF 交付物已过期或被修改"]
    expected_inputs = report.get("input_hashes")
    if not isinstance(expected_inputs, dict):
        return ["PDF 渲染输入哈希非法"]
    source = safe_relative(root, str(report.get("source", "")))
    template = safe_relative(root, str(report.get("template", "")))
    if not source.is_file() or not template.is_file():
        return ["PDF 渲染源文件或模板缺失"]
    current_inputs = _render_inputs(root, source, template)
    changed = sorted(
        relative for relative in set(expected_inputs) | set(current_inputs)
        if expected_inputs.get(relative) != current_inputs.get(relative)
    )
    return [f"PDF 渲染输入已变化: {relative}" for relative in changed]
