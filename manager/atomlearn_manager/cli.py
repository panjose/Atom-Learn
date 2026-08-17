"""Command-line surface for the stable AtomLearn Release Manager."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import MANAGER_VERSION
from .bootstrap import (
    DEFAULT_TRUST_BUNDLE,
    bootstrap_apply,
    bootstrap_plan,
    bootstrap_recover,
    bootstrap_status,
)
from .common import ManagerError, manager_root
from .codex import (
    bridge_status,
    codex_home,
    install_bridge,
    migrate_source_copy,
    recover_bridge_migration,
    resolve_core_skill,
    source_copy_status,
)
from .manager import (
    apply_profile,
    apply_update,
    capability_doctor,
    check_release,
    plan_update,
    profile_plan,
    profile_status,
    recover_profile,
    recover_latest,
    rollback,
    rollback_profile,
    status,
)
from .manifest import (
    accept_tofu,
    break_glass_trust,
    initialize_trust,
    initialize_trust_bundle,
    load_trust,
    pin_trust,
    rotate_trust,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely install and recover signed immutable AtomLearn Core releases")
    parser.add_argument("--manager-root", help="Absolute isolated manager root")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize an explicit Ed25519 trust root")
    init.add_argument("--key-id", help="Stable identifier for a directly pinned Ed25519 public key")
    init.add_argument("--public-key", help="Base64 raw directly pinned Ed25519 public key")
    init.add_argument("--trust-bundle", help="Local trust-bundle JSON/YAML path; never bootstrap trust from a release URL")
    init.add_argument("--expected-fingerprint", help="Out-of-band sha256 fingerprint that pins an active bundle key")
    init.add_argument("--repository", default="panjose/Atom-Learn", help="Only GitHub owner/repository allowed to sign releases")
    sub.add_parser("version", help="Show stable manager and active Core versions")
    trust = sub.add_parser("trust", help="Inspect or rotate the local release trust root")
    trust_sub = trust.add_subparsers(dest="trust_action", required=True)
    trust_sub.add_parser("inspect", help="Show trust level bundle version keys and fingerprints")
    tofu = trust_sub.add_parser("accept-tofu", help="Explicitly accept a displayed first-seen active-key fingerprint")
    tofu.add_argument("--fingerprint", required=True, help="Exact sha256 fingerprint displayed by trust inspect")
    tofu.add_argument("--confirmed", action="store_true", help="Confirm trust-on-first-use rather than out-of-band pinning")
    pin = trust_sub.add_parser("pin", help="Promote an existing trust root using an out-of-band fingerprint")
    pin.add_argument("--fingerprint", required=True, help="Exact independently verified active-key sha256 fingerprint")
    pin.add_argument("--confirmed", action="store_true", help="Confirm the out-of-band fingerprint verification")
    rotate = trust_sub.add_parser("rotate", help="Apply a monotonic bundle signed by a currently trusted key")
    rotate.add_argument("--bundle", required=True, help="Local next-version signed trust bundle")
    rotate.add_argument("--confirmed", action="store_true", help="Confirm the reviewed trust-key rotation")
    break_glass = trust_sub.add_parser("break-glass", help="Replace a compromised key with an independently pinned bundle")
    break_glass.add_argument("--bundle", required=True, help="Local replacement trust bundle from an independent channel")
    break_glass.add_argument("--expected-fingerprint", required=True, help="Out-of-band fingerprint of a new active key")
    break_glass.add_argument("--confirmed", action="store_true", help="Confirm emergency replacement of the current trust root")
    codex = sub.add_parser("codex", help="Install inspect resolve or repair the stable Codex bridge Skill")
    codex_sub = codex.add_subparsers(dest="codex_action", required=True)
    codex_install = codex_sub.add_parser("install", help="Install the small manager-owned bridge without overwriting foreign Skills")
    codex_install.add_argument("--codex-home", help="Absolute Codex home; defaults to CODEX_HOME or ~/.codex")
    codex_status = codex_sub.add_parser("status", help="Verify bridge ownership and packaged content")
    codex_status.add_argument("--codex-home", help="Absolute Codex home; defaults to CODEX_HOME or ~/.codex")
    codex_resolve = codex_sub.add_parser("resolve", help="Resolve the exact active signed Core Skill entry point")
    codex_resolve.add_argument("--json", action="store_true", help="Emit the machine-readable JSON result (default output format)")
    codex_repair = codex_sub.add_parser("repair", help="Atomically replace an owned bridge and retain its previous copy")
    codex_repair.add_argument("--codex-home", help="Absolute Codex home; defaults to CODEX_HOME or ~/.codex")
    codex_repair.add_argument("--confirmed", action="store_true", help="Confirm repair of the manager-owned bridge")
    codex_migrate = codex_sub.add_parser("migrate", help="Plan apply or recover an exact official source-copy migration")
    codex_migrate_sub = codex_migrate.add_subparsers(dest="codex_migrate_action", required=True)
    for action, help_text in [
        ("plan", "Classify the existing atom-learn Skill without writing"),
        ("apply", "Back up an exact official source copy and install the bound bridge"),
        ("recover", "Restore the source copy from the latest interrupted migration"),
    ]:
        command = codex_migrate_sub.add_parser(action, help=help_text)
        command.add_argument("--codex-home", help="Absolute Codex home; defaults to CODEX_HOME or ~/.codex")
        if action == "apply":
            command.add_argument("--confirmed", action="store_true", help="Confirm backup and bridge replacement")
    bootstrap = sub.add_parser("bootstrap", help="Plan apply inspect or recover the stable signed onboarding path")
    bootstrap_sub = bootstrap.add_subparsers(dest="bootstrap_action", required=True)
    for action, help_text in [
        ("plan", "Preview trust Core profile bridge and every write location"),
        ("apply", "Execute the reviewed idempotent stable onboarding plan"),
    ]:
        command = bootstrap_sub.add_parser(action, help=help_text)
        command.add_argument("version", help="Target signed Core semantic version")
        command.add_argument(
            "--trust-bundle",
            default=str(DEFAULT_TRUST_BUNDLE),
            help="Local trust bundle; defaults to the Manager-packaged convenience bundle",
        )
        command.add_argument(
            "--expected-fingerprint",
            help="Independently verified active-key sha256 fingerprint; required for first stable bootstrap",
        )
        command.add_argument("--manifest", help="Local or HTTPS signed manifest; defaults to the canonical tagged release URL")
        command.add_argument("--artifact", help="Local signed Core artifact; omit to download during apply")
        command.add_argument("--runtime-bundle", help="Local matching runtime profile bundle; omit to download during apply")
        command.add_argument(
            "--profile",
            choices=["base", "scale", "semantic-cpu", "ocr", "semantic-gpu"],
            default="base",
            help="Finite signed runtime profile to activate",
        )
        command.add_argument(
            "--allow-experimental",
            action="store_true",
            help="Explicitly permit a profile whose signed stability level is experimental",
        )
        command.add_argument("--model-dir", help="Absolute local directory matching a signed semantic model lock")
        command.add_argument("--channel", choices=["stable", "prerelease"], default="stable", help="Required signed channel")
        command.add_argument("--data-dir", help="Absolute AtomLearn user-data root to copy and validate")
        command.add_argument("--workspace", action="append", default=[], help="Absolute course workspace; repeat as needed")
        command.add_argument("--codex-home", help="Absolute Codex home; defaults to CODEX_HOME or ~/.codex")
        if action == "apply":
            command.add_argument("--confirmed", action="store_true", help="Confirm the exact bootstrap plan")
    bootstrap_status_parser = bootstrap_sub.add_parser("status", help="Inspect trust Core and bridge onboarding state")
    bootstrap_status_parser.add_argument("--codex-home", help="Absolute Codex home; defaults to CODEX_HOME or ~/.codex")
    bootstrap_sub.add_parser("recover", help="Recover unfinished Core profile and bridge onboarding transactions")
    update = sub.add_parser("update", help="Check plan apply inspect or recover updates")
    update_sub = update.add_subparsers(dest="update_action", required=True)
    check = update_sub.add_parser("check", help="Read and verify one signed release manifest")
    check.add_argument("--manifest", required=True, help="Local path or HTTPS release-manifest URL")
    check.add_argument("--channel", choices=["stable", "prerelease"], default="stable", help="Required signed release channel")
    check.add_argument("--offline", action="store_true", help="Skip a remote request and keep the current Core available")
    for action, help_text in [
        ("plan", "Preview artifact disk schema and state-copy impact"),
        ("apply", "Install verify health-check and activate one release"),
    ]:
        command = update_sub.add_parser(action, help=help_text)
        command.add_argument("version", help="Target Core semantic version matching the signed manifest")
        command.add_argument("--manifest", required=True, help="Local path or HTTPS signed release-manifest URL")
        command.add_argument("--artifact", help="Local artifact; omit to download the signed URL")
        command.add_argument("--runtime-bundle", help="Local matching signed runtime bundle; omit to download its signed URL")
        command.add_argument(
            "--profile",
            choices=["base", "scale", "semantic-cpu", "ocr", "semantic-gpu"],
            default="base",
            help="Signed runtime profile to select for this Core release",
        )
        command.add_argument(
            "--model-dir",
            help="Absolute local model directory matching the semantic profile's signed model lock",
        )
        command.add_argument(
            "--allow-experimental",
            action="store_true",
            help="Explicitly permit a profile whose signed stability level is experimental",
        )
        command.add_argument("--channel", choices=["stable", "prerelease"], default="stable", help="Required signed release channel")
        command.add_argument("--data-dir", help="Absolute AtomLearn user-data root to copy and validate")
        command.add_argument("--workspace", action="append", default=[], help="Absolute course workspace; repeat for multiple courses")
        if action == "apply":
            command.add_argument("--confirmed", action="store_true", help="Confirm the previously reviewed update plan")
    update_sub.add_parser("status", help="Show active release and unfinished transactions")
    update_sub.add_parser("recover", help="Recover the latest interrupted update transaction")
    rollback_parser = sub.add_parser("rollback", help="Restore the paired previous Core and state copy")
    rollback_parser.add_argument("version", help="Paired previous Core version named by the active pointer")
    rollback_parser.add_argument("--confirmed", action="store_true", help="Confirm paired Core and state-snapshot rollback")
    profile = sub.add_parser("profile", help="Plan install inspect or roll back signed immutable runtime profiles")
    profile_sub = profile.add_subparsers(dest="profile_action", required=True)
    profile_sub.add_parser("status", help="List signed profiles for this platform and their installed/active state")
    for action, help_text in [
        ("plan", "Verify and preview one signed runtime profile without changing active state"),
        ("apply", "Install preflight smoke and atomically activate one signed runtime profile"),
    ]:
        command = profile_sub.add_parser(action, help=help_text)
        command.add_argument(
            "profile_name",
            choices=["base", "scale", "semantic-cpu", "ocr", "semantic-gpu"],
            help="Finite capability profile declared by the active signed release",
        )
        command.add_argument(
            "--runtime-bundle",
            help="Local matching signed profile bundle; omit during apply to download its signed URL",
        )
        command.add_argument(
            "--model-dir",
            help="Absolute local model directory matching the profile's signed model lock",
        )
        command.add_argument(
            "--allow-experimental",
            action="store_true",
            help="Explicitly permit a profile whose signed stability level is experimental",
        )
        if action == "apply":
            command.add_argument("--confirmed", action="store_true", help="Confirm the reviewed profile plan and activation")
    profile_rollback = profile_sub.add_parser("rollback", help="Atomically restore the paired previous runtime profile")
    profile_rollback.add_argument("--confirmed", action="store_true", help="Confirm paired runtime-profile rollback")
    profile_sub.add_parser("recover", help="Recover the latest interrupted runtime-profile transaction")
    doctor = sub.add_parser("doctor", help="Report available declared installed usable and stable capability states")
    doctor.add_argument(
        "--capability",
        choices=["base", "scale", "semantic-cpu", "ocr", "semantic-gpu"],
        help="Limit diagnosis to one capability",
    )
    doctor.add_argument(
        "--model-dir",
        help="Absolute local model directory to verify against a signed semantic model lock",
    )
    return parser


def _data_path(value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ManagerError(f"User-data root must be absolute: {path}")
    return path.resolve(strict=False)


def _optional_absolute(value: str | None, label: str) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ManagerError(f"{label} must be absolute: {path}")
    return path.resolve(strict=False)


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    root = manager_root(args.manager_root, create=args.command == "init")
    if args.command not in {"init", "bootstrap"} and not root.is_dir():
        raise ManagerError(f"Manager is not initialized: {root}")
    if args.command == "init":
        direct = bool(args.key_id or args.public_key)
        bundled = bool(args.trust_bundle)
        if direct == bundled:
            raise ManagerError("Initialize with exactly one of --key-id/--public-key or --trust-bundle")
        if direct:
            if not args.key_id or not args.public_key or args.expected_fingerprint:
                raise ManagerError("Direct trust initialization requires both --key-id and --public-key and no bundle fingerprint")
            trust = initialize_trust(root, args.key_id, args.public_key, args.repository)
        else:
            trust = initialize_trust_bundle(root, Path(args.trust_bundle).resolve(), args.expected_fingerprint)
        result = {"ok": True, "manager_root": str(root), "manager_version": MANAGER_VERSION, "trust": trust}
    elif args.command == "trust":
        if args.trust_action == "inspect":
            trust = load_trust(root)
            result = {"ok": True, "trust": trust}
        elif args.trust_action == "accept-tofu":
            trust = accept_tofu(root, args.fingerprint, args.confirmed)
            result = {"ok": True, "trust": trust}
        elif args.trust_action == "pin":
            trust = pin_trust(root, args.fingerprint, args.confirmed)
            result = {"ok": True, "trust": trust}
        elif args.trust_action == "break-glass":
            trust = break_glass_trust(
                root, Path(args.bundle).resolve(), args.expected_fingerprint, args.confirmed
            )
            result = {"ok": True, "trust": trust}
        else:
            trust = rotate_trust(root, Path(args.bundle).resolve(), args.confirmed)
            result = {"ok": True, "trust": trust}
    elif args.command == "codex":
        if args.codex_action == "resolve":
            result = resolve_core_skill(root)
        elif args.codex_action == "migrate":
            home = codex_home(args.codex_home)
            if args.codex_migrate_action == "plan":
                result = source_copy_status(root, home)
            elif args.codex_migrate_action == "apply":
                result = migrate_source_copy(root, home, confirmed=args.confirmed)
            else:
                result = recover_bridge_migration(root)
        else:
            home = codex_home(args.codex_home)
            if args.codex_action == "status":
                result = bridge_status(root, home)
            elif args.codex_action == "install":
                result = install_bridge(root, home)
            else:
                result = install_bridge(root, home, repair=True, confirmed=args.confirmed)
    elif args.command == "bootstrap":
        if args.bootstrap_action == "status":
            result = bootstrap_status(root, codex_home(args.codex_home))
        elif args.bootstrap_action == "recover":
            result = bootstrap_recover(root)
        else:
            data_root = _data_path(args.data_dir)
            workspaces = [_optional_absolute(value, "Course workspace") for value in args.workspace]
            artifact = Path(args.artifact).resolve() if args.artifact else None
            runtime_bundle = Path(args.runtime_bundle).resolve() if args.runtime_bundle else None
            model_dir = _optional_absolute(args.model_dir, "Model directory")
            common = {
                "trust_bundle": Path(args.trust_bundle).resolve(),
                "expected_fingerprint": args.expected_fingerprint,
                "manifest_source": args.manifest,
                "artifact": artifact,
                "runtime_bundle": runtime_bundle,
                "profile_name": args.profile,
                "allow_experimental": args.allow_experimental,
                "model_dir": model_dir,
                "data_root": data_root,
                "workspaces": workspaces,
                "channel": args.channel,
                "home": codex_home(args.codex_home),
            }
            if args.bootstrap_action == "plan":
                result = bootstrap_plan(root, args.version, **common)
            else:
                result = bootstrap_apply(root, args.version, confirmed=args.confirmed, **common)
    elif args.command == "version":
        state = status(root)
        result = {
            "ok": state["ok"],
            "manager_version": MANAGER_VERSION,
            "active_core_version": state["active"]["current_version"] if state["active"] else None,
        }
    elif args.command == "rollback":
        result = rollback(root, args.version, args.confirmed)
    elif args.command == "profile":
        if args.profile_action == "status":
            result = profile_status(root)
        elif args.profile_action == "recover":
            result = recover_profile(root)
        elif args.profile_action == "rollback":
            result = rollback_profile(root, confirmed=args.confirmed)
        else:
            runtime_bundle = Path(args.runtime_bundle).resolve() if args.runtime_bundle else None
            model_dir = _optional_absolute(args.model_dir, "Model directory")
            if args.profile_action == "plan":
                result = profile_plan(
                    root,
                    args.profile_name,
                    runtime_bundle,
                    allow_experimental=args.allow_experimental,
                )
            else:
                result = apply_profile(
                    root,
                    args.profile_name,
                    runtime_bundle,
                    confirmed=args.confirmed,
                    allow_experimental=args.allow_experimental,
                    model_dir=model_dir,
                )
    elif args.command == "doctor":
        result = capability_doctor(
            root,
            args.capability,
            model_dir=_optional_absolute(args.model_dir, "Model directory"),
        )
    elif args.update_action == "check":
        result = check_release(root, args.manifest, args.channel, offline=args.offline)
    elif args.update_action == "status":
        result = status(root)
    elif args.update_action == "recover":
        result = recover_latest(root)
    elif args.update_action in {"plan", "apply"}:
        data_root = _data_path(args.data_dir)
        workspaces = [Path(value).resolve() for value in args.workspace]
        artifact = Path(args.artifact).resolve() if args.artifact else None
        runtime_bundle = Path(args.runtime_bundle).resolve() if args.runtime_bundle else None
        if args.update_action == "plan":
            result = plan_update(
                root,
                args.version,
                args.manifest,
                artifact,
                runtime_bundle,
                data_root,
                workspaces,
                args.channel,
                args.profile,
                allow_experimental=args.allow_experimental,
            )
        else:
            result = apply_update(
                root,
                args.version,
                args.manifest,
                artifact,
                runtime_bundle,
                data_root,
                workspaces,
                args.channel,
                args.confirmed,
                args.profile,
                _optional_absolute(args.model_dir, "Model directory"),
                allow_experimental=args.allow_experimental,
            )
    else:  # pragma: no cover
        raise ManagerError("Unhandled manager command")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except ManagerError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        wrapped = ManagerError(str(exc), code="manager_input_error")
        print(json.dumps(wrapped.as_dict(), ensure_ascii=False, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
