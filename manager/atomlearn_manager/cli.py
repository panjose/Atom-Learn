"""Command-line surface for the stable AtomLearn Release Manager."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from . import MANAGER_VERSION
from .common import ManagerError, manager_root
from .manager import apply_update, check_release, plan_update, recover_latest, rollback, status
from .manifest import initialize_trust


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely install and recover signed immutable AtomLearn Core releases")
    parser.add_argument("--manager-root", help="Absolute isolated manager root")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Initialize an explicit Ed25519 trust root")
    init.add_argument("--key-id", required=True, help="Stable identifier for the trusted Ed25519 public key")
    init.add_argument("--public-key", required=True, help="Base64 raw Ed25519 public key")
    init.add_argument("--repository", default="panjose/Atom-Learn", help="Only GitHub owner/repository allowed to sign releases")
    sub.add_parser("version", help="Show stable manager and active Core versions")
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
        trust = initialize_trust(root, args.key_id, args.public_key, args.repository)
        result = {"ok": True, "manager_root": str(root), "manager_version": MANAGER_VERSION, "trust": trust}
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
        if args.update_action == "plan":
            result = plan_update(root, args.version, args.manifest, artifact, data_root, workspaces, args.channel)
        else:
            result = apply_update(
                root,
                args.version,
                args.manifest,
                artifact,
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
