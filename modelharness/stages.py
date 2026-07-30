"""Milestone facade binding autonomous computation decisions and runs."""
from __future__ import annotations

from pathlib import Path

from .problem_graph import ProblemGraph
from .stages_core import StageService as _StageService
from .toolchain import ToolchainService
from .toolchain_registry import ToolRegistry


class StageService(_StageService):
    def __init__(self, project: Path):
        # ToolRegistry performs one-time V3.0 -> V3.1 config migration before
        # ProblemGraph starts including tool configuration in contract hashes.
        ToolRegistry(project)
        super().__init__(project)

    def _problem_metadata(self, stage: str) -> dict:
        metadata = super()._problem_metadata(stage)
        if ProblemGraph(self.project).exists:
            metadata["toolchain_contract_sha256"] = ToolRegistry(
                self.project
            ).catalog_hash()
        return metadata

    def gate(self, stage: str) -> dict:
        if ProblemGraph(self.project).exists:
            registry_errors = ToolRegistry(self.project).audit_catalog()
            decision_errors = ToolchainService(self.project).audit_stage(stage)
            errors = [*registry_errors, *decision_errors]
            if errors:
                raise RuntimeError("\n".join(errors))
        return super().gate(stage)
