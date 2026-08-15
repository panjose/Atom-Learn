"""Deterministic signed AtomLearn Core release artifact builder."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import MANAGER_VERSION
from .common import ManagerError, atomic_text, is_reparse_or_symlink, require_schema, sha256_bytes, sha256_file
from .manifest import sign_manifest
from .verify import ZERO_HASH, content_tree_hash


EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".test-workspaces", ".git"}


def _private_key(path: Path) -> Ed25519PrivateKey:
    content = path.read_bytes()
    try:
        loaded = serialization.load_pem_private_key(content, password=None)
    except ValueError:
        try:
            raw = base64.b64decode(content.strip(), validate=True)
            loaded = Ed25519PrivateKey.from_private_bytes(raw)
        except (ValueError, TypeError) as exc:
            raise ManagerError("Private key must be an unencrypted Ed25519 PEM or base64 raw 32-byte key") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise ManagerError("Release signing key is not Ed25519")
    return loaded


def _collect(source: Path) -> list[tuple[str, bytes]]:
    roots = [source / "atom-learn", source / "pyproject.toml", source / "README.md", source / "README.zh-CN.md"]
    result: list[tuple[str, bytes]] = []
    for root in roots:
        if not root.exists():
            raise ManagerError(f"Release source is missing: {root}")
        paths = [root] if root.is_file() else sorted(root.rglob("*"), key=lambda item: item.as_posix())
        for path in paths:
            relative = path.relative_to(source)
            if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
                continue
            if is_reparse_or_symlink(path):
                raise ManagerError(f"Release source links and reparse points are forbidden: {path}")
            if path.is_file():
                result.append((relative.as_posix(), path.read_bytes()))
    return result


def build_release(
    source: Path,
    output_dir: Path,
    *,
    tag: str,
    commit_sha: str,
    artifact_url: str,
    key_id: str,
    private_key_path: Path,
    gate_report_path: Path,
    channel: str,
    repository: str,
) -> dict[str, Any]:
    source = source.resolve()
    output_dir = output_dir.resolve(strict=False)
    if not output_dir.is_absolute():  # pragma: no cover - resolve is absolute
        raise ManagerError("Release output directory must be absolute")
    files = _collect(source)
    by_name = dict(files)
    project = tomllib.loads(by_name["pyproject.toml"].decode("utf-8"))
    version = project.get("project", {}).get("version")
    if not isinstance(version, str) or tag != f"v{version}":
        raise ManagerError("Git tag, package version, and Core version must match")
    if channel == "stable" and "-" in version:
        raise ManagerError("Stable artifacts cannot use prerelease versions")
    gate = json.loads(gate_report_path.read_text(encoding="utf-8"))
    if not isinstance(gate, dict):
        raise ManagerError("Release gate report must be a JSON object")
    require_schema(gate, "release-gate-report")
    if gate["tag"] != tag or gate["commit_sha"] != commit_sha:
        raise ManagerError("Release gate report tag/commit does not match the requested release")
    gate_bytes = json.dumps(gate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    by_name["release/gate-report.json"] = gate_bytes
    core_path = "atom-learn/assets/core-manifest.yaml"
    core = yaml.safe_load(by_name[core_path].decode("utf-8"))
    if not isinstance(core, dict) or core.get("core_version") != version:
        raise ManagerError("Package and Core manifest versions disagree")
    core["release_channel"] = channel
    core["artifact_sha256"] = ZERO_HASH
    by_name[core_path] = yaml.safe_dump(core, allow_unicode=True, sort_keys=False, width=100).encode("utf-8")
    tree_hash = content_tree_hash(by_name.items())
    core["artifact_sha256"] = tree_hash
    by_name[core_path] = yaml.safe_dump(core, allow_unicode=True, sort_keys=False, width=100).encode("utf-8")
    filename = f"atomlearn-{version}.zip"
    parsed = urlparse(artifact_url)
    expected_path = f"/{repository}/releases/download/{tag}/{filename}"
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or parsed.path != expected_path:
        raise ManagerError("Artifact URL must be the exact immutable GitHub release asset URL")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = output_dir / filename
    manifest_path = output_dir / f"atomlearn-{version}.manifest.json"
    if artifact_path.exists() or manifest_path.exists():
        raise ManagerError("Release outputs already exist and will not be overwritten")
    root = f"atomlearn-{version}"
    with zipfile.ZipFile(artifact_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, content in sorted(by_name.items()):
            info = zipfile.ZipInfo(f"{root}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100444 & 0xFFFF) << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    artifact_hash = sha256_file(artifact_path)
    unsigned = {
        "kind": "atomlearn.release-manifest",
        "manifest_version": 1,
        "version": version,
        "channel": channel,
        "tag": tag,
        "source": {
            "kind": "github_release",
            "repository": repository,
            "commit_sha": commit_sha,
            "artifact_url": artifact_url,
        },
        "artifact": {
            "filename": filename,
            "format": "zip",
            "sha256": artifact_hash,
            "size": artifact_path.stat().st_size,
        },
        "core_manifest_sha256": sha256_bytes(by_name[core_path]),
        "core_content_sha256": tree_hash,
        "gate_report_sha256": sha256_bytes(gate_bytes),
        "min_manager_version": MANAGER_VERSION,
        "schemas": core["schemas"],
    }
    manifest = sign_manifest(unsigned, key_id, _private_key(private_key_path))
    atomic_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "ok": True,
        "version": version,
        "artifact": str(artifact_path),
        "artifact_sha256": artifact_hash,
        "manifest": str(manifest_path),
        "core_content_sha256": tree_hash,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic signed AtomLearn release artifact")
    parser.add_argument("source")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--artifact-url", required=True)
    parser.add_argument("--repository", default="panjose/Atom-Learn")
    parser.add_argument("--channel", choices=["prerelease", "stable"], required=True)
    parser.add_argument("--key-id", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--gate-report", required=True)
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    result = build_release(
        Path(args.source),
        Path(args.output_dir),
        tag=args.tag,
        commit_sha=args.commit_sha,
        artifact_url=args.artifact_url,
        key_id=args.key_id,
        private_key_path=Path(args.private_key),
        gate_report_path=Path(args.gate_report),
        channel=args.channel,
        repository=args.repository,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except (ManagerError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
