"""Install and resolve the stable manager-owned Codex bridge Skill."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from . import MANAGER_VERSION
from .common import (
    FileLock,
    ManagerError,
    atomic_bytes,
    atomic_yaml,
    canonical_json,
    is_reparse_or_symlink,
    manager_root,
    now_iso,
    read_mapping,
    require_schema,
    sha256_bytes,
)
from .manager import SimulatedInterruption, load_active, verify_installed
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


def _path_entry_exists(path: Path) -> bool:
    """Treat broken links and reparse points as occupied filesystem entries."""

    return path.exists() or is_reparse_or_symlink(path)


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
        "scripts/resolve.py": (BRIDGE_SOURCE / "scripts" / "resolve.py").read_bytes(),
    }


def _content_identity(files: dict[str, bytes]) -> str:
    return sha256_bytes(b"".join(name.encode("utf-8") + b"\0" + content for name, content in sorted(files.items())))


def _safe_tree_files(path: Path) -> dict[str, bytes]:
    """Read an exact regular-file tree without following links or reparse points."""

    if not path.is_dir() or is_reparse_or_symlink(path):
        raise ManagerError(f"Skill source must be a regular directory tree: {path}")
    files: dict[str, bytes] = {}
    directories_seen: set[str] = set()
    folded_entries: dict[str, str] = {}
    for parent, directories, filenames in os.walk(path, topdown=True, followlinks=False):
        parent_path = Path(parent)
        safe_directories = []
        for name in directories:
            candidate = parent_path / name
            if is_reparse_or_symlink(candidate):
                raise ManagerError(f"Skill source contains a linked directory: {candidate}")
            relative = candidate.relative_to(path).as_posix()
            folded = relative.casefold()
            if folded in folded_entries:
                raise ManagerError(f"Skill source contains case-colliding entries: {relative}")
            folded_entries[folded] = relative
            directories_seen.add(relative)
            safe_directories.append(name)
        directories[:] = safe_directories
        for name in filenames:
            candidate = parent_path / name
            if not candidate.is_file() or is_reparse_or_symlink(candidate):
                raise ManagerError(f"Skill source contains an unsafe file: {candidate}")
            relative = candidate.relative_to(path).as_posix()
            folded = relative.casefold()
            if folded in folded_entries:
                raise ManagerError(f"Skill source contains case-colliding entries: {relative}")
            folded_entries[folded] = relative
            files[relative] = candidate.read_bytes()
    implied_directories = {
        Path(*parts[:index]).as_posix()
        for relative in files
        for parts in [Path(relative).parts]
        for index in range(1, len(parts))
    }
    if directories_seen != implied_directories:
        raise ManagerError("Skill source contains an empty directory absent from the signed file inventory")
    return files


def install_bridge(root: Path, home: Path, *, repair: bool = False, confirmed: bool = False) -> dict[str, Any]:
    target = bridge_path(home)
    existing = _path_entry_exists(target)
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
    if existing and not repair:
        state = bridge_status(root, home)
        if state["ok"]:
            return {
                "ok": True,
                "installed": False,
                "idempotent": True,
                "bridge_path": str(target),
                "previous_bridge": None,
                "marker": existing_marker,
                "manager_root": str(root),
            }
        raise ManagerError("Owned Codex bridge is damaged or stale; use codex repair --confirmed")
    files = _source_files()
    identity = _content_identity(files)
    marker = {
        "kind": "atomlearn.codex-bridge",
        "schema_version": 2,
        "owner": "atomlearn-manager",
        "manager_version": MANAGER_VERSION,
        "bridge_protocol_version": BRIDGE_PROTOCOL_VERSION,
        "manager_root": str(root.resolve()),
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
        if previous is not None and not _path_entry_exists(target):
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
    if _path_entry_exists(target) and is_reparse_or_symlink(target):
        return {
            "ok": False,
            "installed": True,
            "owned": False,
            "bridge_path": str(target),
            "reason": "unsafe_linked_path",
        }
    marker = _marker(target)
    if marker is None:
        return {"ok": False, "installed": _path_entry_exists(target), "owned": False, "bridge_path": str(target)}
    files = _source_files()
    expected = _content_identity(files)
    try:
        tree = _safe_tree_files(target)
    except ManagerError:
        tree = {}
    marker_bytes = tree.pop(MARKER, None)
    actual = _content_identity(tree)
    exact_inventory = set(tree) == set(files) and marker_bytes is not None
    root_matches = marker.get("schema_version") == 2 and marker.get("manager_root") == str(root.resolve())
    content_valid = exact_inventory and root_matches and actual == expected == marker.get("content_sha256")
    return {
        "ok": content_valid,
        "installed": True,
        "owned": True,
        "bridge_path": str(target),
        "bridge_protocol_version": marker.get("bridge_protocol_version"),
        "content_valid": content_valid,
        "exact_inventory": exact_inventory,
        "manager_root_matches": root_matches,
        "manager_root": str(root),
    }


def _known_official_skill_trees(root: Path) -> dict[str, list[str]]:
    known: dict[str, list[str]] = {}
    manifests = root / "manifests"
    if not manifests.is_dir():
        return known
    try:
        trust = load_trust(root)
    except ManagerError:
        return known
    for path in sorted(manifests.glob("*.json")):
        try:
            manifest = read_mapping(path)
            validate_release_manifest(manifest, trust)
            version = manifest["version"]
            verify_installed(root, version, manifest)
            files = _safe_tree_files(root / "releases" / version / "atom-learn")
        except (KeyError, ManagerError, OSError):
            continue
        known.setdefault(_content_identity(files), []).append(version)
    return known


def source_copy_status(
    root: Path,
    home: Path,
    *,
    additional_known_trees: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    target = bridge_path(home)
    if not _path_entry_exists(target):
        return {
            "ok": True,
            "classification": "absent",
            "bridge_path": str(target),
            "migratable": False,
            "action": "install_bridge",
        }
    if is_reparse_or_symlink(target):
        return {
            "ok": False,
            "classification": "unsafe_linked_path",
            "bridge_path": str(target),
            "migratable": False,
            "action": "resolve_conflict_manually",
        }
    marker = _marker(target)
    if marker is not None:
        state = bridge_status(root, home)
        return {
            **state,
            "classification": "owned_bridge" if state["ok"] else "owned_bridge_needs_repair",
            "migratable": False,
            "action": "none" if state["ok"] else "repair_owned_bridge",
        }
    try:
        files = _safe_tree_files(target)
    except ManagerError as exc:
        return {
            "ok": False,
            "classification": "unsafe_source_copy",
            "bridge_path": str(target),
            "migratable": False,
            "action": "resolve_conflict_manually",
            "reason": str(exc),
        }
    fingerprint = _content_identity(files)
    known = _known_official_skill_trees(root)
    for identity, versions in (additional_known_trees or {}).items():
        known.setdefault(identity, []).extend(version for version in versions if version not in known.get(identity, []))
    versions = sorted(set(known.get(fingerprint, [])))
    if versions:
        return {
            "ok": True,
            "classification": "official_source_copy",
            "bridge_path": str(target),
            "migratable": True,
            "action": "backup_and_install_bridge",
            "source_sha256": fingerprint,
            "source_versions": versions,
            "file_count": len(files),
        }
    return {
        "ok": False,
        "classification": "unknown_or_modified_source_copy",
        "bridge_path": str(target),
        "migratable": False,
        "action": "resolve_conflict_manually",
        "source_sha256": fingerprint,
        "file_count": len(files),
    }


def _save_bridge_transaction(root: Path, transaction: dict[str, Any]) -> None:
    transaction["updated_at"] = now_iso()
    require_schema(transaction, "bridge-migration")
    atomic_yaml(root / "transactions" / f"{transaction['id']}.yaml", transaction)


def _maybe_bridge_interrupt(stage: str) -> None:
    if os.environ.get("ATOMLEARN_MANAGER_FAIL_AFTER") == stage:
        raise SimulatedInterruption(f"Simulated process interruption after {stage}")


def _recover_bridge_transaction(root: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    target = Path(transaction["target_path"])
    backup = Path(transaction["backup_path"])
    failed = Path(transaction["failed_bridge_path"])
    expected_source = transaction["source_sha256"]
    prior_error = transaction.get("error")

    target_is_source = False
    if target.is_dir() and not is_reparse_or_symlink(target):
        try:
            target_is_source = _content_identity(_safe_tree_files(target)) == expected_source
        except ManagerError:
            target_is_source = False
    if not target_is_source:
        if _path_entry_exists(target):
            if failed.exists():
                transaction["status"] = "needs_manual_recovery"
                transaction["error"] = "Both target and failed-bridge preservation paths exist"
                _save_bridge_transaction(root, transaction)
                raise ManagerError(transaction["error"])
            os.replace(target, failed)
        if not backup.is_dir() or is_reparse_or_symlink(backup):
            transaction["status"] = "needs_manual_recovery"
            transaction["error"] = "Source-copy backup is missing or unsafe"
            _save_bridge_transaction(root, transaction)
            raise ManagerError(transaction["error"])
        os.replace(backup, target)
    restored = _content_identity(_safe_tree_files(target))
    if restored != expected_source:
        transaction["status"] = "needs_manual_recovery"
        transaction["error"] = "Restored source-copy fingerprint does not match the migration plan"
        _save_bridge_transaction(root, transaction)
        raise ManagerError(transaction["error"])
    transaction["status"] = "recovered"
    transaction["stage"] = "recovered"
    transaction["error"] = prior_error
    _save_bridge_transaction(root, transaction)
    return {
        "ok": True,
        "recovered": True,
        "transaction": transaction["id"],
        "bridge_path": str(target),
        "backup_restored": True,
        "failed_bridge": str(failed) if failed.exists() else None,
    }


def migrate_source_copy(root: Path, home: Path, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ManagerError("Source-copy bridge migration requires --confirmed after reviewing its plan")
    with FileLock(root / ".bridge-migration.lock"):
        state = source_copy_status(root, home)
        if state["classification"] == "owned_bridge":
            return {"ok": True, "migrated": False, "idempotent": True, "bridge": state}
        if state["classification"] == "owned_bridge_needs_repair":
            repaired = install_bridge(root, home, repair=True, confirmed=True)
            return {"ok": True, "migrated": False, "repaired": True, "bridge": repaired}
        if state["classification"] == "absent":
            installed = install_bridge(root, home)
            return {"ok": True, "migrated": False, "installed": True, "bridge": installed}
        if not state.get("migratable"):
            raise ManagerError(
                "Codex Skill is not an exact known official source copy and will not be replaced",
                code="codex_bridge_ownership_conflict",
                details=state,
            )
        target = bridge_path(home)
        transaction_id = "bmtxn-" + uuid.uuid4().hex
        suffix = transaction_id.removeprefix("bmtxn-")[:12]
        timestamp = now_iso().replace("+00:00", "Z").replace("-", "").replace(":", "")
        backup = target.with_name(f"atom-learn.source-backup-{timestamp}-{suffix}")
        failed = target.with_name(f"atom-learn.failed-bridge-{suffix}")
        if backup.exists() or failed.exists():
            raise ManagerError("Bridge migration preservation path already exists")
        transaction = {
            "kind": "atomlearn.bridge-migration",
            "schema_version": 1,
            "id": transaction_id,
            "status": "in_progress",
            "stage": "planned",
            "codex_home": str(home.resolve()),
            "target_path": str(target.resolve(strict=False)),
            "backup_path": str(backup.resolve(strict=False)),
            "failed_bridge_path": str(failed.resolve(strict=False)),
            "source_sha256": state["source_sha256"],
            "source_versions": state["source_versions"],
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "error": None,
        }
        _save_bridge_transaction(root, transaction)
        try:
            os.replace(target, backup)
            transaction["stage"] = "backed_up"
            _save_bridge_transaction(root, transaction)
            _maybe_bridge_interrupt("bridge_backed_up")
            installed = install_bridge(root, home)
            transaction["stage"] = "bridge_installed"
            _save_bridge_transaction(root, transaction)
            _maybe_bridge_interrupt("bridge_installed")
            verified = bridge_status(root, home)
            if not verified["ok"]:
                raise ManagerError("Installed Manager bridge failed exact content verification")
            transaction["stage"] = "verified"
            _save_bridge_transaction(root, transaction)
            transaction["status"] = "committed"
            transaction["stage"] = "committed"
            _save_bridge_transaction(root, transaction)
            return {
                "ok": True,
                "migrated": True,
                "transaction": transaction_id,
                "backup": str(backup),
                "bridge": installed,
            }
        except SimulatedInterruption:
            raise
        except Exception as exc:
            transaction["status"] = "failed"
            transaction["error"] = str(exc)
            _save_bridge_transaction(root, transaction)
            try:
                _recover_bridge_transaction(root, transaction)
            except ManagerError:
                pass
            raise ManagerError(f"Bridge migration failed and the source copy was preserved: {exc}") from exc


def recover_bridge_migration(root: Path) -> dict[str, Any]:
    with FileLock(root / ".bridge-migration.lock"):
        candidates = []
        for path in (root / "transactions").glob("bmtxn-*.yaml"):
            transaction = read_mapping(path)
            require_schema(transaction, "bridge-migration")
            if transaction["status"] in {"in_progress", "failed", "needs_manual_recovery"}:
                candidates.append(transaction)
        if not candidates:
            return {"ok": True, "recovered": False, "reason": "no_unfinished_bridge_migration"}
        transaction = sorted(candidates, key=lambda item: item["updated_at"])[-1]
        return _recover_bridge_transaction(root, transaction)


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
