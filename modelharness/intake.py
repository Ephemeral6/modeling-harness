from __future__ import annotations

import mimetypes
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from .calibration import inherit_baseline
from .contracts import safe_relative
from .scaffold import create
from .storage import file_lock
from .util import now, sha256, write_json

WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def slugify(value: str) -> str:
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value, flags=re.UNICODE)
    return value.strip("-_").lower()[:48] or "modeling-task"


def safe_attachment_name(name: str) -> str:
    candidate = Path(name).name
    if candidate != name or ":" in candidate or "\x00" in candidate:
        raise ValueError(f"非法附件名称: {name}")
    stem = Path(candidate).stem.rstrip(". ").casefold()
    if not candidate or stem in WINDOWS_RESERVED:
        raise ValueError(f"Windows 保留附件名称: {name}")
    return candidate


def intake(harness_root: Path, title: str, prompt: str, files: list[Path],
           project_dir: Path | None = None, engine: str = "auto",
           baseline: Path | str | None = None) -> dict:
    harness_root = harness_root.resolve()
    projects = harness_root / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    baseline_root = (
        Path(baseline).expanduser().resolve() if baseline is not None else None
    )
    if baseline_root is not None and not baseline_root.is_dir():
        raise ValueError(f"baseline 项目目录不存在: {baseline_root}")
    sources = [Path(item).resolve(strict=True) for item in files]
    if any(not source.is_file() for source in sources):
        raise ValueError("所有 Intake 输入都必须是普通文件")
    names = [safe_attachment_name(source.name) for source in sources]
    with file_lock(projects / "intake", timeout=30):
        if project_dir:
            destination = safe_relative(harness_root, project_dir)
            if destination.exists():
                raise ValueError(f"目标项目已存在: {destination}")
        else:
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            destination = projects / f"{slugify(title)}--{stamp}"
            counter = 2
            while destination.exists():
                destination = projects / f"{slugify(title)}--{stamp}-{counter}"
                counter += 1
        staging = projects / f".intake-{uuid.uuid4().hex}"
        try:
            root = create(staging, title, engine=engine)
            inbox = root / "problem" / "data_raw"
            manifest, text_candidates = [], []
            if not sources:
                # 用户把题面直接粘贴在对话里、没有附件：原文物化为 data_raw
                # 中的只读原始文件并锁定哈希，与上传附件享受同等保留待遇。
                pasted = inbox / "001__pasted_statement.md"
                pasted.write_text(prompt.strip() + "\n", encoding="utf-8")
                manifest.append({
                    "index": 1, "original_name": "pasted_statement.md",
                    "stored_as": pasted.relative_to(root).as_posix(),
                    "bytes": pasted.stat().st_size, "sha256": sha256(pasted),
                    "mime": "text/markdown",
                    "origin": "conversation_paste",
                    "received_at": now(),
                })
                text_candidates.append(pasted)
            for index, (source, name) in enumerate(zip(sources, names), start=1):
                target = inbox / f"{index:03d}__{name}"
                shutil.copy2(source, target, follow_symlinks=False)
                mime, _ = mimetypes.guess_type(name)
                manifest.append({
                    "index": index, "original_name": name,
                    "stored_as": target.relative_to(root).as_posix(),
                    "bytes": target.stat().st_size, "sha256": sha256(target),
                    "mime": mime or "application/octet-stream",
                    "received_at": now(),
                })
                if source.suffix.lower() in {".md", ".txt"}:
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
                listing = "\n".join(f"- `{x['stored_as']}`" for x in manifest)
                statement.write_text(
                    "# 待解析题面\n\nS0 必须读取下列原始附件并识别题面，"
                    "不得凭文件名推测内容：\n\n" + listing + "\n",
                    encoding="utf-8",
                )
            write_json(root / "problem" / "intake_manifest.json", {
                "schema": 2, "title": title, "received_at": now(),
                "user_prompt": "problem/user_prompt.md", "files": manifest,
            })
            inheritance = (
                inherit_baseline(root, baseline_root)
                if baseline_root is not None else None
            )
            os.replace(staging, destination)
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        write_json(projects / ".current.json", {
            "schema": 2,
            "project": destination.relative_to(harness_root).as_posix(),
            "title": title, "updated_at": now(),
        })
    result = {
        "project": str(destination), "title": title,
        "files_received": len(sources),
        "manifest": str(destination / "problem" / "intake_manifest.json"),
        "next_action": "运行 modelharness autopilot next 并持续推进到 S6。",
    }
    if inheritance is not None:
        # 上一 run 的自设口径已随项目落盘，改动必须写 superseded_reason。
        result["baseline"] = inheritance["baseline"]
        result["calibration"] = {
            "freeze": inheritance["freeze"],
            "diff": inheritance["diff"],
            "entries": inheritance["entries"],
            "constraints": inheritance["constraints"],
            "counts": inheritance["counts"],
        }
    return result
