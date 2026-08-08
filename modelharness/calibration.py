"""Freeze the numbers the modeler invented, so two runs cannot disagree quietly.

A *self-imposed* quantity is a number the problem statement never fixed: a
minimum planting share, a dispersion cap, a risk weight.  Nothing in the
harness used to pin such a number down, so the same task solved twice could
carry two different management calibrations and report incomparable headline
numbers without a single mechanical complaint.

The freeze contract (``config/calibration_freeze.json``) binds every
self-imposed quantity to the authoritative ``results`` field that must still
reproduce it.  Drift is only allowed when it is confessed in writing
(``superseded_reason``).  A run that inherits a baseline copies the freeze
verbatim, registers the reference in the import manifest and materializes an
entry-by-entry diff, so a changed calibration is a visible decision rather
than an accident.

Projects that never wrote a freeze file are untouched by this module.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .checks_core import _json_path
from .contracts import safe_relative
from .provenance_core import register_import
from .storage import atomic_write_json, read_json
from .util import now

FREEZE_RELPATH = "config/calibration_freeze.json"
LEDGER_RELPATH = "problem/constraint_ledger.json"
DIFF_RELPATH = "results/calibration_diff.json"
SELF_IMPOSED = "self_imposed"
CONSTRAINT_ORIGINS = ("statement", SELF_IMPOSED)
DIFF_STATES = ("same", "changed", "dropped", "added")
_REQUIRED_TEXT_FIELDS = ("kind", "statement", "rationale", "result_path", "frozen_at")
# Sentinel: an inherited entry whose result artifact this run has not produced
# yet.  Not an error, and never rendered to the agent as one.
_PENDING = object()


def freeze_path(root: Path) -> Path:
    return Path(root) / "config" / "calibration_freeze.json"


def _entries(path: Path) -> list[dict]:
    data = read_json(path, {})
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [item for item in entries if isinstance(item, dict)]


def _numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _values_equal(frozen: Any, actual: Any, tolerance: float = 0.0) -> bool:
    if _numeric(frozen) and _numeric(actual):
        return math.isclose(
            float(frozen), float(actual), rel_tol=0.0, abs_tol=max(tolerance, 0.0)
        )
    return frozen == actual


def _resolve_result(
    root: Path, result_path: str, inherited: bool = False
) -> tuple[Any, Any]:
    """Read ``<stem>.<dotted.field>`` out of ``results/<stem>.json``.

    ``inherited`` entries arrive from a baseline run before this run has
    computed anything.  Until the addressed result artifact exists there is
    no number to drift from, so a missing file is *pending*, not a failure —
    otherwise a freshly inherited project fails the freeze audit at S0 for
    results it is not scheduled to produce until S3/S4.  The entry still
    binds the moment the artifact appears, and the paper content contract
    keeps requiring its disclosure either way.
    """
    parts = str(result_path).split(".")
    if parts and parts[0] == "results":
        parts = parts[1:]
    if len(parts) < 2 or not all(part.strip() for part in parts):
        return (
            f"result_path must address a field under results/: {result_path}",
            None,
        )
    relative = f"results/{parts[0]}.json"
    try:
        path = safe_relative(root, relative)
    except ValueError as exc:
        return str(exc), None
    if not path.is_file():
        if inherited:
            return (_PENDING, None)
        return f"result artifact missing: {relative}", None
    found, value = _json_path(read_json(path), ".".join(parts[1:]))
    if not found:
        return f"result_path not found: {result_path}", None
    return None, value


def _entry_shape_errors(entry: dict, label: str, seen: set[str]) -> list[str]:
    errors: list[str] = []
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        errors.append(f"calibration freeze entry id missing: {label}")
    elif entry_id in seen:
        errors.append(f"duplicate calibration freeze entry id: {label}")
    else:
        seen.add(entry_id)
    for field in _REQUIRED_TEXT_FIELDS:
        if not str(entry.get(field, "")).strip():
            errors.append(f"calibration freeze entry missing {field}: {label}")
    if "value" not in entry:
        errors.append(f"calibration freeze entry missing value: {label}")
    if not isinstance(entry.get("unit"), str) or not entry["unit"].strip():
        errors.append(f"calibration freeze entry missing unit: {label}")
    if entry.get("origin") != SELF_IMPOSED:
        errors.append(
            f"calibration freeze entry origin must be self_imposed: {label}"
        )
    if not isinstance(entry.get("inherited"), bool):
        errors.append(
            f"calibration freeze entry inherited must be a boolean: {label}"
        )
    return errors


def audit_freeze(root: Path) -> list[str]:
    """Check every frozen self-imposed value against its authoritative field.

    A missing freeze file means the project predates the contract: silent by
    design, never a retroactive failure.
    """
    root = Path(root).resolve()
    path = freeze_path(root)
    if not path.is_file():
        return []
    data = read_json(path)
    if not isinstance(data, dict) or data.get("schema") != 1:
        return ["calibration_freeze missing or schema is not 1"]
    entries = data.get("entries")
    if not isinstance(entries, list):
        return ["calibration_freeze.entries must be a list"]
    errors: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(
                f"calibration freeze entry is not an object: index {index}"
            )
            continue
        entry_id = entry.get("id")
        label = (
            entry_id
            if isinstance(entry_id, str) and entry_id.strip()
            else f"index {index}"
        )
        errors.extend(_entry_shape_errors(entry, label, seen))
        superseded = entry.get("superseded_reason")
        if superseded is not None and (
            not isinstance(superseded, str) or not superseded.strip()
        ):
            errors.append(
                "calibration freeze superseded_reason must be a non-empty "
                f"string: {label}"
            )
            superseded = None
        result_path = entry.get("result_path")
        if not isinstance(result_path, str) or not result_path.strip():
            continue
        if isinstance(superseded, str) and superseded.strip():
            # The calibration was consciously replaced and the reason is on
            # disk; the frozen value no longer binds this run.
            continue
        tolerance = entry.get("tolerance", 0)
        if not _numeric(tolerance) or float(tolerance) < 0:
            errors.append(f"calibration freeze tolerance invalid: {label}")
            tolerance = 0
        message, actual = _resolve_result(
            root, result_path, inherited=entry.get("inherited") is True
        )
        if message is _PENDING:
            # Inherited but not yet recomputed by this run: nothing to compare.
            continue
        if message is not None:
            errors.append(f"calibration freeze {message}: {label}")
            continue
        if not _values_equal(entry.get("value"), actual, float(tolerance)):
            errors.append(
                f"self-imposed calibration drifted: {label}: frozen "
                f"{entry.get('value')!r} but {result_path} is {actual!r}"
            )
    return sorted(set(errors))


def self_imposed_records(root: Path) -> list[dict]:
    """Collect every self-imposed item the paper must disclose."""
    root = Path(root).resolve()
    records: list[dict] = []
    for entry in _entries(freeze_path(root)):
        if entry.get("origin") != SELF_IMPOSED:
            continue
        records.append({
            "id": str(entry.get("id", "")),
            "source": FREEZE_RELPATH,
            "statement": str(entry.get("statement", "")),
            "rationale": str(entry.get("rationale", "")),
            "disclosure_anchor": entry.get("disclosure_anchor"),
        })
    ledger = read_json(root / "problem" / "constraint_ledger.json", {})
    constraints = ledger.get("constraints") if isinstance(ledger, dict) else None
    for constraint_id, item in sorted(
        constraints.items() if isinstance(constraints, dict) else []
    ):
        if not isinstance(item, dict) or item.get("origin") != SELF_IMPOSED:
            continue
        records.append({
            "id": str(constraint_id),
            "source": LEDGER_RELPATH,
            "statement": str(item.get("statement", "")),
            "rationale": str(item.get("rationale", "")),
            "disclosure_anchor": item.get("disclosure_anchor"),
        })
    return records


def _baseline_root(root: Path, baseline: Path | str | None) -> Path:
    if baseline is None:
        data = read_json(freeze_path(root), {})
        recorded = data.get("inherited_from") if isinstance(data, dict) else None
        if not isinstance(recorded, str) or not recorded.strip():
            raise ValueError(
                f"{FREEZE_RELPATH} 缺少 inherited_from，且未显式给出 baseline"
            )
        baseline = recorded
    return Path(baseline).expanduser().resolve()


def build_calibration_diff(root: Path, baseline: Path | str | None = None) -> dict:
    """Materialize the entry-by-entry same/changed/dropped/added comparison."""
    root = Path(root).resolve()
    source_root = _baseline_root(root, baseline)
    before = {
        entry["id"]: entry
        for entry in _entries(freeze_path(source_root))
        if isinstance(entry.get("id"), str) and entry["id"]
    }
    after = {
        entry["id"]: entry
        for entry in _entries(freeze_path(root))
        if isinstance(entry.get("id"), str) and entry["id"]
    }
    records: list[dict] = []
    for entry_id in sorted(set(before) | set(after)):
        old, new = before.get(entry_id), after.get(entry_id)
        if old is None:
            state = "added"
        elif new is None:
            state = "dropped"
        elif _values_equal(old.get("value"), new.get("value")) and old.get(
            "unit"
        ) == new.get("unit"):
            state = "same"
        else:
            state = "changed"
        records.append({
            "id": entry_id,
            "state": state,
            "baseline_value": old.get("value") if isinstance(old, dict) else None,
            "value": new.get("value") if isinstance(new, dict) else None,
            "unit": (new or old or {}).get("unit"),
            "superseded_reason": (new or {}).get("superseded_reason"),
        })
    report = {
        "schema": 1,
        "generator": "modelharness.calibration.build_calibration_diff",
        "created_at": now(),
        "baseline": source_root.as_posix(),
        "counts": {
            state: sum(1 for item in records if item["state"] == state)
            for state in DIFF_STATES
        },
        "entries": records,
    }
    atomic_write_json(root / "results" / "calibration_diff.json", report)
    return report


def inherit_baseline(root: Path, baseline: Path | str) -> dict:
    """Carry a prior run's self-imposed calibration into a fresh project.

    Freeze entries and self-imposed ledger constraints arrive marked
    ``inherited: true``; the copy is registered as a ``reference`` import so
    the provenance audit can see where the numbers came from.
    """
    root = Path(root).resolve()
    source_root = Path(baseline).expanduser().resolve()
    if not source_root.is_dir():
        raise ValueError(f"baseline 项目目录不存在: {source_root}")
    if source_root == root:
        raise ValueError("baseline 不能指向当前项目自身")
    target = freeze_path(root)
    if target.exists():
        raise ValueError(f"{FREEZE_RELPATH} 已存在，拒绝覆盖")

    source_freeze = freeze_path(source_root)
    entries: list[dict] = []
    for entry in _entries(source_freeze):
        item = dict(entry)
        item["inherited"] = True
        # A prior run's supersede note explains the prior run, not this one.
        item.pop("superseded_reason", None)
        entries.append(item)
    atomic_write_json(target, {
        "schema": 1,
        "inherited_from": source_root.as_posix(),
        "inherited_at": now(),
        "entries": entries,
    })

    imports: list[dict] = []
    if source_freeze.is_file():
        imports.append(register_import(
            root, source_freeze, FREEZE_RELPATH, "reference",
            f"继承上一 run 的自设口径冻结: {source_root.name}",
        )["record"])

    source_ledger = source_root / "problem" / "constraint_ledger.json"
    ledger_data = read_json(source_ledger, {})
    constraints = (
        ledger_data.get("constraints") if isinstance(ledger_data, dict) else None
    )
    inherited: dict[str, dict] = {}
    for constraint_id, item in sorted(
        constraints.items() if isinstance(constraints, dict) else []
    ):
        if isinstance(item, dict) and item.get("origin") == SELF_IMPOSED:
            copied = dict(item)
            copied["inherited"] = True
            inherited[str(constraint_id)] = copied
    if inherited:
        ledger_target = root / "problem" / "constraint_ledger.json"
        current = read_json(ledger_target, {"schema": 1, "constraints": {}})
        if not isinstance(current, dict) or not isinstance(
            current.get("constraints"), dict
        ):
            raise ValueError(f"{LEDGER_RELPATH} 已存在但结构非法，拒绝继承")
        merged = dict(current["constraints"])
        for constraint_id, item in inherited.items():
            merged.setdefault(constraint_id, item)
        current["constraints"] = merged
        current.setdefault("schema", 1)
        atomic_write_json(ledger_target, current)
        imports.append(register_import(
            root, source_ledger, LEDGER_RELPATH, "reference",
            f"继承上一 run 的自设约束条目: {source_root.name}",
        )["record"])

    diff = build_calibration_diff(root, source_root)
    return {
        "ok": True,
        "baseline": source_root.as_posix(),
        "freeze": FREEZE_RELPATH,
        "diff": DIFF_RELPATH,
        "entries": len(entries),
        "constraints": len(inherited),
        "imports": imports,
        "counts": diff["counts"],
    }
