"""Side-by-side update transactions, health checks, recovery, and paired rollback."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import MANAGER_VERSION
from .common import (
    FileLock,
    ManagerError,
    atomic_bytes,
    atomic_text,
    atomic_yaml,
    canonical_json,
    is_reparse_or_symlink,
    manager_root,
    now_iso,
    read_mapping,
    require_schema,
    sha256_bytes,
    sha256_file,
    version_tuple,
)
from .manifest import load_trust, validate_release_manifest
from .statecopy import apply_migrated_files, plan_state, restore_files, snapshot_and_migrate, state_copy_size
from .verify import content_tree_hash, safe_extract, verify_release


class SimulatedInterruption(ManagerError):
    """Test-only process-boundary simulation that deliberately skips auto-recovery."""


INTERRUPTIBLE_STAGES = (
    "planned",
    "downloaded",
    "verified",
    "state_copied",
    "installed",
    "health_checked",
    "state_applied",
    "activated",
)


def _manifest_hash(manifest: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(manifest))


def load_active(root: Path) -> dict[str, Any] | None:
    path = root / "active.yaml"
    if not path.is_file():
        return None
    active = read_mapping(path)
    require_schema(active, "active")
    return active


def save_transaction(root: Path, transaction: dict[str, Any]) -> None:
    transaction["updated_at"] = now_iso()
    require_schema(transaction, "transaction")
    atomic_yaml(root / "transactions" / f"{transaction['id']}.yaml", transaction)


def load_transaction(root: Path, transaction_id: str) -> dict[str, Any]:
    transaction = read_mapping(root / "transactions" / f"{transaction_id}.yaml")
    require_schema(transaction, "transaction")
    return transaction


def _manifest_from_source(source: str, *, offline: bool = False) -> tuple[dict[str, Any] | None, str]:
    if source.startswith(("https://", "http://")):
        if offline:
            return None, "offline"
        if not source.startswith("https://"):
            raise ManagerError("Release manifests may be fetched only over HTTPS")
        try:
            request = Request(source, headers={"Accept": "application/json", "User-Agent": f"AtomLearnManager/{MANAGER_VERSION}"})
            with urlopen(request, timeout=15) as response:  # nosec B310: HTTPS is checked above
                if not response.geturl().startswith("https://"):
                    raise ManagerError("Release manifest redirect must remain on HTTPS")
                content = response.read(2 * 1024 * 1024 + 1)
        except HTTPError as exc:
            host = urlparse(source).hostname or "unknown"
            raise ManagerError(
                f"Release manifest request failed with HTTP {exc.code}; the release may be private, unavailable, or inaccessible",
                code="release_manifest_http_error",
                retryable=500 <= int(exc.code) < 600,
                details={"host": host, "status": int(exc.code)},
            ) from exc
        except (OSError, URLError) as exc:
            host = urlparse(source).hostname or "unknown"
            raise ManagerError(
                "Release manifest is temporarily unavailable; the active Core is unchanged",
                code="release_manifest_unavailable",
                retryable=True,
                details={"host": host},
            ) from exc
        if len(content) > 2 * 1024 * 1024:
            raise ManagerError("Remote release manifest exceeds the size limit")
        try:
            value = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManagerError("Remote release manifest is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise ManagerError("Remote release manifest must be an object")
        return value, source
    path = Path(source)
    return read_mapping(path), str(path.resolve())


def check_release(root: Path, manifest_source: str, channel: str, *, offline: bool = False) -> dict[str, Any]:
    trust = load_trust(root)
    manifest, source = _manifest_from_source(manifest_source, offline=offline)
    active = load_active(root)
    if manifest is None:
        return {
            "ok": True,
            "offline": True,
            "current_version": active["current_version"] if active else None,
            "available": False,
            "reason": "offline_requested",
        }
    validate_release_manifest(manifest, trust, requested_channel=channel)
    current = active["current_version"] if active else None
    available = current is None or version_tuple(manifest["version"]) > version_tuple(current)
    return {
        "ok": True,
        "offline": False,
        "current_version": current,
        "available_version": manifest["version"],
        "available": available,
        "channel": manifest["channel"],
        "manifest_hash": _manifest_hash(manifest),
        "source": source,
    }


def _assert_isolated(root: Path, data_root: Path | None, workspaces: list[Path]) -> None:
    roots = [path.resolve() for path in ([data_root] if data_root else []) + workspaces]
    for path in roots:
        if path == root or root in path.parents or path in root.parents:
            raise ManagerError("Manager install root must be isolated from user data and course workspaces")


def plan_update(
    root: Path,
    version: str,
    manifest_source: str,
    artifact_path: Path | None,
    data_root: Path | None,
    workspaces: list[Path],
    channel: str,
) -> dict[str, Any]:
    trust = load_trust(root)
    manifest, _ = _manifest_from_source(manifest_source)
    if manifest is None:  # defensive: only explicit check mode may operate without a manifest
        raise ManagerError(
            "A verified release manifest is required to plan an update",
            code="release_manifest_required",
        )
    validate_release_manifest(manifest, trust, requested_channel=channel)
    if manifest["version"] != version:
        raise ManagerError("Requested update version does not match the signed manifest")
    active = load_active(root)
    if active and version_tuple(version) <= version_tuple(active["current_version"]):
        raise ManagerError("Update only accepts a newer version; use paired rollback for downgrades")
    _assert_isolated(root, data_root, workspaces)
    state = plan_state(data_root, workspaces, manifest)
    verified = False
    archive = None
    if artifact_path is not None:
        archive = verify_release(manifest, artifact_path)
        verified = True
    fake_free = os.environ.get("ATOMLEARN_MANAGER_FAKE_FREE_BYTES")
    free = int(fake_free) if fake_free is not None else shutil.disk_usage(root).free
    state_bytes = state_copy_size(data_root, workspaces)
    required = int(manifest["artifact"]["size"]) * 2 + state_bytes * 2 + 16 * 1024 * 1024
    return {
        "ok": True,
        "current_version": active["current_version"] if active else None,
        "target_version": version,
        "channel": channel,
        "artifact_verified": verified,
        "artifact_file_count": archive["file_count"] if archive else None,
        "disk": {"free_bytes": free, "required_bytes": required, "sufficient": free >= required},
        "state": state,
        "activation": "side_by_side_atomic_pointer",
        "old_release_retained": bool(active),
    }


def _download_artifact(manifest: dict[str, Any], source: Path | None, destination: Path) -> None:
    expected_size = int(manifest["artifact"]["size"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source is not None:
        if not source.is_file() or is_reparse_or_symlink(source):
            raise ManagerError(f"Artifact source must be a regular non-link file: {source}")
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    else:
        url = manifest["source"]["artifact_url"]
        request = Request(url, headers={"User-Agent": f"AtomLearnManager/{MANAGER_VERSION}"})
        try:
            with urlopen(request, timeout=30) as response, destination.open("xb") as writer:  # nosec B310
                if not response.geturl().startswith("https://"):
                    raise ManagerError("Artifact redirect must remain on HTTPS")
                remaining = expected_size
                while remaining:
                    block = response.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    writer.write(block)
                    remaining -= len(block)
                if response.read(1):
                    raise ManagerError("Downloaded artifact is larger than its signed size")
                writer.flush()
                os.fsync(writer.fileno())
        except (OSError, URLError) as exc:
            raise ManagerError(f"Artifact download failed; active Core is unchanged: {exc}") from exc


def _maybe_interrupt(stage: str) -> None:
    if os.environ.get("ATOMLEARN_MANAGER_FAIL_AFTER") == stage:
        raise SimulatedInterruption(f"Simulated process interruption after {stage}")


def _installed_files(release: Path) -> list[tuple[str, bytes]]:
    result = []
    for path in sorted(release.rglob("*"), key=lambda item: item.as_posix()):
        if is_reparse_or_symlink(path):
            raise ManagerError(f"Installed release contains a link or reparse point: {path}")
        if path.is_file():
            result.append((path.relative_to(release).as_posix(), path.read_bytes()))
    return result


def verify_installed(root: Path, version: str, manifest: dict[str, Any]) -> None:
    release = root / "releases" / version
    if not release.is_dir() or is_reparse_or_symlink(release):
        raise ManagerError(f"Installed release directory is missing or unsafe: {release}")
    if content_tree_hash(_installed_files(release)) != manifest["core_content_sha256"]:
        raise ManagerError(f"Installed release content hash is invalid: {version}")


def _smoke(root: Path, version: str, transaction_root: Path | None = None, actual_paths: tuple[Path | None, list[Path]] | None = None) -> None:
    core = root / "releases" / version / "atom-learn" / "scripts" / "atomlearn.py"
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    commands = [[sys.executable, str(core), "version"], [sys.executable, str(core), "--help"]]
    if transaction_root is not None:
        data_copy = transaction_root / "state-copy" / "data"
        workspace_parent = transaction_root / "state-copy" / "workspaces"
        mirrors = sorted([path for path in workspace_parent.iterdir() if path.is_dir()]) if workspace_parent.is_dir() else []
        commands.append(
            [sys.executable, str(core), "migrate", "validate", "--data-dir", str(data_copy.resolve()), *sum((["--workspace", str(path.resolve())] for path in mirrors), [])]
        )
        for mirror in mirrors:
            commands.append([sys.executable, str(core), "validate", str(mirror.resolve())])
            commands.append([sys.executable, str(core), "status", str(mirror.resolve()), "--json"])
        environment["ATOMLEARN_DATA_DIR"] = str(data_copy.resolve())
    elif actual_paths is not None:
        data_root, workspaces = actual_paths
        command = [sys.executable, str(core), "migrate", "validate"]
        if data_root is not None:
            command.extend(["--data-dir", str(data_root)])
        for workspace in workspaces:
            command.extend(["--workspace", str(workspace)])
        commands.append(command)
    for command in commands:
        result = subprocess.run(command, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
        if result.returncode != 0:
            raise ManagerError(f"Core {version} health check failed: {' '.join(command[2:])}: {result.stderr.strip()}")
        if command[-1] == "version":
            try:
                reported = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise ManagerError("Core version health check did not return JSON") from exc
            if reported.get("core_version") != version:
                raise ManagerError("Core health check reported a mismatched version")


def _mark_read_only(release: Path) -> None:
    for path in release.rglob("*"):
        if path.is_file():
            path.chmod(stat.S_IREAD)


def _recover_transaction(root: Path, transaction: dict[str, Any]) -> dict[str, Any]:
    try:
        restore_files(transaction["state_files"], lambda: save_transaction(root, transaction))
        if transaction["pointer_switched"]:
            active = load_active(root)
            if active and active["transaction_id"] != transaction["id"]:
                raise ManagerError("Active pointer moved after the failed transaction; refusing automatic recovery")
            previous = transaction["previous_active"]
            active_path = root / "active.yaml"
            if previous is None:
                if active_path.exists():
                    archived = root / "transactions" / f"abandoned-active-{transaction['id']}.yaml"
                    if archived.exists():
                        raise ManagerError("Recovery archive already exists while active pointer still needs recovery")
                    os.replace(active_path, archived)
            else:
                atomic_yaml(active_path, previous)
            transaction["pointer_switched"] = False
        transaction["status"] = "recovered"
        transaction["stage"] = "recovered"
        save_transaction(root, transaction)
        return transaction
    except ManagerError as exc:
        transaction["status"] = "needs_manual_recovery"
        transaction["error"] = str(exc)
        save_transaction(root, transaction)
        raise


def apply_update(
    root: Path,
    version: str,
    manifest_source: str,
    artifact_source: Path | None,
    data_root: Path | None,
    workspaces: list[Path],
    channel: str,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise ManagerError("Update apply requires --confirmed after reviewing update plan")
    _assert_isolated(root, data_root, workspaces)
    with FileLock(root / ".manager.lock"):
        trust = load_trust(root)
        manifest, manifest_origin = _manifest_from_source(manifest_source)
        if manifest is None:  # defensive: apply never has an offline mode
            raise ManagerError(
                "A verified release manifest is required to apply an update",
                code="release_manifest_required",
            )
        validate_release_manifest(manifest, trust, requested_channel=channel)
        if manifest["version"] != version:
            raise ManagerError("Requested update version does not match the signed manifest")
        previous_active = load_active(root)
        if previous_active and version_tuple(version) <= version_tuple(previous_active["current_version"]):
            raise ManagerError("Update only accepts a newer Core; use rollback for a paired downgrade")
        plan = plan_update(root, version, manifest_source, artifact_source, data_root, workspaces, channel)
        if not plan["disk"]["sufficient"]:
            raise ManagerError("Insufficient disk space for side-by-side release and state copies")
        transaction_id = "txn-" + uuid.uuid4().hex
        transaction_root = root / "staging" / transaction_id.removeprefix("txn-")[:12]
        transaction_root.mkdir(parents=True, exist_ok=False)
        staged_artifact = transaction_root / manifest["artifact"]["filename"]
        transaction = {
            "kind": "atomlearn.manager-transaction",
            "schema_version": 1,
            "id": transaction_id,
            "target_version": version,
            "previous_version": previous_active["current_version"] if previous_active else None,
            "previous_active": previous_active,
            "status": "in_progress",
            "stage": "planned",
            "manifest_hash": _manifest_hash(manifest),
            "artifact_hash": manifest["artifact"]["sha256"],
            "release_manifest_path": manifest_origin,
            "artifact_path": str(staged_artifact),
            "data_root": str(data_root) if data_root else None,
            "workspaces": [str(path) for path in workspaces],
            "state_files": [],
            "pointer_switched": False,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "error": None,
        }
        save_transaction(root, transaction)
        try:
            _maybe_interrupt("planned")
            _download_artifact(manifest, artifact_source, staged_artifact)
            transaction["stage"] = "downloaded"
            save_transaction(root, transaction)
            _maybe_interrupt("downloaded")
            verify_release(manifest, staged_artifact)
            transaction["stage"] = "verified"
            save_transaction(root, transaction)
            _maybe_interrupt("verified")
            transaction["state_files"] = snapshot_and_migrate(transaction_root, data_root, workspaces, manifest)
            transaction["stage"] = "state_copied"
            save_transaction(root, transaction)
            _maybe_interrupt("state_copied")
            release = root / "releases" / version
            manifest_copy = root / "manifests" / f"{version}.json"
            if release.exists():
                if manifest_copy.exists() and _manifest_hash(read_mapping(manifest_copy)) != _manifest_hash(manifest):
                    raise ManagerError("Existing side-by-side release has a different signed manifest")
                verify_installed(root, version, manifest)
                if not manifest_copy.exists():
                    atomic_text(manifest_copy, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            else:
                extracted = safe_extract(staged_artifact, transaction_root / "unpacked", version)
                os.replace(extracted, release)
                _mark_read_only(release)
                if manifest_copy.exists():
                    raise ManagerError(f"Release manifest exists without its release directory: {manifest_copy}")
                atomic_text(manifest_copy, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            transaction["stage"] = "installed"
            save_transaction(root, transaction)
            _maybe_interrupt("installed")
            verify_installed(root, version, manifest)
            _smoke(root, version, transaction_root)
            transaction["stage"] = "health_checked"
            save_transaction(root, transaction)
            _maybe_interrupt("health_checked")
            apply_migrated_files(transaction["state_files"], lambda: save_transaction(root, transaction))
            transaction["stage"] = "state_applied"
            save_transaction(root, transaction)
            _maybe_interrupt("state_applied")
            active = {
                "kind": "atomlearn.manager-active",
                "schema_version": 1,
                "current_version": version,
                "previous_version": previous_active["current_version"] if previous_active else None,
                "manifest_hash": _manifest_hash(manifest),
                "transaction_id": transaction_id,
            }
            require_schema(active, "active")
            atomic_yaml(root / "active.yaml", active)
            transaction["pointer_switched"] = True
            transaction["stage"] = "activated"
            save_transaction(root, transaction)
            _maybe_interrupt("activated")
            transaction["status"] = "committed"
            transaction["stage"] = "committed"
            save_transaction(root, transaction)
            return {"ok": True, "active": active, "transaction": transaction_id, "old_release_retained": previous_active is not None}
        except SimulatedInterruption:
            raise
        except Exception as exc:
            transaction["status"] = "failed"
            transaction["error"] = str(exc)
            save_transaction(root, transaction)
            try:
                _recover_transaction(root, transaction)
            except ManagerError:
                pass
            raise ManagerError(f"Update failed; previous Core remains active: {exc}") from exc


def recover_latest(root: Path) -> dict[str, Any]:
    with FileLock(root / ".manager.lock"):
        candidates = []
        for path in (root / "transactions").glob("txn-*.yaml"):
            transaction = read_mapping(path)
            require_schema(transaction, "transaction")
            if transaction["status"] in {"in_progress", "failed", "needs_manual_recovery"}:
                candidates.append(transaction)
        if not candidates:
            return {"ok": True, "recovered": False, "reason": "no_unfinished_transaction"}
        transaction = sorted(candidates, key=lambda item: item["updated_at"])[-1]
        recovered = _recover_transaction(root, transaction)
        return {"ok": True, "recovered": True, "transaction": recovered["id"], "active": load_active(root)}


def rollback(root: Path, version: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ManagerError("Rollback requires --confirmed")
    with FileLock(root / ".manager.lock"):
        active = load_active(root)
        if not active or active.get("previous_version") != version:
            raise ManagerError("Rollback is limited to the paired previous version")
        current_transaction = load_transaction(root, active["transaction_id"])
        target_manifest = read_mapping(root / "manifests" / f"{version}.json")
        validate_release_manifest(target_manifest, load_trust(root))
        verify_installed(root, version, target_manifest)
        transaction_id = "txn-" + uuid.uuid4().hex
        reverse_files = [
            {
                **item,
                "backup": item["migrated"],
                "migrated": item["backup"],
                "original_hash": item["migrated_hash"],
                "migrated_hash": item["original_hash"],
                "applied": False,
            }
            for item in current_transaction["state_files"]
        ]
        transaction = {
            "kind": "atomlearn.manager-transaction",
            "schema_version": 1,
            "id": transaction_id,
            "target_version": version,
            "previous_version": active["current_version"],
            "previous_active": active,
            "status": "in_progress",
            "stage": "planned",
            "manifest_hash": _manifest_hash(target_manifest),
            "artifact_hash": target_manifest["artifact"]["sha256"],
            "release_manifest_path": str(root / "manifests" / f"{version}.json"),
            "artifact_path": f"installed:{version}",
            "data_root": current_transaction["data_root"],
            "workspaces": current_transaction["workspaces"],
            "state_files": reverse_files,
            "pointer_switched": False,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "error": None,
        }
        save_transaction(root, transaction)
        try:
            apply_migrated_files(reverse_files, lambda: save_transaction(root, transaction))
            transaction["stage"] = "state_applied"
            save_transaction(root, transaction)
            data = Path(transaction["data_root"]) if transaction["data_root"] else None
            workspaces = [Path(path) for path in transaction["workspaces"]]
            _smoke(root, version, actual_paths=(data, workspaces))
            rolled = {
                "kind": "atomlearn.manager-active",
                "schema_version": 1,
                "current_version": version,
                "previous_version": active["current_version"],
                "manifest_hash": _manifest_hash(target_manifest),
                "transaction_id": transaction_id,
            }
            atomic_yaml(root / "active.yaml", rolled)
            transaction["pointer_switched"] = True
            transaction["status"] = "rolled_back"
            transaction["stage"] = "rolled_back"
            save_transaction(root, transaction)
            return {"ok": True, "active": rolled, "transaction": transaction_id}
        except Exception as exc:
            transaction["status"] = "failed"
            transaction["error"] = str(exc)
            save_transaction(root, transaction)
            _recover_transaction(root, transaction)
            raise ManagerError(f"Rollback failed; current Core remains active: {exc}") from exc


def status(root: Path) -> dict[str, Any]:
    active = load_active(root)
    unfinished = []
    transaction_dir = root / "transactions"
    if transaction_dir.is_dir():
        for path in transaction_dir.glob("txn-*.yaml"):
            value = read_mapping(path)
            require_schema(value, "transaction")
            if value["status"] in {"in_progress", "failed", "needs_manual_recovery"}:
                unfinished.append({"id": value["id"], "status": value["status"], "stage": value["stage"]})
    active_valid = False
    if active:
        manifest = read_mapping(root / "manifests" / f"{active['current_version']}.json")
        validate_release_manifest(manifest, load_trust(root))
        verify_installed(root, active["current_version"], manifest)
        active_valid = active["manifest_hash"] == _manifest_hash(manifest)
    return {
        "ok": active_valid if active else True,
        "manager_version": MANAGER_VERSION,
        "active": active,
        "active_valid": active_valid,
        "installed_versions": sorted(path.name for path in (root / "releases").iterdir() if path.is_dir()) if (root / "releases").is_dir() else [],
        "unfinished_transactions": unfinished,
        "recovery_required": bool(unfinished),
    }
