from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, read_json


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    atomic_write_json(path, value)


def project_root(start: str | Path = ".") -> Path:
    path = Path(start).resolve()
    for candidate in (path, *path.parents):
        if (candidate / "modeling-project.json").is_file():
            return candidate
    raise ValueError("当前目录不在 Modeling Harness 项目中")
