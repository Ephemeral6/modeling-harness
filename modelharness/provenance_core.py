"""Cross-run provenance: import manifest contract, warm starts, baseline copies."""
from __future__ import annotations

from pathlib import Path

from .storage import CorruptStateError, read_json
from .util import now, sha256, write_json

WARM_START_PREFIX = "warm_start"
SCAN_DIRS = ("results", "predictions")
PROJECT_CODE_DIRS = ("src", "code", "checks", "scripts")
IMPORT_METHODS = ("copy", "warm_start", "reference")
MANIFEST_RELPATH = "problem/import_manifest.json"
_HASH_KEYS = ("sha256", "stored_sha256", "source_sha256")


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


def _collect_warm_start(
    root: Path,
) -> tuple[list[str], list[tuple[str, str, object]]]:
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
    return errors, findings


def _warm_start_policy_errors(
    root: Path, findings: list[tuple[str, str, object]]
) -> list[str]:
    if not findings:
        return []
    policy = read_json(root / "config" / "search_policy.json")
    warm = policy.get("warm_start") if isinstance(policy, dict) else None
    allowed = isinstance(warm, dict) and warm.get("allowed") is True
    declared = _declared_hashes(warm)
    errors: list[str] = []
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


def audit_warm_start_keys(root: Path) -> list[str]:
    """Flag warm_start* fields in run outputs that lack a matching policy."""
    root = root.resolve()
    errors, findings = _collect_warm_start(root)
    return errors + _warm_start_policy_errors(root, findings)


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


def _baseline_index(baselines: Path) -> dict[str, tuple[str, str]]:
    index: dict[str, tuple[str, str]] = {}
    for registry in _load_baseline_registries(baselines):
        baseline_id = str(registry.get("baseline_id") or "unknown")
        files = registry.get("files")
        for item in files if isinstance(files, list) else []:
            if isinstance(item, dict) and isinstance(item.get("sha256"), str):
                index.setdefault(
                    item["sha256"], (baseline_id, str(item.get("path", "")))
                )
    return index


def _registered_hashes(manifest) -> set[str]:
    registered: set[str] = set()
    if isinstance(manifest, dict):
        for key, value in manifest.items():
            if key in _HASH_KEYS and isinstance(value, str):
                registered.add(value)
            registered |= _registered_hashes(value)
    elif isinstance(manifest, list):
        for value in manifest:
            registered |= _registered_hashes(value)
    return registered


def _scan_candidates(
    root: Path, subdirs: tuple[str, ...] | None
) -> list[Path]:
    candidates: list[Path] = []
    if subdirs is None:
        candidates.extend(root.rglob("*.py"))
    else:
        for name in subdirs:
            base = root / name
            if base.is_dir():
                candidates.extend(base.rglob("*.py"))
    return sorted(
        path for path in candidates if "__pycache__" not in path.parts
    )


def _unregistered_copy_errors(
    root: Path,
    index: dict[str, tuple[str, str]],
    registered: set[str],
    subdirs: tuple[str, ...] | None,
) -> list[str]:
    errors: list[str] = []
    for path in _scan_candidates(root, subdirs):
        digest = sha256(path)
        hit = index.get(digest)
        if hit is not None and digest not in registered:
            rel = path.relative_to(root).as_posix()
            errors.append(
                f"unregistered cross-run copy: {rel} matches {hit[0]}/{hit[1]}"
            )
    return errors


def scan_against_baselines(
    root: Path, baselines: Path, subdirs: tuple[str, ...] | None = None
) -> list[str]:
    """Flag project .py files byte-identical to a registered baseline run."""
    root = root.resolve()
    index = _baseline_index(baselines)
    if not index:
        return []
    registered = _registered_hashes(
        read_json(root / "problem" / "import_manifest.json")
    )
    return _unregistered_copy_errors(root, index, registered, subdirs)


def _manifest_file(root: Path) -> Path:
    return root / "problem" / "import_manifest.json"


def register_import(
    root: Path, source: Path | str, target: str, method: str, reason: str
) -> dict:
    """Append one import record with freshly computed hashes on both ends."""
    root = Path(root).resolve()
    if method not in IMPORT_METHODS:
        raise ValueError(
            f"method 必须是 {'/'.join(IMPORT_METHODS)} 之一: {method}"
        )
    if not str(reason).strip():
        raise ValueError("reason 不能为空")
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise ValueError(f"source 文件不存在: {source_path}")
    target_path = (root / target).resolve()
    try:
        stored_as = target_path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"target 必须是项目内相对路径: {target}") from exc
    if not target_path.is_file():
        raise ValueError(f"target 文件不存在: {stored_as}")
    manifest = read_json(_manifest_file(root), {"schema": 1, "imports": []})
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != 1
        or not isinstance(manifest.get("imports"), list)
    ):
        raise ValueError(f"{MANIFEST_RELPATH} 已存在但 schema 无效，拒绝追加")
    source_sha = sha256(source_path)
    stored_sha = sha256(target_path)
    imports = manifest["imports"]
    source_str = source_path.as_posix()
    for record in imports:
        if (
            isinstance(record, dict)
            and record.get("stored_as") == stored_as
            and record.get("source_path") == source_str
            and record.get("source_sha256") == source_sha
            and record.get("stored_sha256") == stored_sha
        ):
            return {"ok": True, "skipped": True, "record": record}
    record = {
        "index": len(imports),
        "source_path": source_str,
        "source_sha256": source_sha,
        "stored_as": stored_as,
        "stored_sha256": stored_sha,
        "method": method,
        "reason": str(reason),
        "registered_at": now(),
    }
    imports.append(record)
    write_json(_manifest_file(root), manifest)
    return {"ok": True, "skipped": False, "record": record}


