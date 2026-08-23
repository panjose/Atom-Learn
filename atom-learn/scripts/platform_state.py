#!/usr/bin/env python3
"""Versioned AtomLearn platform state, lazy user paths, and atomic namespace writes."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from platformdirs import user_data_dir

from core_paths import CORE_ROOT

MANIFEST_PATH = CORE_ROOT / "assets" / "core-manifest.yaml"
MANIFEST_SCHEMA_PATH = CORE_ROOT / "assets" / "schemas" / "core-manifest.schema.json"
DATA_DIR_ENV = "ATOMLEARN_DATA_DIR"


class PlatformStateError(RuntimeError):
    """A safe, user-correctable platform-state error."""


def _read_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PlatformStateError(f"Required state file not found: {path}")
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise PlatformStateError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlatformStateError(f"Expected a mapping in {path}")
    return value


def atomic_text(path: Path, content: str) -> None:
    import uuid

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".tmp-{uuid.uuid4().hex[:12]}")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_yaml(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100))


def load_core_manifest() -> dict[str, Any]:
    manifest = _read_mapping(MANIFEST_PATH)
    schema = _read_mapping(MANIFEST_SCHEMA_PATH)
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda item: list(item.path))
    if errors:
        details = "; ".join(error.message for error in errors[:5])
        raise PlatformStateError(f"Core manifest is invalid: {details}")
    return manifest


def core_version() -> str:
    return str(load_core_manifest()["core_version"])


def version_tuple(value: str) -> tuple[int, int, int]:
    main = value.split("-", 1)[0].split("+", 1)[0]
    pieces = main.split(".")
    if len(pieces) != 3 or any(not piece.isdigit() for piece in pieces):
        raise PlatformStateError(f"Invalid semantic version: {value!r}")
    return tuple(int(piece) for piece in pieces)  # type: ignore[return-value]


def resolve_user_data_root(override: str | Path | None = None, *, create: bool = False) -> Path:
    candidate: str | Path
    if override is not None:
        candidate = override
    elif os.environ.get(DATA_DIR_ENV):
        candidate = os.environ[DATA_DIR_ENV]
    else:
        candidate = user_data_dir("AtomLearn", "AtomLearn", roaming=False)
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        raise PlatformStateError(f"AtomLearn user data root must be absolute: {path}")
    resolved = path.resolve(strict=False)
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def validate_envelope(record: dict[str, Any], namespace: str, manifest: dict[str, Any] | None = None) -> None:
    manifest = manifest or load_core_manifest()
    compatibility = manifest.get("schemas", {}).get(namespace)
    if not isinstance(compatibility, dict):
        raise PlatformStateError(f"Core manifest does not declare namespace {namespace!r}")
    schema_version = record.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise PlatformStateError(f"{namespace} schema_version must be a positive integer")
    if schema_version not in compatibility.get("read", []):
        raise PlatformStateError(
            f"Core {manifest['core_version']} cannot read {namespace} schema {schema_version}"
        )
    revision = record.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise PlatformStateError(f"{namespace} revision must be a non-negative integer")
    minimum = record.get("min_reader_core_version")
    if minimum is not None:
        if not isinstance(minimum, str):
            raise PlatformStateError(f"{namespace} min_reader_core_version must be a string")
        if version_tuple(manifest["core_version"]) < version_tuple(minimum):
            raise PlatformStateError(
                f"Core {manifest['core_version']} is older than {namespace} minimum reader {minimum}"
            )


class FileLock(AbstractContextManager["FileLock"]):
    """Small cross-platform advisory lock that leaves an auditable lock file behind."""

    def __init__(self, path: Path, timeout: float = 10.0):
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
                    raise PlatformStateError(f"Timed out waiting for state lock: {self.path}") from exc
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


class NamespaceStore:
    """Revision-protected YAML state for one user-level namespace."""

    def __init__(self, root: Path, namespace: str, filename: str = "state.yaml"):
        self.root = root.resolve(strict=False)
        self.namespace = namespace
        self.path = self.root / filename
        self.lock_path = self.root / ".state.lock"

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> dict[str, Any]:
        record = _read_mapping(self.path)
        validate_envelope(record, self.namespace)
        return record

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        with FileLock(self.lock_path):
            if self.path.exists():
                raise PlatformStateError(f"State already exists: {self.path}")
            value = dict(record)
            value.setdefault("created_by_core_version", core_version())
            value.setdefault("last_written_by_core_version", core_version())
            value.setdefault("min_reader_core_version", core_version())
            value.setdefault("revision", 0)
            validate_envelope(value, self.namespace)
            atomic_yaml(self.path, value)
            return value

    def write(self, record: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        with FileLock(self.lock_path):
            current = self.read()
            if current["revision"] != expected_revision:
                raise PlatformStateError(
                    f"Stale {self.namespace} revision: expected {expected_revision}, current is {current['revision']}"
                )
            value = dict(record)
            value["revision"] = current["revision"] + 1
            value["created_by_core_version"] = current.get("created_by_core_version", core_version())
            value["last_written_by_core_version"] = core_version()
            value.setdefault("min_reader_core_version", current.get("min_reader_core_version", core_version()))
            validate_envelope(value, self.namespace)
            atomic_yaml(self.path, value)
            return value


def tree_fingerprint(root: Path) -> str:
    """Hash regular files without following links, for Core read-only regression checks."""

    root = root.resolve()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()
