"""Compatibility facade for the authoritative StageService."""
from pathlib import Path

from .contracts import STAGES
from .stages import StageService

ORDER = list(STAGES)


def load(root: Path) -> dict:
    return StageService(root).config


def status(root: Path) -> list[dict]:
    service = StageService(root)
    valid = set(service.valid_prefix())
    return [
        {
            "stage": stage,
            "name": service.config[stage]["name"],
            "stamped": stage in valid,
            "errors": service.validate_stamp(stage)
            if service.stamp_path(stage).exists() else [],
        }
        for stage in STAGES
    ]


def run_gate(root: Path, stage: str) -> dict:
    return StageService(root).gate(stage)


def invalidate(root: Path, stage: str, reason: str = "manual") -> list[str]:
    return StageService(root).invalidate(stage, reason)
