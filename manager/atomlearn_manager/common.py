"""Small dependency-light primitives kept independent from AtomLearn course code."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from platformdirs import user_data_dir


SCHEMA_ROOT = Path(__file__).resolve().parent / "schemas"
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?$")


class ManagerError(RuntimeError):
    """A safe release-manager error that must leave the active Core usable."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def manager_root(override: str | Path | None = None, *, create: bool = False) -> Path:
    candidate = Path(override) if override is not None else Path(user_data_dir("AtomLearnManager", "AtomLearn"))
    if not candidate.is_absolute():
        raise ManagerError(f"Manager root must be absolute: {candidate}")
    result = candidate.resolve(strict=False)
    if create:
        for child in [result, result / "releases", result / "staging", result / "transactions", result / "manifests"]:
            child.mkdir(parents=True, exist_ok=True)
    return result


def read_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ManagerError(f"Required file not found: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManagerError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManagerError(f"Expected a mapping in {path}")
    return value


def schema_errors(value: dict[str, Any], name: str) -> list[str]:
    schema = json.loads((SCHEMA_ROOT / f"{name}.schema.json").read_text(encoding="utf-8"))
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    ]


def require_schema(value: dict[str, Any], name: str) -> None:
    errors = schema_errors(value, name)
    if errors:
        raise ManagerError(f"{name} validation failed:\n- " + "\n- ".join(errors))


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with temporary.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_yaml(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100))


def sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def version_tuple(value: str) -> tuple[int, int, int, int, tuple[tuple[int, int | str], ...]]:
    match = SEMVER.fullmatch(value)
    if not match:
        raise ManagerError(f"Invalid semantic version: {value!r}")
    prerelease = match.group(4)
    identifiers = tuple(
        (0, int(piece)) if piece.isdigit() else (1, piece)
        for piece in (prerelease.split(".") if prerelease is not None else [])
    )
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), 1 if prerelease is None else 0, identifiers


def is_reparse_or_symlink(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat().st_file_attributes  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False
    return bool(attributes & 0x400)


class FileLock(AbstractContextManager["FileLock"]):
    def __init__(self, path: Path, timeout: float = 15.0):
        self.path = path.resolve(strict=False)
        self.timeout = timeout
        self.handle: Any = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise ManagerError(f"Timed out waiting for manager lock: {self.path}") from exc
                time.sleep(0.05)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None
