"""Crash-safe local state storage with cooperative process locking."""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class StateError(RuntimeError):
    pass


class CorruptStateError(StateError):
    pass


class LockTimeout(StateError):
    pass


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorruptStateError(f"状态文件损坏: {path}: {exc}") from exc


def atomic_write_json(path: Path, value: Any) -> None:
    """Write fsync'd JSON then atomically replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def file_lock(
    target: Path, timeout: float = 10.0, stale_after: float = 3600.0
) -> Iterator[None]:
    """Cross-process cooperative lock based on exclusive lock-file creation."""
    lock = target.with_name(target.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "pid": os.getpid(), "created": time.time(),
                    "target": str(target),
                }))
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
                if age > stale_after:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                continue
            if time.monotonic() >= deadline:
                raise LockTimeout(f"等待状态锁超时: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def json_transaction(
    path: Path, default: Any, timeout: float = 10.0
) -> Iterator[Any]:
    with file_lock(path, timeout=timeout):
        value = read_json(path, default)
        yield value
        atomic_write_json(path, value)
