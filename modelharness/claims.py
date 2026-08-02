"""Claim-binding evaluation, decision-lock validation, and display audit."""
from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .contracts import safe_relative
from .safeeval import evaluate
from .storage import atomic_write_json, read_json
from .util import sha256


GENERATOR = "modelharness.claims.evaluate_claims"


def json_pointer(data: Any, ptr: str) -> tuple[bool, Any]:
    """Resolve an RFC 6901 JSON Pointer, including array indexes."""
    if ptr == "":
        return True, data
    if not isinstance(ptr, str) or not ptr.startswith("/"):
        return False, None
    current = data
    for encoded in ptr[1:].split("/"):
        if re.search(r"~(?![01])", encoded):
            return False, None
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif (
            isinstance(current, list)
            and token.isdigit()
            and int(token) < len(current)
        ):
            current = current[int(token)]
        else:
            return False, None
    return True, current


def _reference(root: Path, reference: Any) -> tuple[Any, dict]:
    if not isinstance(reference, dict):
        raise ValueError("claim reference must be an object")
    if "const" in reference:
        return reference["const"], {"const": reference["const"]}
    relative = str(reference.get("artifact", ""))
    path = safe_relative(root, relative)
    if not path.is_file():
        raise ValueError(f"claim artifact missing: {relative}")
    data = read_json(path)
    pointer = str(reference.get("pointer", ""))
    found, value = json_pointer(data, pointer)
    if not found:
        raise ValueError(f"claim pointer missing: {relative}#{pointer}")
    return value, {
        "artifact": relative,
        "pointer": pointer,
        "artifact_sha256": sha256(path),
    }


def _numeric(value: Any) -> bool:
    return (
        isinstance(value, (int, float, Decimal))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _within(left: Any, right: Any, tolerance: dict) -> bool:
    if _numeric(left) and _numeric(right):
        return math.isclose(
            float(left),
            float(right),
            rel_tol=float(tolerance.get("rel", 0)),
            abs_tol=float(tolerance.get("abs", 0)),
        )
    return left == right


def _display(value: Any, display: Any) -> str:
    if not isinstance(display, dict):
        return str(value)
    decimals = int(display.get("decimals", 0))
    if decimals < 0:
        raise ValueError("display.decimals must be non-negative")
    if display.get("rounding", "half_up") != "half_up":
        raise ValueError("only half_up display rounding is supported")
    if not _numeric(value):
        raise ValueError("decimal display requires a finite numeric source")
    try:
        quantum = Decimal("1").scaleb(-decimals)
        rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"cannot format claim value: {value!r}") from exc
    return format(rounded, f".{decimals}f")


def _evaluate_one(root: Path, claim_id: str, binding: Any) -> dict:
    record: dict[str, Any] = {
        "status": "error",
        "errors": [],
    }
    if not isinstance(binding, dict):
        record["errors"].append(f"invalid binding: {claim_id}")
        return record
    record.update({
        "requirement_ids": list(binding.get("requirement_ids", [])),
        "value_type": binding.get("value_type"),
        "unit": binding.get("unit"),
        "scenario_set": binding.get("scenario_set"),
        "tolerance": dict(binding.get("tolerance", {})),
    })
    try:
        source_value, source_meta = _reference(root, binding.get("source"))
        record["source_value"] = source_value
        record["source"] = source_meta
        expected_type = binding.get("value_type")
        if expected_type == "number" and not _numeric(source_value):
            record["errors"].append("type_mismatch: expected number")

        derivation = binding.get("derivation")
        if derivation is None:
            derived_value = source_value
            inputs_meta: dict[str, dict] = {}
        elif not isinstance(derivation, dict):
            raise ValueError("derivation must be an object")
        else:
            values: dict[str, Any] = {}
            inputs_meta = {}
            for name, reference in derivation.get("inputs", {}).items():
                if not isinstance(name, str) or not name.isidentifier():
                    raise ValueError(f"invalid derivation input name: {name!r}")
                values[name], inputs_meta[name] = _reference(root, reference)
            derived_value = evaluate(str(derivation.get("expr", "")), values)
        record["derived_value"] = derived_value
        record["derivation_inputs"] = inputs_meta
        if not _within(source_value, derived_value, record["tolerance"]):
            record["errors"].append("value_drift: source and derivation differ")

        binding_unit = binding.get("unit")
        declared_units = [
            binding.get("source", {}).get("unit")
            if isinstance(binding.get("source"), dict) else None,
            derivation.get("unit") if isinstance(derivation, dict) else None,
        ]
        if any(unit is not None and unit != binding_unit for unit in declared_units):
            record["errors"].append("unit_mismatch: binding/source/derivation")

        record["displayed"] = _display(source_value, binding.get("display"))
        record["status"] = "valid" if not record["errors"] else "value_drift"
    except (ArithmeticError, TypeError, ValueError) as exc:
        record["errors"].append(str(exc))
    return record


