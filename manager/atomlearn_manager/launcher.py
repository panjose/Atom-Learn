"""Stable dispatcher for the signed Core selected by the manager active pointer."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from .common import ManagerError, canonical_json, manager_root, read_mapping, sha256_bytes
from .manager import load_active, verify_installed
from .manifest import load_trust, validate_release_manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the currently active signed AtomLearn Core",
        epilog="Use '--' before Core options, for example: atomlearn-core -- --help",
    )
    parser.add_argument("--manager-root", help="Absolute isolated manager root")
    parser.add_argument("core_args", nargs=argparse.REMAINDER, help="Arguments passed unchanged to the active Core")
    return parser


def active_core(root: Path) -> tuple[Path, str]:
    active = load_active(root)
    if active is None:
        raise ManagerError("No active AtomLearn Core; install a signed release first")
    version = active["current_version"]
    manifest = read_mapping(root / "manifests" / f"{version}.json")
    validate_release_manifest(manifest, load_trust(root))
    if sha256_bytes(canonical_json(manifest)) != active["manifest_hash"]:
        raise ManagerError("Active pointer does not match the signed release manifest")
    verify_installed(root, version, manifest)
    core = root / "releases" / version / "atom-learn" / "scripts" / "atomlearn.py"
    if not core.is_file():  # pragma: no cover - verify_installed checks the content tree first
        raise ManagerError(f"Active Core entry point is missing: {core}")
    return core, version


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = manager_root(args.manager_root)
    if not root.is_dir():
        raise ManagerError(f"Manager is not initialized: {root}")
    core, _ = active_core(root)
    core_args = list(args.core_args)
    if core_args[:1] == ["--"]:
        core_args = core_args[1:]
    if not core_args:
        core_args = ["--help"]
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, str(core), *core_args], env=environment, check=False)
    return result.returncode


def main() -> int:
    try:
        return run()
    except (ManagerError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
