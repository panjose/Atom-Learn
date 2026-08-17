"""Idempotent stable onboarding and conservative Codex Skill ownership migration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .codex import (
    BRIDGE_PROTOCOL_VERSION,
    PACKAGE_ROOT,
    _content_identity,
    bridge_status,
    bridge_path,
    codex_home,
    install_bridge,
    migrate_source_copy,
    recover_bridge_migration,
    source_copy_status,
)
from .common import ManagerError, manager_root
from .manager import (
    _manifest_from_source,
    _manifest_hash,
    apply_profile,
    apply_update,
    capability_doctor,
    load_active,
    plan_update,
    profile_plan,
    recover_latest,
    recover_profile,
    status as manager_status,
    verify_installed,
)
from .manifest import (
    initialize_trust_bundle,
    load_trust,
    pin_trust,
    trust_from_bundle,
    validate_release_manifest,
)
from .runtime import platform_identity, select_runtime
from .verify import verify_release


DEFAULT_TRUST_BUNDLE = PACKAGE_ROOT / "trust" / "atomlearn-trust-bundle.json"


def default_manifest_source(version: str) -> str:
    return (
        "https://github.com/panjose/Atom-Learn/releases/download/"
        f"v{version}/atomlearn-{version}.manifest.json"
    )


def _active_fingerprints(trust: dict[str, Any]) -> list[str]:
    return sorted(item["fingerprint"] for item in trust["keys"].values() if item["status"] == "active")


def _trust_plan(root: Path, bundle: Path, expected_fingerprint: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    trust_path = root / "trust.yaml"
    if trust_path.is_file():
        trust = load_trust(root)
        fingerprints = _active_fingerprints(trust)
        if expected_fingerprint is not None and expected_fingerprint not in fingerprints:
            raise ManagerError("Bootstrap fingerprint does not match the existing active trust root")
        if trust.get("schema_version") != 2:
            action = "replace_legacy_trust_manually"
            ready = False
        elif trust["trust_level"] == "unverified":
            action = "pin_existing" if expected_fingerprint else "provide_or_accept_fingerprint"
            ready = expected_fingerprint is not None
        else:
            action = "reuse"
            ready = True
        return trust, {
            "action": action,
            "ready": ready,
            "trust_level": trust.get("trust_level", "legacy"),
            "bundle_version": trust.get("bundle_version"),
            "active_fingerprints": fingerprints,
            "trust_path": str(trust_path),
        }
    trust = trust_from_bundle(bundle, expected_fingerprint)
    return trust, {
        "action": "initialize" if expected_fingerprint else "provide_fingerprint",
        "ready": expected_fingerprint is not None,
        "trust_level": trust["trust_level"],
        "bundle_version": trust["bundle_version"],
        "active_fingerprints": _active_fingerprints(trust),
        "trust_path": str(trust_path),
    }


def _artifact_skill_tree(manifest: dict[str, Any], artifact: Path | None) -> dict[str, list[str]]:
    if artifact is None:
        return {}
    inspected = verify_release(manifest, artifact)
    files = {
        relative.removeprefix("atom-learn/"): content
        for relative, content in inspected["files"]
        if relative.startswith("atom-learn/")
    }
    return {_content_identity(files): [manifest["version"]]}


def bootstrap_plan(
    root: Path,
    version: str,
    *,
    trust_bundle: Path = DEFAULT_TRUST_BUNDLE,
    expected_fingerprint: str | None,
    manifest_source: str | None = None,
    artifact: Path | None = None,
    runtime_bundle: Path | None = None,
    profile_name: str = "base",
    allow_experimental: bool = False,
    model_dir: Path | None = None,
    data_root: Path | None = None,
    workspaces: list[Path] | None = None,
    channel: str = "stable",
    home: Path | None = None,
) -> dict[str, Any]:
    root = root.resolve(strict=False)
    home = (home or codex_home(None)).resolve(strict=False)
    skill_target = bridge_path(home).resolve(strict=False)
    if root == skill_target or root in skill_target.parents or skill_target in root.parents:
        raise ManagerError("Manager root must be isolated from the Codex Skill path")
    workspaces = workspaces or []
    trust, trust_plan = _trust_plan(root, trust_bundle.resolve(), expected_fingerprint)
    source = manifest_source or default_manifest_source(version)
    manifest, resolved_source = _manifest_from_source(source)
    if manifest is None:
        raise ManagerError("Bootstrap requires a signed release manifest")
    validate_release_manifest(manifest, trust, requested_channel=channel)
    if manifest["version"] != version:
        raise ManagerError("Bootstrap version does not match the signed release manifest")
    if manifest.get("manifest_version") != 2:
        raise ManagerError("Stable bootstrap requires a signed manifest v2 bridge protocol")
    protocol = manifest["skill_protocol"]
    if not protocol["bridge_min"] <= BRIDGE_PROTOCOL_VERSION <= protocol["bridge_max"]:
        raise ManagerError("Signed Core Skill protocol is incompatible with this Manager bridge")

    active = load_active(root) if root.is_dir() else None
    core_ready = True
    if active is None:
        core_action = "install"
        core_plan = plan_update(
            root,
            version,
            source,
            artifact,
            runtime_bundle,
            data_root,
            workspaces,
            channel,
            profile_name,
            allow_experimental=allow_experimental,
            trust_override=trust,
        )
    elif active["current_version"] == version:
        if active["manifest_hash"] != _manifest_hash(manifest):
            raise ManagerError("Active Core version has a different signed manifest identity")
        verify_installed(root, version, manifest)
        selected = select_runtime(manifest, profile_name)
        if selected is not None and active.get("runtime_id") != selected["id"]:
            core_action = "activate_profile"
            core_plan = profile_plan(
                root,
                profile_name,
                runtime_bundle,
                allow_experimental=allow_experimental,
            )
        else:
            core_action = "reuse_active"
            core_plan = {
                "ok": True,
                "current_version": version,
                "target_version": version,
                "runtime": {"profile": profile_name, "id": active.get("runtime_id")},
                "disk": {"sufficient": True},
                "activation": "none_idempotent",
            }
    else:
        from .common import version_tuple

        if version_tuple(active["current_version"]) > version_tuple(version):
            core_action = "refuse_downgrade"
            core_ready = False
            core_plan = {
                "ok": False,
                "current_version": active["current_version"],
                "target_version": version,
                "reason": "paired_rollback_required",
                "disk": {"sufficient": True},
            }
        else:
            core_action = "update"
            core_plan = plan_update(
                root,
                version,
                source,
                artifact,
                runtime_bundle,
                data_root,
                workspaces,
                channel,
                profile_name,
                allow_experimental=allow_experimental,
                trust_override=trust,
            )
    core_ready = core_ready and core_plan.get("disk", {}).get("sufficient", True)
    known_trees = _artifact_skill_tree(manifest, artifact)
    bridge = source_copy_status(root, home, additional_known_trees=known_trees)
    bridge_ready = bridge["classification"] in {
        "absent",
        "owned_bridge",
        "owned_bridge_needs_repair",
        "official_source_copy",
    }
    system, architecture, python_minor = platform_identity()
    ready = trust_plan["ready"] and core_ready and bridge_ready
    remediation = []
    if not trust_plan["ready"]:
        remediation.append("provide an independently verified active-key fingerprint")
    if not core_ready:
        remediation.append("resolve the Core version or disk-space blocker")
    if not bridge_ready:
        remediation.append("move or review the unknown/modified Codex Skill manually; it will not be overwritten")
    return {
        "ok": True,
        "ready": ready,
        "manager_root": str(root),
        "manager_root_exists": root.is_dir(),
        "trust_bundle": str(trust_bundle.resolve()),
        "trust": trust_plan,
        "target": {
            "version": version,
            "profile": profile_name,
            "channel": channel,
            "platform": system,
            "architecture": architecture,
            "python_minor": python_minor,
            "manifest_source": resolved_source,
            "model_dir": str(model_dir.resolve()) if model_dir else None,
        },
        "core_action": core_action,
        "core": core_plan,
        "bridge": bridge,
        "write_locations": {
            "manager_root": str(root),
            "trust": str(root / "trust.yaml"),
            "active": str(root / "active.yaml"),
            "codex_skill": str(home / "skills" / "atom-learn"),
        },
        "confirmation_required": True,
        "remediation": remediation,
    }


def bootstrap_apply(
    root: Path,
    version: str,
    *,
    trust_bundle: Path = DEFAULT_TRUST_BUNDLE,
    expected_fingerprint: str | None,
    manifest_source: str | None = None,
    artifact: Path | None = None,
    runtime_bundle: Path | None = None,
    profile_name: str = "base",
    allow_experimental: bool = False,
    model_dir: Path | None = None,
    data_root: Path | None = None,
    workspaces: list[Path] | None = None,
    channel: str = "stable",
    home: Path | None = None,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise ManagerError("Bootstrap apply requires --confirmed after reviewing bootstrap plan")
    plan = bootstrap_plan(
        root,
        version,
        trust_bundle=trust_bundle,
        expected_fingerprint=expected_fingerprint,
        manifest_source=manifest_source,
        artifact=artifact,
        runtime_bundle=runtime_bundle,
        profile_name=profile_name,
        allow_experimental=allow_experimental,
        model_dir=model_dir,
        data_root=data_root,
        workspaces=workspaces,
        channel=channel,
        home=home,
    )
    if not plan["ready"]:
        raise ManagerError("Bootstrap plan is not ready", code="bootstrap_blocked", details=plan)
    root = manager_root(root, create=True)
    home = (home or codex_home(None)).resolve(strict=False)
    if plan["trust"]["action"] == "initialize":
        trust_result = initialize_trust_bundle(root, trust_bundle.resolve(), expected_fingerprint)
    elif plan["trust"]["action"] == "pin_existing":
        if expected_fingerprint is None:  # defensive; plan cannot select this action without it
            raise ManagerError("Existing unverified trust requires an explicit fingerprint")
        trust_result = pin_trust(root, expected_fingerprint, True)
    else:
        trust_result = load_trust(root)

    source = manifest_source or default_manifest_source(version)
    workspaces = workspaces or []
    if plan["core_action"] in {"install", "update"}:
        core_result = apply_update(
            root,
            version,
            source,
            artifact,
            runtime_bundle,
            data_root,
            workspaces,
            channel,
            True,
            profile_name,
            model_dir,
            allow_experimental=allow_experimental,
        )
    elif plan["core_action"] == "activate_profile":
        core_result = apply_profile(
            root,
            profile_name,
            runtime_bundle,
            confirmed=True,
            allow_experimental=allow_experimental,
            model_dir=model_dir,
        )
    else:
        core_result = {"ok": True, "idempotent": True, "active": load_active(root)}

    classification = plan["bridge"]["classification"]
    if classification == "absent":
        bridge_result = install_bridge(root, home)
    elif classification == "owned_bridge":
        bridge_result = {"ok": True, "idempotent": True, "status": bridge_status(root, home)}
    elif classification == "owned_bridge_needs_repair":
        bridge_result = install_bridge(root, home, repair=True, confirmed=True)
    else:
        bridge_result = migrate_source_copy(root, home, confirmed=True)
    final_bridge = bridge_status(root, home)
    if not final_bridge["ok"]:
        raise ManagerError("Bootstrap bridge verification failed")
    doctor = capability_doctor(root, profile_name, model_dir=model_dir)
    if not doctor["ok"]:
        raise ManagerError("Bootstrap capability doctor failed", code="bootstrap_doctor_failed", details=doctor)
    return {
        "ok": True,
        "idempotent": plan["core_action"] == "reuse_active" and classification == "owned_bridge",
        "manager_root": str(root),
        "trust": trust_result,
        "core": core_result,
        "bridge": bridge_result,
        "doctor": doctor,
    }


def bootstrap_status(root: Path, home: Path | None = None) -> dict[str, Any]:
    home = (home or codex_home(None)).resolve(strict=False)
    if not root.is_dir():
        return {
            "ok": True,
            "initialized": False,
            "manager_root": str(root),
            "bridge": source_copy_status(root, home),
        }
    trust = load_trust(root) if (root / "trust.yaml").is_file() else None
    active = load_active(root)
    bridge = source_copy_status(root, home)
    state = manager_status(root) if trust is not None else None
    return {
        "ok": bool(trust is not None and active is not None and bridge.get("classification") == "owned_bridge"),
        "initialized": trust is not None,
        "manager_root": str(root),
        "trust_level": trust.get("trust_level") if trust else None,
        "active": active,
        "bridge": bridge,
        "manager": state,
    }


def bootstrap_recover(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        return {"ok": True, "recovered": False, "reason": "manager_not_initialized"}
    core = recover_latest(root)
    profile = recover_profile(root)
    bridge = recover_bridge_migration(root)
    return {
        "ok": core["ok"] and profile["ok"] and bridge["ok"],
        "recovered": any(item.get("recovered", False) for item in [core, profile, bridge]),
        "core": core,
        "profile": profile,
        "bridge": bridge,
    }
