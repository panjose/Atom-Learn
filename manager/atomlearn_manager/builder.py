"""Deterministic signed AtomLearn Core release artifact builder."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from . import MANAGER_VERSION
from .common import (
    ManagerError,
    atomic_text,
    is_reparse_or_symlink,
    read_mapping,
    require_schema,
    sha256_bytes,
    sha256_file,
)
from .manifest import key_fingerprint, sign_manifest, validate_trust_bundle
from .runtime import inspect_runtime_bundle
from .verify import ZERO_HASH, content_tree_hash


EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".test-workspaces", ".git"}
REVIEW_RUNTIME_MEMBERS = {
    "core_paths.py",
    "review_scheduler.py",
    "atomlearn_assets/assets/core-manifest.yaml",
    "atomlearn_assets/assets/benchmarks/memory-core-v1.yaml",
    "atomlearn_assets/assets/schemas/review-policy.schema.json",
}


def _require_capability_runtime_payload(runtime_path: Path, required_smoke: list[str]) -> None:
    """Fail a release when a required runtime capability is absent from its Core wheel."""

    if "review" not in required_smoke:
        return
    inspected = inspect_runtime_bundle(runtime_path.resolve())
    recipe = inspected["recipe"]
    wheel_name = recipe["core_wheel"]["filename"]
    wheel_bytes = inspected["files"][f"wheels/{wheel_name}"]
    try:
        with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as wheel:
            members = {item.filename for item in wheel.infolist() if not item.is_dir()}
    except (OSError, zipfile.BadZipFile) as exc:
        raise ManagerError(f"Runtime Core wheel is not a valid ZIP: {wheel_name}") from exc
    missing = sorted(REVIEW_RUNTIME_MEMBERS - members)
    if missing:
        raise ManagerError(
            f"Runtime Core wheel omits payload required by review smoke: {wheel_name}; missing={missing}"
        )


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
    manager_artifact_path: Path,
    runtime_bundle_paths: list[Path],
    trust_bundle_path: Path,
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
    gate_bytes = gate_report_path.read_bytes()
    gate = json.loads(gate_bytes.decode("utf-8"))
    if not isinstance(gate, dict):
        raise ManagerError("Release gate report must be a JSON object")
    require_schema(gate, "release-gate-report")
    if gate["tag"] != tag or gate["commit_sha"] != commit_sha:
        raise ManagerError("Release gate report tag/commit does not match the requested release")
    expected_gate_bytes = json.dumps(gate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if gate_bytes != expected_gate_bytes:
        raise ManagerError("Release gate report must use the canonical JSON bytes emitted by release/gate.py")
    manager_artifact_path = manager_artifact_path.resolve()
    expected_manager_prefix = f"atomlearn_manager-{MANAGER_VERSION}-"
    if (
        not manager_artifact_path.is_file()
        or is_reparse_or_symlink(manager_artifact_path)
        or not manager_artifact_path.name.startswith(expected_manager_prefix)
        or not manager_artifact_path.name.endswith("-py3-none-any.whl")
    ):
        raise ManagerError("Manager artifact must be the expected regular universal wheel")
    if not trust_bundle_path.is_file() or is_reparse_or_symlink(trust_bundle_path):
        raise ManagerError("Release trust bundle must be a regular local file")
    trust_bundle = read_mapping(trust_bundle_path)
    require_schema(trust_bundle, "trust-bundle")
    validate_trust_bundle(trust_bundle)
    if trust_bundle["repository"] != repository:
        raise ManagerError("Release trust bundle repository does not match the release repository")
    signing_key = _private_key(private_key_path)
    signing_public = base64.b64encode(
        signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode("ascii")
    signing_records = [item for item in trust_bundle["keys"] if item["key_id"] == key_id]
    if len(signing_records) != 1 or signing_records[0]["status"] != "active":
        raise ManagerError("Release signing key must be exactly one active trust-bundle key")
    signing_record = signing_records[0]
    if (
        signing_record["public_key"] != signing_public
        or signing_record["fingerprint"] != key_fingerprint(signing_public)
    ):
        raise ManagerError("Release private key does not match the active trust-bundle identity")
    runtime_bundles = []
    runtime_ids: set[str] = set()
    for runtime_path in runtime_bundle_paths:
        runtime_path = runtime_path.resolve()
        inspected_runtime = inspect_runtime_bundle(runtime_path)
        recipe = inspected_runtime["recipe"]
        if recipe["core_version"] != version:
            raise ManagerError("Runtime bundle Core version does not match the release")
        if recipe["id"] in runtime_ids:
            raise ManagerError(f"Duplicate runtime bundle id: {recipe['id']}")
        runtime_ids.add(recipe["id"])
        runtime_bundles.append(
            {
                "id": recipe["id"],
                "platform": recipe["platform"],
                "architecture": recipe["architecture"],
                "python_minor": recipe["python_minor"],
                "filename": runtime_path.name,
                "url": f"https://github.com/{repository}/releases/download/{tag}/{runtime_path.name}",
                "sha256": sha256_file(runtime_path),
                "size": runtime_path.stat().st_size,
                "recipe_sha256": inspected_runtime["recipe_sha256"],
                "core_wheel": recipe["core_wheel"],
            }
        )
    required_matrix = {
        (system, "amd64", python_minor)
        for system in ["linux", "windows"]
        for python_minor in ["3.10", "3.11", "3.12", "3.13"]
    }
    actual_matrix = {(item["platform"], item["architecture"], item["python_minor"]) for item in runtime_bundles}
    if channel == "stable" and actual_matrix != required_matrix:
        missing = sorted(required_matrix - actual_matrix)
        extra = sorted(actual_matrix - required_matrix)
        raise ManagerError(f"Stable release runtime matrix mismatch; missing={missing}, extra={extra}")
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
    capability_ledger = yaml.safe_load(by_name[core["capability_ledger"]].decode("utf-8"))
    if not isinstance(capability_ledger, dict) or not isinstance(capability_ledger.get("required_smoke"), list):
        raise ManagerError("Capability ledger must declare the release smoke matrix")
    for runtime_path in runtime_bundle_paths:
        _require_capability_runtime_payload(runtime_path, capability_ledger["required_smoke"])
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
        "manifest_version": 2,
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
        "manager_artifact": {
            "version": MANAGER_VERSION,
            "filename": manager_artifact_path.name,
            "format": "wheel",
            "sha256": sha256_file(manager_artifact_path),
            "size": manager_artifact_path.stat().st_size,
        },
        "core_manifest_sha256": sha256_bytes(by_name[core_path]),
        "core_content_sha256": tree_hash,
        "gate_report_sha256": sha256_bytes(gate_bytes),
        "min_manager_version": MANAGER_VERSION,
        "schemas": core["schemas"],
        "skill_protocol": {
            "version": core["skill_protocol_version"],
            "entrypoint": "atom-learn/SKILL.md",
            "entrypoint_sha256": sha256_bytes(by_name["atom-learn/SKILL.md"]),
            "bridge_min": 1,
            "bridge_max": 1,
        },
        "runtime_bundles": sorted(runtime_bundles, key=lambda item: item["id"]),
        "capabilities": {
            "ledger_sha256": sha256_bytes(by_name[core["capability_ledger"]]),
            "required_smoke": capability_ledger["required_smoke"],
        },
        "smoke_fixture_sha256": sha256_bytes(by_name[core["smoke_fixtures"]]),
        "trust": {"bundle_version": trust_bundle["bundle_version"], "key_id": key_id},
    }
    manifest = sign_manifest(unsigned, key_id, signing_key)
    atomic_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {
        "ok": True,
        "version": version,
        "artifact": str(artifact_path),
        "artifact_sha256": artifact_hash,
        "manifest": str(manifest_path),
        "core_content_sha256": tree_hash,
        "manager_artifact_sha256": manifest["manager_artifact"]["sha256"],
        "runtime_bundles": [item["filename"] for item in manifest["runtime_bundles"]],
        "runtime_bundle_paths": [str(path.resolve()) for path in runtime_bundle_paths],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic signed AtomLearn release artifact")
    parser.add_argument("source", help="Repository source root containing the versioned Core package")
    parser.add_argument("--output-dir", required=True, help="New artifact destination directory")
    parser.add_argument("--tag", required=True, help="Immutable v<version> Git tag")
    parser.add_argument("--commit-sha", required=True, help="Exact lowercase 40-character release commit")
    parser.add_argument("--artifact-url", required=True, help="Exact tagged GitHub URL for the future Core ZIP asset")
    parser.add_argument("--repository", default="panjose/Atom-Learn", help="Trusted GitHub owner/repository identity")
    parser.add_argument("--channel", choices=["prerelease", "stable"], required=True, help="Signed release channel")
    parser.add_argument("--key-id", required=True, help="Trusted Ed25519 signing-key identifier")
    parser.add_argument("--private-key", required=True, help="Unencrypted Ed25519 PEM or raw base64 private-key file")
    parser.add_argument("--gate-report", required=True, help="Schema-valid CI gate report for this exact tag and commit")
    parser.add_argument("--manager-artifact", required=True, help="Built atomlearn-manager universal wheel")
    parser.add_argument(
        "--trust-bundle", required=True,
        help="Local public trust bundle containing the active key matching --private-key",
    )
    parser.add_argument(
        "--runtime-bundle", action="append", required=True,
        help="Signed-input runtime bundle; repeat for every supported OS/Python target",
    )
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
        manager_artifact_path=Path(args.manager_artifact),
        runtime_bundle_paths=[Path(path) for path in args.runtime_bundle],
        trust_bundle_path=Path(args.trust_bundle),
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
