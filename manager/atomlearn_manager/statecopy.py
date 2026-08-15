"""Plan and migrate only state copies; never execute code from a release artifact."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from .common import ManagerError, atomic_bytes, is_reparse_or_symlink, read_mapping, sha256_bytes, version_tuple


WORKSPACE_TARGETS = {
    "workspace_core": ".atomlearn/course.yaml",
    "workspace_adaptation": ".atomlearn/adaptation/state.yaml",
    "workspace_evolution": ".atomlearn/evolution/state.yaml",
    "workspace_intake": ".atomlearn/intake.yaml",
    "workspace_rag": ".atomlearn/rag/state.yaml",
    "workspace_research": ".atomlearn/research/state.yaml",
    "workspace_exam": ".atomlearn/exam/state.yaml",
    "workspace_lineage": ".atomlearn/lineage/state.yaml",
    "workspace_profile_binding": ".atomlearn/profile-binding.yaml",
}
USER_TARGETS = {
    "user_profile": ("profiles", "state.yaml"),
    "user_strategy": ("strategies", "state.yaml"),
}
Migration = Callable[[dict[str, Any]], dict[str, Any]]
MIGRATIONS: dict[tuple[str, int], Migration] = {}


@dataclass(frozen=True)
class Target:
    namespace: str
    path: Path
    scope: str
    relative: Path
    workspace_ref: str | None = None


def register_migration(namespace: str, from_version: int, function: Migration) -> None:
    key = (namespace, from_version)
    if key in MIGRATIONS:
        raise ManagerError(f"Duplicate manager migration: {namespace} {from_version}->{from_version + 1}")
    MIGRATIONS[key] = function


def catalog(data_root: Path | None, workspaces: list[Path]) -> list[Target]:
    result: list[Target] = []
    if data_root and data_root.is_dir():
        if is_reparse_or_symlink(data_root):
            raise ManagerError(f"User data root cannot be a link or reparse point: {data_root}")
        for namespace, (folder, filename) in USER_TARGETS.items():
            parent = data_root / folder
            if not parent.is_dir():
                continue
            if is_reparse_or_symlink(parent):
                raise ManagerError(f"State directory cannot be a link or reparse point: {parent}")
            for profile in sorted(parent.iterdir(), key=lambda item: item.name):
                if is_reparse_or_symlink(profile):
                    raise ManagerError(f"State profile cannot be a link or reparse point: {profile}")
                path = profile / filename
                if profile.is_dir() and path.is_file():
                    result.append(Target(namespace, path.resolve(), "user", path.relative_to(data_root)))
    for workspace in workspaces:
        resolved = workspace.resolve()
        if not (resolved / ".atomlearn").is_dir() or is_reparse_or_symlink(resolved):
            raise ManagerError(f"Invalid or linked AtomLearn workspace: {resolved}")
        reference = "ws-" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
        for namespace, relative in WORKSPACE_TARGETS.items():
            path = resolved / relative
            if path.is_file():
                result.append(Target(namespace, path.resolve(), "workspace", Path(relative), reference))
    return result


def _tree_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if is_reparse_or_symlink(path):
            raise ManagerError(f"State-copy source cannot contain links or reparse points: {path}")
        if path.is_file():
            result.append(path)
    return result


def state_copy_size(data_root: Path | None, workspaces: list[Path]) -> int:
    total = 0
    if data_root and data_root.is_dir():
        for folder in ["profiles", "strategies"]:
            total += sum(path.stat().st_size for path in _tree_files(data_root / folder))
    for workspace in workspaces:
        total += sum(path.stat().st_size for path in _tree_files(workspace.resolve() / ".atomlearn"))
    return total


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
        if is_reparse_or_symlink(path):
            raise ManagerError(f"State-copy source cannot contain links or reparse points: {path}")
        if path.is_dir():
            (destination / path.relative_to(source)).mkdir(parents=True, exist_ok=True)
    for path in _tree_files(source):
        atomic_bytes(destination / path.relative_to(source), path.read_bytes())


def _migration_path(namespace: str, current: int, target: int) -> list[Migration]:
    result: list[Migration] = []
    while current < target:
        function = MIGRATIONS.get((namespace, current))
        if function is None:
            raise ManagerError(f"No trusted manager migration for {namespace} schema {current}->{current + 1}")
        result.append(function)
        current += 1
    return result


def plan_document(namespace: str, value: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    compatibility = manifest.get("schemas", {}).get(namespace)
    version = value.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return {"namespace": namespace, "status": "forbidden", "reason": "invalid_schema_version"}
    if not isinstance(compatibility, dict):
        return {"namespace": namespace, "from_version": version, "status": "needs_review", "reason": "namespace_not_declared"}
    target = compatibility.get("write")
    if version == target:
        status, reason = "compatible", "current_write_schema"
    elif isinstance(target, int) and version < target:
        try:
            steps = _migration_path(namespace, version, target)
        except ManagerError:
            status, reason = "needs_review", "missing_trusted_migration"
        else:
            return {
                "namespace": namespace,
                "from_version": version,
                "to_version": target,
                "status": "migrate",
                "reason": "trusted_migration_path",
                "steps": [f"{version + index}->{version + index + 1}" for index in range(len(steps))],
            }
    else:
        status, reason = "needs_review", "future_or_unsupported_schema"
    return {"namespace": namespace, "from_version": version, "to_version": target, "status": status, "reason": reason}


def plan_state(data_root: Path | None, workspaces: list[Path], manifest: dict[str, Any]) -> dict[str, Any]:
    items = []
    for target in catalog(data_root, workspaces):
        item = plan_document(target.namespace, read_mapping(target.path), manifest)
        item.update({"scope": target.scope, "path": str(target.path)})
        items.append(item)
    return {
        "target_count": len(items),
        "counts": {status: sum(item["status"] == status for item in items) for status in ["compatible", "migrate", "needs_review", "forbidden"]},
        "items": items,
    }


def migrate_value(namespace: str, value: dict[str, Any], target_version: int) -> dict[str, Any]:
    current = value.get("schema_version")
    if not isinstance(current, int) or isinstance(current, bool) or current < 1:
        raise ManagerError(f"{namespace} has no valid schema version")
    result = copy.deepcopy(value)
    for function in _migration_path(namespace, current, target_version):
        before = int(result["schema_version"])
        result = function(copy.deepcopy(result))
        if not isinstance(result, dict) or result.get("schema_version") != before + 1:
            raise ManagerError(f"Trusted migration for {namespace} did not advance exactly one schema version")
    return result


def snapshot_and_migrate(
    transaction_root: Path,
    data_root: Path | None,
    workspaces: list[Path],
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    plan = plan_state(data_root, workspaces, manifest)
    blocked = [item for item in plan["items"] if item["status"] in {"needs_review", "forbidden"}]
    if blocked:
        raise ManagerError("State-copy migration is blocked:\n- " + "\n- ".join(f"{item['path']}: {item['reason']}" for item in blocked))
    if data_root and data_root.is_dir():
        for folder in ["profiles", "strategies"]:
            _copy_tree(data_root / folder, transaction_root / "state-copy" / "data" / folder)
    for workspace in workspaces:
        resolved = workspace.resolve()
        reference = "ws-" + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
        _copy_tree(resolved / ".atomlearn", transaction_root / "state-copy" / "workspaces" / reference / ".atomlearn")
    result: list[dict[str, Any]] = []
    for index, target in enumerate(catalog(data_root, workspaces), start=1):
        if is_reparse_or_symlink(target.path):
            raise ManagerError(f"State files cannot be links or reparse points: {target.path}")
        original_bytes = target.path.read_bytes()
        value = read_mapping(target.path)
        compatibility = manifest["schemas"][target.namespace]
        target_version = int(compatibility["write"])
        migrated_value = migrate_value(target.namespace, value, target_version) if value["schema_version"] < target_version else value
        migrated_bytes = (
            yaml.safe_dump(migrated_value, allow_unicode=True, sort_keys=False, width=100).encode("utf-8")
            if migrated_value != value
            else original_bytes
        )
        scope_root = Path("data") / target.relative if target.scope == "user" else Path("workspaces") / str(target.workspace_ref) / target.relative
        backup = transaction_root / "state-before" / f"item-{index:04d}.yaml"
        migrated = transaction_root / "state-copy" / scope_root
        atomic_bytes(backup, original_bytes)
        atomic_bytes(migrated, migrated_bytes)
        minimum = migrated_value.get("min_reader_core_version")
        if isinstance(minimum, str) and version_tuple(manifest["version"]) < version_tuple(minimum):
            raise ManagerError(f"Target Core is older than {target.namespace} minimum reader {minimum}")
        if migrated_value["schema_version"] not in compatibility["read"]:
            raise ManagerError(f"Target Core cannot read migrated {target.namespace} schema")
        result.append(
            {
                "namespace": target.namespace,
                "original": str(target.path),
                "backup": str(backup),
                "migrated": str(migrated),
                "original_hash": sha256_bytes(original_bytes),
                "migrated_hash": sha256_bytes(migrated_bytes),
                "changed": original_bytes != migrated_bytes,
                "applied": False,
            }
        )
    return result


def apply_migrated_files(files: list[dict[str, Any]], on_applied: Callable[[], None] | None = None) -> None:
    for item in files:
        if not item["changed"]:
            continue
        original = Path(item["original"])
        if sha256_bytes(original.read_bytes()) != item["original_hash"]:
            raise ManagerError(f"State changed after snapshot; refusing to overwrite: {original}")
        atomic_bytes(original, Path(item["migrated"]).read_bytes())
        item["applied"] = True
        if on_applied:
            on_applied()


def restore_files(files: list[dict[str, Any]], on_restored: Callable[[], None] | None = None) -> None:
    for item in files:
        if not item["applied"]:
            continue
        original = Path(item["original"])
        if not original.is_file() or sha256_bytes(original.read_bytes()) != item["migrated_hash"]:
            raise ManagerError(f"State changed after activation; automatic recovery would overwrite newer data: {original}")
        atomic_bytes(original, Path(item["backup"]).read_bytes())
        item["applied"] = False
        if on_restored:
            on_restored()
