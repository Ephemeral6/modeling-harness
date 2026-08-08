"""Opt-in assurance for optimization-heavy modeling tasks.

This module deliberately adds no new workflow stage.  It provides small,
derived audits that existing S1/S3/S5 gates can call when an optimization
problem is detected.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from .calibration import CONSTRAINT_ORIGINS, SELF_IMPOSED
from .contracts import safe_relative
from .storage import atomic_write_json, read_json
from .util import sha256


OPTIMIZATION_SCOPES = {
    "GLOBAL_CERTIFICATE",
    "RESTRICTED_CLASS_OPTIMAL",
    "BOUNDED_GAP",
    "BEST_KNOWN_FEASIBLE",
    "FEASIBLE_ONLY",
    "UNKNOWN",
}
SEMANTIC_TYPES = {
    "measurement",
    "estimate",
    "lower_bound",
    "upper_bound",
    "feasible_solution",
    "best_known_solution",
    "optimal_solution",
    "simulation_mean",
    "recommendation",
}
DECISION_VARIABLE_ROLES = {"decision", "parameter", "derived"}
_OPTIMIZATION_RE = re.compile(
    r"最优|优化|最大化|最小化|排程|调度|路径规划|资源分配|组合优化|"
    r"整数规划|线性规划|mixed.integer|optimization|scheduling|routing",
    re.IGNORECASE,
)


def _config(root: Path) -> dict[str, Any]:
    value = read_json(root / "config" / "optimization_assurance.json", {})
    return value if isinstance(value, dict) else {}


def _requirements(root: Path) -> dict[str, dict]:
    value = read_json(root / "problem" / "requirements.json", {})
    records = value.get("requirements", {}) if isinstance(value, dict) else {}
    return records if isinstance(records, dict) else {}


def _artifact_hash(root: Path, relative: str) -> str | None:
    try:
        path = safe_relative(root, relative)
    except ValueError:
        return None
    return sha256(path) if path.is_file() else None


def optimization_relevant(root: Path) -> bool:
    """Conservatively detect optimization tasks; projects may override mode."""
    root = root.resolve()
    mode = str(_config(root).get("mode", "auto")).lower()
    if mode == "required":
        return True
    if mode == "disabled":
        return False
    if (root / "problem" / "constraint_ledger.json").is_file():
        return True
    graph = read_json(root / ".harness" / "problem_graph.json", {})
    nodes = graph.get("nodes", {}) if isinstance(graph, dict) else {}
    for node in nodes.values() if isinstance(nodes, dict) else []:
        if not isinstance(node, dict):
            continue
        if node.get("method_pack") == "mathematical-optimization":
            return True
        if str(node.get("task_type", "")).lower() in {
            "optimization", "routing", "scheduling",
        }:
            return True
    for item in _requirements(root).values():
        if not isinstance(item, dict):
            continue
        if item.get("optimization") is True or str(
            item.get("task_family", "")
        ).lower() in {"optimization", "routing", "scheduling"}:
            return True
        text = " ".join(
            str(item.get(key, "")) for key in ("question", "statement", "text")
        )
        if _OPTIMIZATION_RE.search(text):
            return True
    return False


def initialize_constraint_ledger(root: Path) -> dict:
    """Create an editable S1 ledger from source-derived hard requirements."""
    root = root.resolve()
    path = root / "problem" / "constraint_ledger.json"
    if path.exists():
        raise ValueError("problem/constraint_ledger.json already exists")
    constraints: dict[str, dict] = {}
    for requirement_id, item in sorted(_requirements(root).items()):
        if not isinstance(item, dict) or item.get("mandatory") is False:
            continue
        if item.get("type") not in {"constraint", "model_condition", "prohibition"}:
            continue
        constraints[f"constraint.{requirement_id}"] = {
            "statement": item.get("question") or item.get("statement") or requirement_id,
            "type": item.get("type"),
            "hard": True,
            "scope": "source",
            "source_requirement_ids": [requirement_id],
            "status": "UNMODELED",
            "reason": "Classify and encode this source requirement during S1.",
            "mathematical_form": "",
            "implementation": {
                "solver_artifact": "",
                "checker_artifact": "",
            },
        }
    value = {"schema": 1, "constraints": constraints}
    atomic_write_json(path, value)
    return value


def render_constraint_ledger(root: Path) -> Path:
    """Render the JSON authority as a readable model-specification view."""
    root = root.resolve()
    data = read_json(root / "problem" / "constraint_ledger.json", {})
    constraints = data.get("constraints", {}) if isinstance(data, dict) else {}
    lines = [
        "# Constraint Ledger",
        "",
        "> Generated from `problem/constraint_ledger.json`; edit the JSON authority.",
        "",
        "| ID | Hard | Status | Source | Mathematical form |",
        "|---|---:|---|---|---|",
    ]
    for constraint_id, item in sorted(constraints.items() if isinstance(constraints, dict) else []):
        if not isinstance(item, dict):
            continue
        form = str(item.get("mathematical_form", "")).replace("|", "\\|")
        source = ", ".join(map(str, item.get("source_requirement_ids", [])))
        lines.append(
            f"| {constraint_id} | {'yes' if item.get('hard', True) else 'no'} | "
            f"{item.get('status', '')} | {source} | {form} |"
        )
    output = root / "docs" / "constraints.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _origin_errors(item: dict, constraint_id: str) -> list[str]:
    """Audit the optional ``origin`` field; ledgers without it stay clean.

    A constraint the modeler invented must say why it exists and where the
    paper discloses it, so a self-imposed management rule cannot masquerade
    as a statement requirement.
    """
    origin = item.get("origin")
    if origin is None:
        return []
    if origin not in CONSTRAINT_ORIGINS:
        return [f"invalid constraint origin: {constraint_id}"]
    if origin != SELF_IMPOSED:
        return []
    return [
        f"self-imposed constraint missing {field}: {constraint_id}"
        for field in ("rationale", "disclosure_anchor")
        if not str(item.get(field, "")).strip()
    ]


def audit_constraint_ledger(root: Path, phase: str = "model") -> list[str]:
    root = root.resolve()
    if not optimization_relevant(root):
        return []
    data = read_json(root / "problem" / "constraint_ledger.json")
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["constraint_ledger missing or schema is not 1"]
    constraints = data.get("constraints")
    if not isinstance(constraints, dict):
        return ["constraint_ledger.constraints must be an object"]
    errors: list[str] = []
    covered: set[str] = set()
    for constraint_id, item in constraints.items():
        if not isinstance(item, dict):
            errors.append(f"constraint is not an object: {constraint_id}")
            continue
        for field in ("statement", "type", "scope"):
            if not str(item.get(field, "")).strip():
                errors.append(f"constraint missing {field}: {constraint_id}")
        errors.extend(_origin_errors(item, constraint_id))
        source_ids = item.get("source_requirement_ids", [])
        if not isinstance(source_ids, list):
            errors.append(f"source_requirement_ids must be a list: {constraint_id}")
            source_ids = []
        covered.update(str(value) for value in source_ids)
        status = item.get("status")
        if status not in {"MODELED", "UNMODELED", "NOT_APPLICABLE"}:
            errors.append(f"invalid constraint status: {constraint_id}")
        elif status == "MODELED" and not str(item.get("mathematical_form", "")).strip():
            errors.append(f"MODELED constraint lacks mathematical_form: {constraint_id}")
        elif status == "UNMODELED":
            if not str(item.get("reason", "")).strip():
                errors.append(f"UNMODELED constraint lacks reason: {constraint_id}")
            if item.get("hard", True):
                errors.append(f"hard constraint remains UNMODELED: {constraint_id}")
        elif status == "NOT_APPLICABLE":
            disposition = item.get("disposition_review", {})
            if not str(item.get("reason", "")).strip() or not (
                isinstance(disposition, dict)
                and str(disposition.get("authority", "")).lower() == "human"
                and str(disposition.get("verdict", "")).lower() in {"pass", "approve"}
                and str(disposition.get("reviewer_id", "")).strip()
            ):
                errors.append(f"NOT_APPLICABLE lacks human disposition: {constraint_id}")
        if phase in {"implementation", "solver", "all"} and status == "MODELED":
            implementation = item.get("implementation", {})
            if not isinstance(implementation, dict):
                errors.append(f"constraint implementation missing: {constraint_id}")
                continue
            solver = str(implementation.get("solver_artifact", ""))
            checker = str(implementation.get("checker_artifact", ""))
            if not solver or _artifact_hash(root, solver) is None:
                errors.append(f"solver implementation missing: {constraint_id}")
            if not checker or _artifact_hash(root, checker) is None:
                errors.append(f"independent checker missing: {constraint_id}")
            if solver and checker and solver == checker:
                errors.append(f"solver and checker reuse one artifact: {constraint_id}")
    for requirement_id, item in _requirements(root).items():
        if (
            isinstance(item, dict)
            and item.get("mandatory") is not False
            and item.get("type") in {"constraint", "model_condition", "prohibition"}
            and requirement_id not in covered
        ):
            errors.append(f"mandatory hard requirement not in constraint ledger: {requirement_id}")
    return sorted(set(errors))


def _audit_hash_record(root: Path, record: Any, label: str) -> list[str]:
    if not isinstance(record, dict):
        return [f"{label} record missing"]
    relative = str(record.get("artifact", ""))
    actual = _artifact_hash(root, relative) if relative else None
    if actual is None:
        return [f"{label} artifact missing: {relative}"]
    if record.get("sha256") != actual:
        return [f"{label} artifact hash stale: {relative}"]
    return []


def audit_feasibility(root: Path) -> list[str]:
    """Verify a candidate with an implementation independent of the optimizer."""
    root = root.resolve()
    if not optimization_relevant(root):
        return []
    data = read_json(root / "results" / "feasibility_audit.json")
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["feasibility_audit missing or schema is not 1"]
    errors: list[str] = []
    ledger_path = root / "problem" / "constraint_ledger.json"
    if (
        not ledger_path.is_file()
        or data.get("constraint_ledger_sha256") != sha256(ledger_path)
    ):
        errors.append("feasibility constraint ledger hash is stale")
    for label in ("candidate", "solver", "checker"):
        errors.extend(_audit_hash_record(root, data.get(label), label))
    candidate, solver, checker = (
        data.get("candidate", {}), data.get("solver", {}), data.get("checker", {})
    )
    producer_id = str(candidate.get("producer_id", "")) if isinstance(candidate, dict) else ""
    solver_id = str(solver.get("identity", "")) if isinstance(solver, dict) else ""
    checker_id = str(checker.get("identity", "")) if isinstance(checker, dict) else ""
    if not producer_id or not solver_id or not checker_id:
        errors.append("candidate, solver, and checker identities are required")
    if checker_id in {producer_id, solver_id}:
        errors.append("checker identity is not independent")
    if isinstance(checker, dict) and checker.get("implementation_reuse") is not False:
        errors.append("checker must declare implementation_reuse=false")
    if isinstance(solver, dict) and isinstance(checker, dict) and solver.get("artifact") == checker.get("artifact"):
        errors.append("solver and checker artifacts must differ")
    if str(data.get("execution_status", "")).lower() != "completed":
        errors.append("feasibility audit did not complete")
    if str(data.get("verdict", "")).lower() != "pass":
        errors.append("feasibility verdict is not PASS")
    results = data.get("constraints", {})
    ledger = read_json(root / "problem" / "constraint_ledger.json", {})
    constraints = ledger.get("constraints", {}) if isinstance(ledger, dict) else {}
    for constraint_id, item in constraints.items() if isinstance(constraints, dict) else []:
        if not isinstance(item, dict) or item.get("status") != "MODELED" or not item.get("hard", True):
            continue
        result = results.get(constraint_id) if isinstance(results, dict) else None
        if not isinstance(result, dict):
            errors.append(f"hard constraint was not independently checked: {constraint_id}")
            continue
        if str(result.get("execution_status", "")).lower() != "completed" or str(result.get("verdict", "")).lower() != "pass":
            errors.append(f"hard constraint did not PASS: {constraint_id}")
            continue
        violation, tolerance = result.get("max_violation"), result.get("tolerance")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in (violation, tolerance)):
            errors.append(f"constraint residual is not finite: {constraint_id}")
        elif float(violation) > float(tolerance):
            errors.append(f"constraint violation exceeds tolerance: {constraint_id}")
    return sorted(set(errors))


def audit_decision_manifest(root: Path) -> list[str]:
    """Audit the declared decision-variable manifest against real artifacts.

    The manifest is optional; when ``results/decision_variable_manifest.json``
    exists, every variable must carry a legal role and a source artifact whose
    hash still matches, so the paper cannot cite decision variables that no
    solver artifact actually contains.
    """
    root = root.resolve()
    path = root / "results" / "decision_variable_manifest.json"
    if not path.is_file():
        return []
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["decision_variable_manifest missing or schema is not 1"]
    variables = data.get("variables")
    if not isinstance(variables, list):
        return ["decision_variable_manifest.variables must be a list"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(variables):
        if not isinstance(item, dict):
            errors.append(f"decision variable is not an object: index {index}")
            continue
        variable_id = item.get("id")
        label = (
            variable_id
            if isinstance(variable_id, str) and variable_id
            else f"index {index}"
        )
        if not isinstance(variable_id, str) or not variable_id:
            errors.append(f"decision variable id missing: {label}")
        elif variable_id in seen:
            errors.append(f"duplicate decision variable id: {label}")
        else:
            seen.add(variable_id)
        if item.get("role") not in DECISION_VARIABLE_ROLES:
            errors.append(f"invalid decision variable role: {label}")
        if not str(item.get("statement", "")).strip():
            errors.append(f"decision variable statement missing: {label}")
        source = item.get("source")
        if not isinstance(source, dict):
            errors.append(f"decision variable source missing: {label}")
            continue
        relative = str(source.get("path", ""))
        actual = _artifact_hash(root, relative) if relative else None
        if actual is None:
            errors.append(
                f"decision variable source artifact missing: {label}: {relative}"
            )
        elif source.get("sha256") != actual:
            errors.append(
                f"decision variable source hash stale: {label}: {relative}"
            )
    return sorted(set(errors))


def audit_optimality(root: Path) -> list[str]:
    root = root.resolve()
    if not optimization_relevant(root):
        return []
    data = read_json(root / "results" / "optimality.json")
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["optimality.json missing or schema is not 1"]
    errors: list[str] = []
    scope = data.get("scope")
    if scope not in OPTIMIZATION_SCOPES:
        errors.append("invalid optimality scope")
    if not str(data.get("model_scope", "")).strip():
        errors.append("optimality model_scope is required")
    objective = data.get("objective", {})
    if not isinstance(objective, dict) or objective.get("sense") not in {"max", "min"} or not isinstance(objective.get("value"), (int, float)):
        errors.append("optimality objective must contain sense and numeric value")
    feasibility_path = root / "results" / "feasibility_audit.json"
    feasibility = read_json(feasibility_path, {})
    if (
        not feasibility_path.is_file()
        or data.get("feasibility_audit_sha256") != sha256(feasibility_path)
    ):
        errors.append("optimality feasibility audit hash is stale")
    if isinstance(feasibility, dict):
        candidate = feasibility.get("candidate", {})
        if isinstance(candidate, dict) and data.get("candidate_id") != candidate.get("id"):
            errors.append("optimality candidate_id differs from feasibility candidate")
    if scope == "GLOBAL_CERTIFICATE":
        if audit_constraint_ledger(root, "implementation"):
            errors.append("global certificate has unresolved constraint ledger errors")
        certificate = data.get("certificate")
        errors.extend(_audit_hash_record(root, certificate, "optimality certificate"))
        if not isinstance(certificate, dict) or certificate.get("independent") is not True or str(certificate.get("verdict", "")).lower() != "pass":
            errors.append("global certificate must be independent and PASS")
    elif scope == "RESTRICTED_CLASS_OPTIMAL":
        if not isinstance(data.get("restrictions"), list) or not data.get("restrictions"):
            errors.append("restricted optimum must list restrictions")
    elif scope == "BOUNDED_GAP":
        lower, upper = data.get("lower_bound"), data.get("upper_bound")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in (lower, upper)) or float(lower) > float(upper):
            errors.append("bounded gap requires valid lower_bound <= upper_bound")
        elif isinstance(objective, dict) and isinstance(objective.get("value"), (int, float)) and not float(lower) <= float(objective["value"]) <= float(upper):
            errors.append("candidate objective lies outside certified bounds")
        gap = data.get("gap_fraction")
        if not isinstance(gap, (int, float)) or float(gap) < 0:
            errors.append("bounded gap requires non-negative gap_fraction")
        elif all(isinstance(value, (int, float)) for value in (lower, upper)):
            definition = data.get("gap_definition", "incumbent")
            if definition not in {"incumbent", "bound"}:
                errors.append("gap_definition must be incumbent or bound")
            else:
                incumbent = (
                    float(objective.get("value", 0))
                    if isinstance(objective, dict) else 0.0
                )
                sense = objective.get("sense") if isinstance(objective, dict) else None
                reference = (
                    float(upper) if sense == "max" else float(lower)
                ) if definition == "bound" else incumbent
                denominator = max(abs(reference), 1.0)
                certified = (float(upper) - float(lower)) / denominator
                if float(gap) + 1e-12 < certified:
                    errors.append("reported gap_fraction understates certified bounds")
    return sorted(set(errors))


def build_result_provenance(root: Path) -> dict:
    """Materialize semantic labels for headline numbers without duplicating values."""
    root = root.resolve()
    binding_path = root / "config" / "claim_bindings.json"
    values_path = root / "results" / "claim_values.json"
    bindings = read_json(binding_path, {})
    values = read_json(values_path, {})
    binding_claims = bindings.get("claims", {}) if isinstance(bindings, dict) else {}
    value_claims = values.get("claims", {}) if isinstance(values, dict) else {}
    records: dict[str, dict] = {}
    for claim_id, binding in sorted(binding_claims.items() if isinstance(binding_claims, dict) else []):
        if not isinstance(binding, dict) or binding.get("headline") is not True:
            continue
        value = value_claims.get(claim_id, {}) if isinstance(value_claims, dict) else {}
        source = value.get("source", {}) if isinstance(value, dict) else {}
        records[claim_id] = {
            "semantic_type": binding.get("semantic_type"),
            "value": value.get("source_value") if isinstance(value, dict) else None,
            "unit": binding.get("unit"),
            "requirement_ids": list(binding.get("requirement_ids", [])),
            "assumption_ids": list(binding.get("assumption_ids", [])),
            "candidate_id": binding.get("candidate_id"),
            "optimality_scope": binding.get("optimality_scope"),
            "source": source,
            "derivation_inputs": value.get("derivation_inputs", {}) if isinstance(value, dict) else {},
            "claim_status": value.get("status") if isinstance(value, dict) else None,
        }
    report = {
        "schema": 1,
        "generator": "modelharness.optimization.build_result_provenance",
        "binding_sha256": sha256(binding_path) if binding_path.is_file() else None,
        "claim_values_sha256": sha256(values_path) if values_path.is_file() else None,
        "claims": records,
    }
    atomic_write_json(root / "results" / "result_provenance.json", report)
    return report


def audit_result_provenance(root: Path) -> list[str]:
    root = root.resolve()
    if not optimization_relevant(root):
        return []
    path = root / "results" / "result_provenance.json"
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["result_provenance missing or schema is not 1"]
    errors: list[str] = []
    binding = root / "config" / "claim_bindings.json"
    values = root / "results" / "claim_values.json"
    if not binding.is_file() or data.get("binding_sha256") != sha256(binding):
        errors.append("result provenance binding hash is stale")
    if not values.is_file() or data.get("claim_values_sha256") != sha256(values):
        errors.append("result provenance claim-values hash is stale")
    feasibility_errors = audit_feasibility(root)
    optimality_errors = audit_optimality(root)
    errors.extend(optimality_errors)
    optimality = read_json(root / "results" / "optimality.json", {})
    current_scope = optimality.get("scope") if isinstance(optimality, dict) else None
    for claim_id, item in data.get("claims", {}).items() if isinstance(data.get("claims"), dict) else []:
        if not isinstance(item, dict):
            errors.append(f"invalid provenance claim: {claim_id}")
            continue
        semantic = item.get("semantic_type")
        if semantic not in SEMANTIC_TYPES:
            errors.append(f"invalid semantic_type: {claim_id}")
        if item.get("claim_status") != "valid":
            errors.append(f"headline claim is not valid: {claim_id}")
        if semantic in {"lower_bound", "upper_bound"} and not item.get("derivation_inputs") and not item.get("source"):
            errors.append(f"bound lacks derivation/source: {claim_id}")
        if semantic in {"feasible_solution", "best_known_solution", "optimal_solution"} and feasibility_errors:
            errors.append(f"candidate claim lacks passed feasibility audit: {claim_id}")
        expected_value = None
        if semantic in {"feasible_solution", "best_known_solution", "optimal_solution"}:
            objective = optimality.get("objective", {}) if isinstance(optimality, dict) else {}
            expected_value = objective.get("value") if isinstance(objective, dict) else None
        elif semantic == "lower_bound" and isinstance(optimality, dict):
            expected_value = optimality.get("lower_bound")
        elif semantic == "upper_bound" and isinstance(optimality, dict):
            expected_value = optimality.get("upper_bound")
        if expected_value is not None and item.get("value") != expected_value:
            errors.append(f"headline value differs from its optimization semantic: {claim_id}")
        if semantic == "optimal_solution" and current_scope != "GLOBAL_CERTIFICATE":
            errors.append(f"optimal_solution lacks GLOBAL_CERTIFICATE: {claim_id}")
        if item.get("optimality_scope") and item.get("optimality_scope") != current_scope:
            errors.append(f"claim optimality scope is stale: {claim_id}")
    return sorted(set(errors))


def _relative_improvement(objective: dict) -> float | None:
    value, baseline = objective.get("value"), objective.get("baseline_value")
    if not all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in (value, baseline)):
        return None
    denominator = max(abs(float(baseline)), 1e-12)
    delta = float(value) - float(baseline)
    if objective.get("sense") == "min":
        delta = -delta
    return delta / denominator


def assess_optimization(root: Path) -> dict:
    """Separate mechanical blockers from semantic human-review triggers."""
    root = root.resolve()
    relevant = optimization_relevant(root)
    if not relevant:
        return {
            "relevant": False,
            "machine_status": "NOT_APPLICABLE",
            "human_level": "AUTO_CONTINUE",
            "machine_blockers": [],
            "triggers": [],
        }
    from .search_quality import audit_search_quality

    blockers = {
        "constraints": audit_constraint_ledger(root, "implementation"),
        "feasibility": audit_feasibility(root),
        "optimality": audit_optimality(root),
        "provenance": audit_result_provenance(root),
        "search_quality": audit_search_quality(root),
    }
    flat_blockers = [f"{kind}: {message}" for kind, values in blockers.items() for message in values]
    triggers: list[dict] = []
    segmentation = read_json(root / "problem" / "source_segmentation.json", {})
    requirements = _requirements(root)
    if (isinstance(segmentation, dict) and segmentation.get("needs_review")) or any(
        isinstance(item, dict) and (item.get("requires_human") is True or item.get("ambiguity") in {"open", "material"})
        for item in requirements.values()
    ):
        triggers.append({"code": "source_ambiguity", "severity": "recommended"})
    ledger = read_json(root / "problem" / "constraint_ledger.json", {})
    for constraint_id, item in (ledger.get("constraints", {}) if isinstance(ledger, dict) else {}).items():
        if isinstance(item, dict) and item.get("hard", True) and item.get("status") == "UNMODELED":
            triggers.append({"code": "unmodeled_hard_constraint", "severity": "recommended", "constraint_id": constraint_id})
    from .opportunities import build_ledger
    opportunities = build_ledger(root)
    load_bearing = [item for item in opportunities if item.get("kind") == "unmaterialized_assumption_branch"]
    if load_bearing:
        triggers.append({"code": "unresolved_load_bearing_assumption", "severity": "recommended", "opportunity_ids": [item["id"] for item in load_bearing]})
    optimality = read_json(root / "results" / "optimality.json", {})
    objective = optimality.get("objective", {}) if isinstance(optimality, dict) else {}
    improvement = _relative_improvement(objective) if isinstance(objective, dict) else None
    threshold = float(_config(root).get("suspicious_improvement_fraction", 0.01))
    if improvement is not None and improvement > threshold:
        triggers.append({
            "code": "suspicious_improvement",
            "severity": "required" if load_bearing else "recommended",
            "fraction": improvement,
            "threshold": threshold,
        })
    high_opportunities = [
        item for item in opportunities
        if item.get("severity") == "high" and item.get("kind") != "unmaterialized_assumption_branch"
    ]
    if high_opportunities:
        triggers.append({"code": "high_value_opportunity", "severity": "recommended", "opportunity_ids": [item["id"] for item in high_opportunities]})
    scope = optimality.get("scope") if isinstance(optimality, dict) else None
    gap = optimality.get("gap_fraction") if isinstance(optimality, dict) else None
    if scope == "BOUNDED_GAP" and isinstance(gap, (int, float)) and gap <= float(_config(root).get("near_bound_gap_fraction", 0.02)):
        triggers.append({"code": "near_certified_bound", "severity": "recommended", "gap_fraction": gap})
    config = _config(root)
    required_codes = set(config.get("require_human_for", []))
    for item in triggers:
        if item.get("code") in required_codes:
            item["severity"] = "required"
    policy = str(config.get("human_policy", "risk_triggered")).lower()
    human_level = "AUTO_CONTINUE"
    if policy == "always":
        human_level = "HUMAN_REQUIRED"
    elif policy != "disabled" and any(
        item["severity"] == "required" for item in triggers
    ):
        human_level = "HUMAN_REQUIRED"
    elif policy != "disabled" and triggers:
        human_level = "HUMAN_RECOMMENDED"
    return {
        "relevant": True,
        "machine_status": "BLOCKED" if flat_blockers else "READY",
        "human_level": human_level,
        "machine_blockers": flat_blockers,
        "checks": {key: not value for key, value in blockers.items()},
        "optimality_scope": scope,
        "triggers": triggers,
    }


def build_review_packet(root: Path) -> dict:
    root = root.resolve()
    assessment = assess_optimization(root)
    feasibility = read_json(root / "results" / "feasibility_audit.json", {})
    optimality = read_json(root / "results" / "optimality.json", {})
    artifact_paths = [
        "config/optimization_assurance.json",
        "problem/statement.md",
        "problem/requirements.json",
        "problem/source_segmentation.json",
        "problem/constraint_ledger.json",
        "results/candidate_solution.json",
        "results/feasibility_audit.json",
        "results/optimality.json",
        "results/result_provenance.json",
        "docs/assumptions.json",
    ]
    packet = {
        "schema": 1,
        "assessment": assessment,
        "candidate": feasibility.get("candidate") if isinstance(feasibility, dict) else None,
        "objective": optimality.get("objective") if isinstance(optimality, dict) else None,
        "optimality_scope": optimality.get("scope") if isinstance(optimality, dict) else None,
        "review_questions": [
            "Do the source interpretation and constraint scope match the problem statement?",
            "Is any load-bearing assumption hiding a materially better feasible regime?",
            "Is the claimed optimality scope no stronger than the supplied certificate?",
        ],
        "artifact_hashes": {
            relative: _artifact_hash(root, relative)
            for relative in artifact_paths
            if _artifact_hash(root, relative) is not None
        },
    }
    atomic_write_json(root / "reviews" / "optimization_review_packet.json", packet)
    return packet


def audit_human_review(root: Path) -> list[str]:
    root = root.resolve()
    assessment = assess_optimization(root)
    if assessment.get("human_level") != "HUMAN_REQUIRED":
        return []
    if assessment.get("machine_status") != "READY":
        return ["human review cannot override unresolved machine blockers"]
    packet_path = root / "reviews" / "optimization_review_packet.json"
    packet = read_json(packet_path)
    if not isinstance(packet, dict) or packet.get("schema") != 1:
        return ["optimization review packet missing"]
    errors: list[str] = []
    for relative, expected in packet.get("artifact_hashes", {}).items():
        if _artifact_hash(root, str(relative)) != expected:
            errors.append(f"optimization review packet is stale: {relative}")
    review = read_json(root / "reviews" / "optimization_human_review.json")
    if not isinstance(review, dict) or review.get("schema") != 1:
        return [*errors, "optimization human review missing"]
    if str(review.get("authority", "")).lower() != "human":
        errors.append("optimization review authority must be HUMAN")
    if str(review.get("verdict", "")).lower() not in {"approve", "pass"}:
        errors.append("optimization human review did not approve")
    if review.get("packet_sha256") != sha256(packet_path):
        errors.append("optimization human review packet hash is stale")
    reviewer = str(review.get("reviewer_id", ""))
    feasibility = read_json(root / "results" / "feasibility_audit.json", {})
    candidate = feasibility.get("candidate", {}) if isinstance(feasibility, dict) else {}
    producer = str(candidate.get("producer_id", ""))
    if not reviewer or reviewer == producer:
        errors.append("human reviewer cannot approve the producer own conclusion")
    return sorted(set(errors))


def audit_optimization(root: Path, phase: str = "all") -> list[str]:
    if not optimization_relevant(root):
        return []
    errors = audit_constraint_ledger(root, "model" if phase == "model" else "implementation")
    if phase in {"solver", "decision", "all"}:
        errors.extend(audit_feasibility(root))
        errors.extend(audit_optimality(root))
        errors.extend(audit_decision_manifest(root))
    if phase in {"decision", "all"}:
        errors.extend(audit_result_provenance(root))
        errors.extend(audit_human_review(root))
    return sorted(set(errors))
