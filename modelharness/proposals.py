"""Lightweight action proposals and policy decisions.

Proposals are an audit boundary, not a role workflow. Local reversible research
is allowed immediately; only new authority or destructive side effects stop.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from .storage import atomic_write_json, read_json
from .supervisor import StateCapsule
from .toolchain_registry import ToolRegistry
from .util import now
from .workflow import WorkflowEngine


DECISIONS = {
    "ALLOW", "ALLOW_WITH_OBLIGATIONS", "REVISE",
    "ESCALATE_TO_USER", "DENY",
}


class PolicyKernel:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.autonomy = ToolRegistry(self.project).autonomy

    def decide(self, proposal: dict) -> dict:
        reason = str(proposal.get("reason", "")).strip()
        action = str(proposal.get("action", "")).strip()
        if not action or not reason:
            return {
                "decision": "REVISE",
                "reason": "action and short auditable reason are required",
                "obligations": [],
            }
        risk = proposal.get("risk", "local")
        side_effect = proposal.get("side_effect", "project_local")
        if side_effect in {"external_write", "destructive"}:
            risk = side_effect
        authorized = bool(proposal.get("user_authorized", False))
        if risk == "destructive":
            if not authorized:
                return {
                    "decision": "DENY",
                    "reason": "destructive action lacks explicit authorization",
                    "obligations": [],
                }
            return {
                "decision": "ALLOW_WITH_OBLIGATIONS",
                "reason": "destructive action is explicitly authorized",
                "obligations": ["record exact targets and recovery path"],
            }
        if risk == "external_write":
            if not authorized:
                return {
                    "decision": "ESCALATE_TO_USER",
                    "reason": "external write requires new user authority",
                    "obligations": ["record external transaction identifier"],
                }
            return {
                "decision": "ALLOW_WITH_OBLIGATIONS",
                "reason": "external write is explicitly authorized",
                "obligations": ["record external transaction identifier"],
            }
        if risk == "installation":
            if not self.autonomy.get("allow_install", False) and not authorized:
                return {
                    "decision": "ESCALATE_TO_USER",
                    "reason": "installation is not autonomously authorized",
                    "obligations": ["record package and locked version"],
                }
            return {
                "decision": "ALLOW_WITH_OBLIGATIONS",
                "reason": "installation is authorized",
                "obligations": ["lock version", "record environment change"],
            }
        if risk in {"network", "commercial"}:
            if (
                risk not in set(self.autonomy["autonomous_risks"])
                and not authorized
            ):
                return {
                    "decision": "ESCALATE_TO_USER",
                    "reason": f"{risk} capability requires new authority",
                    "obligations": [],
                }
            obligations = ["cache source and record provenance"]
            if risk == "commercial":
                obligations.append("record license and cost")
            return {
                "decision": "ALLOW_WITH_OBLIGATIONS",
                "reason": f"{risk} action is authorized",
                "obligations": obligations,
            }
        if risk != "local":
            return {
                "decision": "REVISE",
                "reason": f"unknown risk class: {risk}",
                "obligations": [],
            }
        return {
            "decision": "ALLOW",
            "reason": "local reversible research action",
            "obligations": [],
        }


class ProposalService:
    def __init__(self, project: Path):
        self.project = project.resolve()
        self.directory = self.project / ".harness" / "proposals"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.policy = PolicyKernel(self.project)

    def submit(
        self,
        *,
        action: str,
        actor: str,
        reason: str,
        target: dict | None = None,
        risk: str = "local",
        side_effect: str = "project_local",
        expected_artifacts: list[str] | None = None,
        verification: list | None = None,
        user_authorized: bool = False,
    ) -> dict:
        proposal_id = f"{int(time.time())}-{uuid.uuid4().hex[:10]}"
        record = {
            "schema": 1,
            "id": proposal_id,
            "time": now(),
            "action": action,
            "actor": actor,
            "reason": reason,
            "target": target or {},
            "risk": risk,
            "side_effect": side_effect,
            "expected_artifacts": expected_artifacts or [],
            "verification": verification or [],
            "user_authorized": user_authorized,
            "input_state_hash": StateCapsule(
                self.project
            ).build()["state_hash"],
        }
        record["policy"] = self.policy.decide(record)
        if record["policy"]["decision"] not in DECISIONS:
            raise RuntimeError("policy returned an invalid decision")
        atomic_write_json(
            self.directory / f"{proposal_id}.json", record
        )
        WorkflowEngine(self.project).event("action.proposed", {
            "proposal_id": proposal_id,
            "action": action,
            "decision": record["policy"]["decision"],
            "risk": risk,
            "target": record["target"],
        })
        return record

    def auto(
        self, action: str, reason: str, target: dict | None = None
    ) -> dict:
        return self.submit(
            action=action,
            actor="agent",
            reason=reason,
            target=target,
        )

    def list(self) -> list[dict]:
        return [
            read_json(path)
            for path in sorted(self.directory.glob("*.json"))
            if isinstance(read_json(path), dict)
        ]

    def get(self, proposal_id: str) -> dict:
        if not proposal_id or any(
            item in proposal_id for item in ("/", "\\", "..")
        ):
            raise ValueError("invalid proposal id")
        record = read_json(self.directory / f"{proposal_id}.json")
        if not isinstance(record, dict):
            raise ValueError(f"proposal does not exist: {proposal_id}")
        return record
