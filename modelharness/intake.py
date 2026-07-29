from __future__ import annotations

import mimetypes
import re
import shutil
from pathlib import Path

from .scaffold import create
from .util import now, sha256, write_json


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE)
    return (value.strip("-_").lower()[:48] or "modeling-task")


def unique_destination(directory: Path, name: str) -> Path:
    target = directory / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    index = 2
    while (directory / f"{stem}_{index}{suffix}").exists():
        index += 1
    return directory / f"{stem}_{index}{suffix}"


def intake(harness_root: Path, title: str, prompt: str, files: list[Path],
           project_dir: Path | None = None) -> dict:
    """Create an isolated run and preserve conversational inputs verbatim."""
    harness_root = harness_root.resolve()
    projects = harness_root / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    destination = (project_dir or projects / slugify(title)).resolve()
    if not destination.is_relative_to(harness_root):
        raise ValueError("Intake 项目必须位于 Harness 仓库内")
    root = create(destination, title)
    inbox = root / "problem" / "data_raw"
    manifest, text_candidates = [], []
    for raw in files:
        source = raw.resolve()
        if not source.is_file():
            raise ValueError(f"附件不存在: {raw}")
        target = unique_destination(inbox, source.name)
        shutil.copy2(source, target)
        mime, _ = mimetypes.guess_type(target.name)
        manifest.append({
            "original_path": str(source),
            "stored_as": target.relative_to(root).as_posix(),
            "name": target.name,
            "bytes": target.stat().st_size,
            "sha256": sha256(target),
            "mime": mime or "application/octet-stream",
            "received_at": now(),
        })
        if target.suffix.lower() in {".md", ".txt"}:
            text_candidates.append(target)
    (root / "problem" / "user_prompt.md").write_text(
        "# 用户任务要求\n\n" + prompt.strip() + "\n", encoding="utf-8"
    )
    statement = root / "problem" / "statement.md"
    if len(text_candidates) == 1:
        statement.write_text(
            text_candidates[0].read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )
    else:
        names = "\n".join(f"- `{x['stored_as']}`" for x in manifest)
        statement.write_text(
            "# 待解析题面\n\n题面包含在本次对话上传的附件中。S0 必须先读取并"
            "解析以下原始文件，不得凭文件名推测内容：\n\n" + names + "\n",
            encoding="utf-8",
        )
    manifest_path = root / "problem" / "intake_manifest.json"
    write_json(manifest_path, {
        "schema": 1, "title": title, "received_at": now(),
        "user_prompt": "problem/user_prompt.md", "files": manifest,
    })
    write_json(harness_root / ".modelharness-current.json", {
        "project": root.relative_to(harness_root).as_posix(),
        "title": title, "updated_at": now(),
    })
    return {
        "project": str(root), "title": title,
        "files_received": len(manifest), "manifest": str(manifest_path),
        "next_action": "读取项目 AGENTS.md、题面、user_prompt.md 和 manifest，执行 S0。",
    }
