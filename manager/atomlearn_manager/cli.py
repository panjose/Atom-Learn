"""Command-line surface for the stable AtomLearn Release Manager."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import MANAGER_VERSION
from .common import ManagerError, manager_root
from .codex import bridge_status, codex_home, install_bridge, resolve_core_skill
from .manager import apply_update, check_release, plan_update, recover_latest, rollback, status
from .manifest import (
    accept_tofu,
    break_glass_trust,
    initialize_trust,
    initialize_trust_bundle,
    load_trust,
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
    return parser


def _data_path(value: str | None) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ManagerError(f"User-data root must be absolute: {path}")
    return path.resolve(strict=False)


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    root = manager_root(args.manager_root, create=args.command == "init")
    if args.command != "init" and not root.is_dir():
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
        else:
            home = codex_home(args.codex_home)
            if args.codex_action == "status":
                result = bridge_status(root, home)
            elif args.codex_action == "install":
                result = install_bridge(root, home)
            else:
                result = install_bridge(root, home, repair=True, confirmed=args.confirmed)
    elif args.command == "version":
        state = status(root)
        result = {
            "ok": state["ok"],
            "manager_version": MANAGER_VERSION,
            "active_core_version": state["active"]["current_version"] if state["active"] else None,
        }
    elif args.command == "rollback":
        result = rollback(root, args.version, args.confirmed)
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
            result = plan_update(root, args.version, args.manifest, artifact, runtime_bundle, data_root, workspaces, args.channel)
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
