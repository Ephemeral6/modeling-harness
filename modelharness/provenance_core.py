"""Cross-run provenance audits: warm-start declarations and baseline copies."""
from __future__ import annotations

from pathlib import Path

from .storage import CorruptStateError, read_json
from .util import sha256

WARM_START_PREFIX = "warm_start"
SCAN_DIRS = ("results", "predictions")


def _walk_keys(node, prefix: str = ""):
    if isinstance(node, dict):
        for key, value in node.items():
            dotted = f"{prefix}{key}" if isinstance(key, str) else prefix
            if isinstance(key, str) and key.startswith(WARM_START_PREFIX):
                yield dotted, value
            yield from _walk_keys(value, f"{dotted}.")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_keys(value, f"{prefix}{index}.")


def _declared_hashes(warm: dict | None) -> set[str]:
    declared = warm.get("source_sha256") if isinstance(warm, dict) else None
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {value for value in declared if isinstance(value, str)}
    return set()


def audit_warm_start_keys(root: Path) -> list[str]:
    """Flag warm_start* fields in run outputs that lack a matching policy."""
    root = root.resolve()
    errors: list[str] = []
    findings: list[tuple[str, str, object]] = []
    for name in SCAN_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.json")):
            rel = path.relative_to(root).as_posix()
            try:
                data = read_json(path)
            except CorruptStateError:
                errors.append(f"{rel}: 无法解析 JSON，warm-start 审计不可判定")
                continue
            for dotted, value in _walk_keys(data):
                findings.append((rel, dotted, value))
    if not findings:
        return errors
    policy = read_json(root / "config" / "search_policy.json")
    warm = policy.get("warm_start") if isinstance(policy, dict) else None
    allowed = isinstance(warm, dict) and warm.get("allowed") is True
    declared = _declared_hashes(warm)
    for rel, dotted, value in findings:
        leaf = dotted.rsplit(".", 1)[-1]
        if policy is None:
            errors.append(
                f"{rel}: {dotted} 使用 warm-start 但缺失 "
                "config/search_policy.json 声明"
            )
        elif not allowed:
            errors.append(
                f"{rel}: {dotted} 使用 warm-start 但 warm_start.allowed 不为 true"
            )
        elif "sha256" in leaf and (
            not isinstance(value, str) or value not in declared
        ):
            errors.append(
                f"{rel}: {dotted} 与声明的 warm_start.source_sha256 不符"
            )
    return errors


def _load_baseline_registries(baselines: Path) -> list[dict]:
    baselines = baselines.resolve()
    if baselines.is_dir():
        paths = sorted(baselines.glob("*.json"))
    elif baselines.is_file():
        paths = [baselines]
    else:
        raise ValueError(f"基线登记表不存在: {baselines}")
    registries = []
    for path in paths:
        registry = read_json(path)
        if isinstance(registry, dict) and registry.get("schema") == 1:
            registries.append(registry)
    return registries


def _registered_hashes(manifest) -> set[str]:
    registered: set[str] = set()
    if isinstance(manifest, dict):
        for key, value in manifest.items():
            if key == "sha256" and isinstance(value, str):
                registered.add(value)
            registered |= _registered_hashes(value)
    elif isinstance(manifest, list):
        for value in manifest:
            registered |= _registered_hashes(value)
    return registered


def scan_against_baselines(root: Path, baselines: Path) -> list[str]:
    """Flag project .py files byte-identical to a registered baseline run."""
    root = root.resolve()
    index: dict[str, tuple[str, str]] = {}
    for registry in _load_baseline_registries(baselines):
        baseline_id = str(registry.get("baseline_id") or "unknown")
        files = registry.get("files")
        for item in files if isinstance(files, list) else []:
            if isinstance(item, dict) and isinstance(item.get("sha256"), str):
                index.setdefault(
                    item["sha256"], (baseline_id, str(item.get("path", "")))
                )
    if not index:
        return []
    registered = _registered_hashes(
        read_json(root / "problem" / "import_manifest.json")
    )
    errors: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest = sha256(path)
        hit = index.get(digest)
        if hit is not None and digest not in registered:
            rel = path.relative_to(root).as_posix()
            errors.append(
                f"unregistered cross-run copy: {rel} matches {hit[0]}/{hit[1]}"
            )
    return errors