def _load_manifest(root: Path) -> tuple[list[str], object, list[dict]]:
    path = _manifest_file(root)
    if not path.is_file():
        return [], None, []
    try:
        raw = read_json(path)
    except CorruptStateError:
        return [f"{MANIFEST_RELPATH}: 无法解析 JSON，溯源审计不可判定"], None, []
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != 1
        or not isinstance(raw.get("imports"), list)
    ):
        return (
            [f"{MANIFEST_RELPATH}: schema 无效（需要 schema=1 且 imports 为数组）"],
            raw,
            [],
        )
    errors: list[str] = []
    records: list[dict] = []
    for position, item in enumerate(raw["imports"]):
        if (
            isinstance(item, dict)
            and isinstance(item.get("stored_as"), str)
            and item["stored_as"]
            and isinstance(item.get("stored_sha256"), str)
        ):
            records.append(item)
        else:
            errors.append(
                f"{MANIFEST_RELPATH}: imports[{position}] "
                "缺少 stored_as/stored_sha256"
            )
    return errors, raw, records


def _manifest_integrity_errors(root: Path, records: list[dict]) -> list[str]:
    latest: dict[str, dict] = {}
    for record in records:
        latest[record["stored_as"]] = record
    errors: list[str] = []
    for stored_as, record in sorted(latest.items()):
        target = root / stored_as
        if not target.is_file():
            errors.append(
                f"{MANIFEST_RELPATH}: {stored_as} 已登记但文件缺失，请重新登记"
            )
        elif sha256(target) != record["stored_sha256"]:
            errors.append(
                f"{MANIFEST_RELPATH}: {stored_as} 内容已漂移"
                "（sha256 与登记的 stored_sha256 不符），请重新登记"
            )
    return errors


def _default_baselines_dir(root: Path) -> Path | None:
    for candidate in (root, *root.parents):
        found = candidate / "benchmarks" / "protocols" / "baselines"
        if found.is_dir():
            return found
    packaged = (
        Path(__file__).resolve().parents[1]
        / "benchmarks" / "protocols" / "baselines"
    )
    if packaged.is_dir():
        return packaged
    return None


def _checker_independence_errors(root: Path, records: list[dict]) -> list[str]:
    audit_path = root / "results" / "feasibility_audit.json"
    if not audit_path.is_file():
        return []
    try:
        data = read_json(audit_path)
    except CorruptStateError:
        return ["results/feasibility_audit.json: 无法解析 JSON，独立性审计不可判定"]
    if not isinstance(data, dict) or data.get("schema") != 1:
        return []
    checker = data.get("checker")
    if (
        not isinstance(checker, dict)
        or checker.get("implementation_reuse") is not False
    ):
        return []
    checker_artifact = str(checker.get("artifact", ""))
    if not checker_artifact:
        return []
    checker_record: dict | None = None
    for record in records:
        if record["stored_as"] == checker_artifact:
            checker_record = record
    if (
        checker_record is None
        or checker_record.get("method") != "copy"
        or not isinstance(checker_record.get("source_sha256"), str)
    ):
        return []
    checked_hashes: set[str] = set()
    for label in ("solver", "candidate"):
        item = data.get(label)
        if not isinstance(item, dict):
            continue
        artifact = str(item.get("artifact", ""))
        if not artifact:
            continue
        if isinstance(item.get("sha256"), str):
            checked_hashes.add(item["sha256"])
        target = root / artifact
        if target.is_file():
            checked_hashes.add(sha256(target))
        for record in records:
            if record["stored_as"] == artifact and isinstance(
                record.get("source_sha256"), str
            ):
                checked_hashes.add(record["source_sha256"])
    if checker_record["source_sha256"] in checked_hashes:
        return [
            "checker provenance violates independence: "
            f"{checker_artifact} 登记为 method=copy 且 source 与被检对象同源"
        ]
    return []


def audit_provenance(root: Path, baselines: Path | None = None) -> list[str]:
    """Aggregate provenance audit over one project.

    Four families of findings:
    (a) import-manifest integrity — every registered ``stored_as`` must still
        exist and hash to its latest ``stored_sha256``;
    (b) warm-start keys — the P0 policy layer plus a manifest layer: each
        declared hash must also appear in ``problem/import_manifest.json``;
    (c) unregistered cross-run copies — ``.py`` files under src/code/checks/
        scripts byte-identical to a registered baseline without a manifest
        record (skipped when no baselines directory can be resolved);
    (d) checker independence — a checker claiming independence whose manifest
        record is a ``copy`` sharing its source with the checked artifacts.

    Projects without a manifest and without violations yield an empty list.
    """
    root = root.resolve()
    errors, raw_manifest, records = _load_manifest(root)
    errors.extend(_manifest_integrity_errors(root, records))
    warm_errors, findings = _collect_warm_start(root)
    errors.extend(warm_errors)
    errors.extend(_warm_start_policy_errors(root, findings))
    registered = _registered_hashes(raw_manifest)
    for rel, dotted, value in findings:
        leaf = dotted.rsplit(".", 1)[-1]
        if (
            "sha256" in leaf
            and isinstance(value, str)
            and value not in registered
        ):
            errors.append(
                f"{rel}: {dotted} 的 warm-start 哈希未在 import_manifest 登记"
            )
    resolved = (
        Path(baselines).resolve()
        if baselines is not None
        else _default_baselines_dir(root)
    )
    if resolved is not None and (resolved.is_dir() or resolved.is_file()):
        index = _baseline_index(resolved)
        if index:
            errors.extend(
                _unregistered_copy_errors(
                    root, index, registered, PROJECT_CODE_DIRS
                )
            )
    errors.extend(_checker_independence_errors(root, records))
    return errors