def evaluate_claims(root: Path) -> dict:
    """Evaluate authoritative bindings and write the generated claim-value artifact."""
    root = root.resolve()
    binding_path = root / "config" / "claim_bindings.json"
    bindings = read_json(binding_path)
    if not isinstance(bindings, dict) or bindings.get("schema") != 1:
        raise ValueError("config/claim_bindings.json schema must be 1")
    claims = bindings.get("claims")
    if not isinstance(claims, dict):
        raise ValueError("config/claim_bindings.json claims must be an object")
    output_path = root / "results" / "claim_values.json"
    existing = read_json(output_path)
    if existing is not None and (
        not isinstance(existing, dict)
        or existing.get("generator") != GENERATOR
    ):
        raise ValueError(
            "results/claim_values.json is not generated by evaluate_claims"
        )
    report = {
        "schema": 1,
        "generator": GENERATOR,
        "binding_sha256": sha256(binding_path),
        "claims": {
            claim_id: _evaluate_one(root, claim_id, binding)
            for claim_id, binding in claims.items()
        },
    }
    atomic_write_json(output_path, report)
    return report


def _registered_claim(data: Any, claim_id: str) -> tuple[bool, Any, Any]:
    if not isinstance(data, dict):
        return False, None, None
    claims = data.get("claims")
    if not isinstance(claims, dict) or claim_id not in claims:
        return False, None, None
    item = claims[claim_id]
    if isinstance(item, dict) and "value" in item:
        return True, item["value"], item.get("unit")
    return True, item, None


def _display_present(text: str, claim_id: str, expected: str) -> bool:
    lines = [line for line in text.splitlines() if claim_id in line]
    escaped = re.escape(expected)
    pattern = re.compile(rf"(?<![\d.]){escaped}(?![\d.])")
    return any(pattern.search(line) for line in lines)


def audit_claims(root: Path, paper: str = "paper/final.md") -> list[str]:
    """Audit derivation, registered decision values, units, and final display."""
    root = root.resolve()
    if not (root / "config" / "claim_bindings.json").is_file():
        return []
    report = evaluate_claims(root)
    bindings = read_json(root / "config" / "claim_bindings.json", {})
    registered = read_json(root / "predictions" / "registered.json")
    paper_text: str | None = None
    if paper:
        paper_path = safe_relative(root, paper)
        paper_text = paper_path.read_text(encoding="utf-8") if paper_path.is_file() else None
    errors: list[str] = []
    for claim_id, record in report["claims"].items():
        binding = bindings["claims"][claim_id]
        if record.get("status") != "valid":
            for error in record.get("errors", []):
                errors.append(f"{error}: {claim_id}")
        found, locked, locked_unit = _registered_claim(registered, claim_id)
        if not found:
            errors.append(f"registered_missing: {claim_id}")
        elif not _within(
            locked,
            record.get("source_value"),
            binding.get("tolerance", {}),
        ):
            errors.append(f"value_drift: {claim_id} registered value differs")
        if (
            locked_unit is not None
            and locked_unit != binding.get("unit")
        ):
            errors.append(f"unit_mismatch: {claim_id} registered unit differs")
        if paper_text is None and paper:
            errors.append(f"display_drift: {claim_id} paper missing")
        elif (
            paper_text is not None
            and not _display_present(
                paper_text, claim_id, str(record.get("displayed", ""))
            )
        ):
            errors.append(f"display_drift: {claim_id}")
    return sorted(set(errors))
