"""Method-pack registry with one-time V2 migration seeding."""
from __future__ import annotations

import shutil
from pathlib import Path

from .method_packs_core import (
    MethodPackRegistry as _MethodPackRegistry,
    validate_method_pack,
)


class MethodPackRegistry(_MethodPackRegistry):
    def __init__(self, root: Path):
        root = root.resolve()
        directory = root / "config" / "method_packs"
        if not directory.exists():
            source = (
                Path(__file__).parent.parent / "templates" /
                "config" / "method_packs"
            )
            directory.mkdir(parents=True, exist_ok=False)
            for path in source.glob("*.json"):
                shutil.copy2(path, directory / path.name)
        super().__init__(root)
