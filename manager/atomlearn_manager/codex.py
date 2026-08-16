"""Install and resolve the stable manager-owned Codex bridge Skill."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from . import MANAGER_VERSION
from .common import ManagerError, atomic_bytes, canonical_json, is_reparse_or_symlink, manager_root, read_mapping, sha256_bytes
from .manager import load_active, verify_installed
from .manifest import load_trust, validate_release_manifest


BRIDGE_PROTOCOL_VERSION = 1
PACKAGE_ROOT = Path(__file__).resolve().parent
BRIDGE_SOURCE = PACKAGE_ROOT / "bridge"
MARKER = ".atomlearn-bridge.json"


def codex_home(override: str | Path | None) -> Path:
    if override is not None:
        candidate = Path(override)
    elif os.environ.get("CODEX_HOME"):
        candidate = Path(os.environ["CODEX_HOME"])
    else:
        candidate = Path.home() / ".codex"
    if not candidate.is_absolute():
        raise ManagerError(f"Codex home must be absolute: {candidate}")
    return candidate.resolve(strict=False)


def bridge_path(home: Path) -> Path:
    return home / "skills" / "atom-learn"


def _marker(path: Path) -> dict[str, Any] | None:
    marker = path / MARKER
    if not marker.is_file() or is_reparse_or_symlink(marker):
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(value, dict) or value.get("kind") != "atomlearn.codex-bridge" or value.get("owner") != "atomlearn-manager":
        return None
    return value


def _source_files() -> dict[str, bytes]:
    return {
        "SKILL.md": (BRIDGE_SOURCE / "SKILL.md").read_bytes(),
        "agents/openai.yaml": (BRIDGE_SOURCE / "agents" / "openai.yaml").read_bytes(),
    }


def install_bridge(root: Path, home: Path, *, repair: bool = False, confirmed: bool = False) -> dict[str, Any]:
    target = bridge_path(home)
    existing = target.exists()
    if existing and is_reparse_or_symlink(target):
        raise ManagerError(
            f"Refusing to replace an unsafe linked Codex Skill path: {target}",
            code="codex_bridge_ownership_conflict",
        )
    existing_marker = _marker(target) if existing else None
    if existing and existing_marker is None:
        raise ManagerError(
            f"Refusing to overwrite a Codex Skill not owned by AtomLearn Manager: {target}",
            code="codex_bridge_ownership_conflict",
        )
    if repair and not confirmed:
        raise ManagerError("Codex bridge repair requires --confirmed")
    files = _source_files()
    identity = sha256_bytes(b"".join(name.encode("utf-8") + b"\0" + content for name, content in sorted(files.items())))
    marker = {
        "kind": "atomlearn.codex-bridge",
        "schema_version": 1,
        "owner": "atomlearn-manager",
        "manager_version": MANAGER_VERSION,
        "bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
        "content_sha256": identity,
    }
    staging = target.with_name(f".atom-learn.bridge-{uuid.uuid4().hex[:12]}")
    staging.mkdir(parents=True, exist_ok=False)
    for relative, content in files.items():
        atomic_bytes(staging / Path(relative), content)
    atomic_bytes(staging / MARKER, canonical_json(marker))
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = None
    if existing:
        previous = target.with_name(f"atom-learn.previous-{uuid.uuid4().hex[:12]}")
        os.replace(target, previous)
    try:
        os.replace(staging, target)
    except OSError:
        if previous is not None and not target.exists():
            os.replace(previous, target)
        raise
    return {
        "ok": True,
        "installed": True,
        "bridge_path": str(target),
        "previous_bridge": str(previous) if previous else None,
        "marker": marker,
        "manager_root": str(root),
    }


def bridge_status(root: Path, home: Path) -> dict[str, Any]:
    target = bridge_path(home)
    if target.exists() and is_reparse_or_symlink(target):
        return {
            "ok": False,
            "installed": True,
            "owned": False,
            "bridge_path": str(target),
            "reason": "unsafe_linked_path",
        }
    marker = _marker(target)
    if marker is None:
        return {"ok": False, "installed": target.exists(), "owned": False, "bridge_path": str(target)}
    files = _source_files()
    expected = sha256_bytes(b"".join(name.encode("utf-8") + b"\0" + content for name, content in sorted(files.items())))
    actual_files = {
        relative: candidate.read_bytes()
        for relative in files
        for candidate in [target / Path(relative)]
        if candidate.is_file() and not is_reparse_or_symlink(candidate)
    }
    actual = sha256_bytes(b"".join(name.encode("utf-8") + b"\0" + content for name, content in sorted(actual_files.items())))
    return {
        "ok": actual == expected == marker.get("content_sha256"),
        "installed": True,
        "owned": True,
        "bridge_path": str(target),
        "bridge_protocol_version": marker.get("bridge_protocol_version"),
        "content_valid": actual == expected == marker.get("content_sha256"),
        "manager_root": str(root),
    }


def resolve_core_skill(root: Path) -> dict[str, Any]:
    active = load_active(root)
    if active is None:
        raise ManagerError("No active signed Core is available for the Codex bridge")
    version = active["current_version"]
    manifest = read_mapping(root / "manifests" / f"{version}.json")
    validate_release_manifest(manifest, load_trust(root))
    if manifest.get("manifest_version") != 2:
        raise ManagerError("Active Core predates the signed bridge protocol; install a manifest v2 release")
    protocol = manifest["skill_protocol"]
    if active.get("skill_protocol_version") != protocol["version"]:
        raise ManagerError("Active pointer Skill protocol does not match the signed release")
    if not protocol["bridge_min"] <= BRIDGE_PROTOCOL_VERSION <= protocol["bridge_max"]:
        raise ManagerError("Active Core Skill protocol is incompatible with this bridge")
    verify_installed(root, version, manifest)
    skill = root / "releases" / version / Path(protocol["entrypoint"])
    if not skill.is_file() or is_reparse_or_symlink(skill) or sha256_bytes(skill.read_bytes()) != protocol["entrypoint_sha256"]:
        raise ManagerError("Active signed Core Skill entry point is missing or has changed")
    return {
        "ok": True,
        "core_version": version,
        "skill_protocol_version": protocol["version"],
        "skill_path": str(skill.resolve()),
        "skill_sha256": protocol["entrypoint_sha256"],
        "manifest_hash": active["manifest_hash"],
    }
