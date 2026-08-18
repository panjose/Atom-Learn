#!/usr/bin/env python3
"""Deterministic schema migration planning and validation for AtomLearn state."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml
from jsonschema import Draft202012Validator

from platform_state import (
    CORE_ROOT,
    PlatformStateError,
    core_version,
    load_core_manifest,
    resolve_user_data_root,
    validate_envelope,
)


MigrationFunction = Callable[[dict[str, Any]], dict[str, Any]]

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
    "workspace_episodes": ".atomlearn/episodes/state.yaml",
}
USER_TARGETS = {
    "user_profile": ("profiles", "state.yaml"),
    "user_strategy": ("strategies", "state.yaml"),
}


class MigrationError(RuntimeError):
    """A deterministic migration cannot be planned or validated."""


def read_mapping(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MigrationError(f"Cannot parse migration target {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"Migration target must contain a mapping: {path}")
    return value


@dataclass(frozen=True)
class MigrationStep:
    namespace: str
    from_version: int
    to_version: int
    migrate: MigrationFunction


class MigrationRegistry:
    def __init__(self) -> None:
        self._steps: dict[tuple[str, int], MigrationStep] = {}

    def register(
        self,
        namespace: str,
        from_version: int,
        to_version: int,
        migrate: MigrationFunction,
    ) -> None:
        if to_version != from_version + 1:
            raise MigrationError("Migration steps must advance exactly one schema version")
        key = (namespace, from_version)
        if key in self._steps:
            raise MigrationError(f"Duplicate migration step for {namespace} schema {from_version}")
        self._steps[key] = MigrationStep(namespace, from_version, to_version, migrate)

    def path(self, namespace: str, from_version: int, to_version: int) -> list[MigrationStep]:
        if from_version > to_version:
            raise MigrationError("Downgrade migrations are not applied to active state")
        result: list[MigrationStep] = []
        current = from_version
        while current < to_version:
            step = self._steps.get((namespace, current))
            if step is None:
                raise MigrationError(
                    f"No deterministic migration registered for {namespace} schema {current} -> {current + 1}"
                )
            result.append(step)
            current = step.to_version
        return result

    def migrate_document(self, namespace: str, value: dict[str, Any], to_version: int) -> dict[str, Any]:
        current = value.get("schema_version")
        if not isinstance(current, int) or isinstance(current, bool) or current < 1:
            raise MigrationError(f"{namespace} state has no valid schema_version")
        result = copy.deepcopy(value)
        for step in self.path(namespace, current, to_version):
            result = step.migrate(copy.deepcopy(result))
            if not isinstance(result, dict):
                raise MigrationError(f"{namespace} migration {step.from_version}->{step.to_version} returned non-mapping")
            if result.get("schema_version") != step.to_version:
                raise MigrationError(
                    f"{namespace} migration {step.from_version}->{step.to_version} did not set schema_version"
                )
        return result


REGISTRY = MigrationRegistry()


@dataclass(frozen=True)
class StateTarget:
    namespace: str
    path: Path
    scope: str


def catalog_targets(
    *,
    data_root: Path | None = None,
    workspaces: list[Path] | None = None,
) -> list[StateTarget]:
    targets: list[StateTarget] = []
    root = data_root or resolve_user_data_root(create=False)
    if root.is_dir():
        for namespace, (folder, filename) in USER_TARGETS.items():
            parent = root / folder
            if not parent.is_dir():
                continue
            for profile in sorted(parent.iterdir(), key=lambda item: item.name):
                path = profile / filename
                if profile.is_dir() and path.is_file():
                    targets.append(StateTarget(namespace, path.resolve(), "user"))
    for workspace in workspaces or []:
        resolved = workspace.resolve()
        for namespace, relative in WORKSPACE_TARGETS.items():
            path = resolved / relative
            if path.is_file():
                targets.append(StateTarget(namespace, path.resolve(), "workspace"))
    return targets


def plan_target(target: StateTarget, manifest: dict[str, Any], registry: MigrationRegistry = REGISTRY) -> dict[str, Any]:
    value = read_mapping(target.path)
    version = value.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return {
            "namespace": target.namespace,
            "path": str(target.path),
            "scope": target.scope,
            "status": "forbidden",
            "reason": "invalid_schema_version",
        }
    compatibility = manifest.get("schemas", {}).get(target.namespace)
    if not isinstance(compatibility, dict):
        return {
            "namespace": target.namespace,
            "path": str(target.path),
            "scope": target.scope,
            "from_version": version,
            "status": "needs_review",
            "reason": "namespace_not_declared",
        }
    write_version = compatibility.get("write")
    readable = compatibility.get("read", [])
    if version == write_version:
        status = "compatible"
        reason = "current_write_schema"
    elif version in readable and isinstance(write_version, int) and version < write_version:
        try:
            steps = registry.path(target.namespace, version, write_version)
        except MigrationError:
            status = "compatible"
            reason = "read_only_schema_no_migration_required"
        else:
            status = "migrated"
            reason = "deterministic_path_available"
            return {
                "namespace": target.namespace,
                "path": str(target.path),
                "scope": target.scope,
                "from_version": version,
                "to_version": write_version,
                "status": status,
                "reason": reason,
                "steps": [f"{step.from_version}->{step.to_version}" for step in steps],
            }
    elif isinstance(write_version, int) and version < write_version:
        try:
            steps = registry.path(target.namespace, version, write_version)
        except MigrationError:
            status = "needs_review"
            reason = "missing_migration_path"
        else:
            status = "migrated"
            reason = "deterministic_path_available"
            return {
                "namespace": target.namespace,
                "path": str(target.path),
                "scope": target.scope,
                "from_version": version,
                "to_version": write_version,
                "status": status,
                "reason": reason,
                "steps": [f"{step.from_version}->{step.to_version}" for step in steps],
            }
    else:
        status = "needs_review"
        reason = "future_or_unsupported_schema"
    return {
        "namespace": target.namespace,
        "path": str(target.path),
        "scope": target.scope,
        "from_version": version,
        "to_version": write_version,
        "status": status,
        "reason": reason,
    }


def build_plan(
    *,
    data_root: Path | None = None,
    workspaces: list[Path] | None = None,
    registry: MigrationRegistry = REGISTRY,
) -> dict[str, Any]:
    manifest = load_core_manifest()
    targets = catalog_targets(data_root=data_root, workspaces=workspaces)
    items = [plan_target(target, manifest, registry) for target in targets]
    return {
        "core_version": manifest["core_version"],
        "data_root": str(data_root or resolve_user_data_root(create=False)),
        "target_count": len(items),
        "counts": {
            status: sum(item["status"] == status for item in items)
            for status in ["compatible", "migrated", "needs_review", "forbidden"]
        },
        "items": items,
    }


def validate_target(target: StateTarget, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        value = read_mapping(target.path)
        validate_envelope(value, target.namespace, manifest)
    except (MigrationError, PlatformStateError) as exc:
        errors.append(str(exc))
        return errors
    schema_names = {
        "user_profile": "user-profile",
        "user_strategy": "user-strategy",
        "workspace_profile_binding": "profile-binding",
        "workspace_episodes": "episode-checkpoint-state",
    }
    schema_name = schema_names.get(target.namespace)
    if schema_name:
        schema_path = CORE_ROOT / "assets" / "schemas" / f"{schema_name}.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        for error in sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path)):
            location = ".".join(str(piece) for piece in error.path) or "<root>"
            errors.append(f"{target.path} {location}: {error.message}")
    return errors


def validate_catalog(*, data_root: Path | None = None, workspaces: list[Path] | None = None) -> dict[str, Any]:
    manifest = load_core_manifest()
    targets = catalog_targets(data_root=data_root, workspaces=workspaces)
    details = []
    for target in targets:
        errors = validate_target(target, manifest)
        details.append(
            {
                "namespace": target.namespace,
                "path": str(target.path),
                "valid": not errors,
                "errors": errors,
            }
        )
    return {
        "ok": all(item["valid"] for item in details),
        "core_version": manifest["core_version"],
        "target_count": len(details),
        "targets": details,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan and validate deterministic AtomLearn state migrations")
    sub = parser.add_subparsers(dest="action", required=True)
    status = sub.add_parser("status", help="Show Core compatibility and discovered state counts")
    status.add_argument("--data-dir")
    for action, help_text in [
        ("plan", "Preview deterministic migrations without changing any state"),
        ("validate", "Validate state envelopes against current Core compatibility"),
    ]:
        command = sub.add_parser(action, help=help_text)
        command.add_argument("--data-dir")
        command.add_argument("--workspace", action="append", default=[])
    return parser


def _data_root(value: str | None) -> Path:
    return resolve_user_data_root(value, create=False)


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    data_root = _data_root(args.data_dir)
    workspaces = [Path(value) for value in getattr(args, "workspace", [])]
    if args.action == "status":
        manifest = load_core_manifest()
        targets = catalog_targets(data_root=data_root)
        result = {
            "ok": True,
            "core_version": core_version(),
            "release_channel": manifest["release_channel"],
            "data_root": str(data_root),
            "data_root_exists": data_root.is_dir(),
            "discovered_targets": len(targets),
            "schemas": manifest["schemas"],
        }
    elif args.action == "plan":
        result = {"ok": True, **build_plan(data_root=data_root, workspaces=workspaces)}
    elif args.action == "validate":
        result = validate_catalog(data_root=data_root, workspaces=workspaces)
        if not result["ok"]:
            raise MigrationError("State compatibility validation failed:\n- " + "\n- ".join(
                error for target in result["targets"] for error in target["errors"]
            ))
    else:  # pragma: no cover
        raise MigrationError(f"Unhandled migration action: {args.action}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except (MigrationError, PlatformStateError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
