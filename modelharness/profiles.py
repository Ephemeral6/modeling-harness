"""Delivery Profile service with one-time V2 migration seeding."""
from __future__ import annotations

import shutil
from pathlib import Path

from .profiles_core import validate_profile
from .profiles_invalidation_core import (
    PROFILE_OUTPUTS,
    ProfileService as _ProfileService,
)


class ProfileService(_ProfileService):
    def __init__(self, root: Path):
        root = root.resolve()
        directory = root / "config" / "profiles"
        if not directory.exists():
            source = (
                Path(__file__).parent.parent /
                "templates" / "config" / "profiles"
            )
            directory.mkdir(parents=True, exist_ok=False)
            for path in source.glob("*.json"):
                shutil.copy2(path, directory / path.name)
        active = root / "config" / "delivery_profile.json"
        if not active.is_file():
            general = directory / "general.json"
            if not general.is_file():
                raise ValueError("delivery profile catalog 损坏：general 缺失")
            active.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(general, active)
        super().__init__(root)
