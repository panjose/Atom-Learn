"""Trust-root and signed immutable release-manifest verification."""

from __future__ import annotations

import base64
import copy
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import MANAGER_VERSION
from .common import ManagerError, atomic_yaml, canonical_json, read_mapping, require_schema, version_tuple


def initialize_trust(root: Path, key_id: str, public_key: str, repository: str) -> dict[str, Any]:
    path = root / "trust.yaml"
    if path.exists():
        raise ManagerError(f"Trust root already exists and will not be overwritten: {path}")
    try:
        decoded = base64.b64decode(public_key, validate=True)
    except ValueError as exc:
        raise ManagerError("Trusted Ed25519 public key must be valid base64") from exc
    if len(decoded) != 32:
        raise ManagerError("Trusted Ed25519 public key must contain exactly 32 bytes")
    value = {
        "kind": "atomlearn.manager-trust",
        "schema_version": 1,
        "revision": 1,
        "repositories": [repository],
        "keys": {key_id: public_key},
    }
    require_schema(value, "trust")
    atomic_yaml(path, value)
    return value


def load_trust(root: Path) -> dict[str, Any]:
    trust = read_mapping(root / "trust.yaml")
    require_schema(trust, "trust")
    return trust


def signature_payload(manifest: dict[str, Any]) -> bytes:
    unsigned = copy.deepcopy(manifest)
    unsigned.pop("signature", None)
    return canonical_json(unsigned)


def validate_release_manifest(
    manifest: dict[str, Any],
    trust: dict[str, Any],
    *,
    requested_channel: str | None = None,
) -> None:
    require_schema(manifest, "release-manifest")
    version = manifest["version"]
    channel = manifest["channel"]
    if requested_channel is not None and channel != requested_channel:
        raise ManagerError(f"Release channel mismatch: requested {requested_channel}, manifest is {channel}")
    if manifest["tag"] != f"v{version}":
        raise ManagerError("Release tag must exactly match the manifest version")
    if version_tuple(MANAGER_VERSION) < version_tuple(manifest["min_manager_version"]):
        raise ManagerError(
            f"Manager {MANAGER_VERSION} is older than required manager {manifest['min_manager_version']}"
        )
    source = manifest["source"]
    if source["repository"] not in trust["repositories"]:
        raise ManagerError(f"Release repository is not trusted: {source['repository']}")
    parsed = urlparse(source["artifact_url"])
    expected_path = f"/{source['repository']}/releases/download/{manifest['tag']}/{manifest['artifact']['filename']}"
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or parsed.path != expected_path:
        raise ManagerError("Artifact URL must identify the immutable tagged GitHub release asset")
    if parsed.query or parsed.fragment or "main" in parsed.path.lower() or "heads/" in parsed.path.lower():
        raise ManagerError("Mutable branch or decorated artifact URLs are forbidden")
    if channel == "stable" and "-" in version:
        raise ManagerError("Stable channel cannot contain a prerelease version")
    manager_artifact = manifest["manager_artifact"]
    expected_manager_prefix = f"atomlearn_manager-{manager_artifact['version']}-"
    if not manager_artifact["filename"].startswith(expected_manager_prefix):
        raise ManagerError("Manager wheel filename and signed manager version disagree")
    if version_tuple(manager_artifact["version"]) < version_tuple(manifest["min_manager_version"]):
        raise ManagerError("Signed Manager wheel is older than the Core minimum manager version")
    signature = manifest["signature"]
    encoded_key = trust["keys"].get(signature["key_id"])
    if encoded_key is None:
        raise ManagerError(f"Release signature key is not trusted: {signature['key_id']}")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded_key, validate=True))
        value = base64.b64decode(signature["value"], validate=True)
        public_key.verify(value, signature_payload(manifest))
    except (ValueError, InvalidSignature) as exc:
        raise ManagerError("Release manifest Ed25519 signature verification failed") from exc


def sign_manifest(manifest: dict[str, Any], key_id: str, private_key: Any) -> dict[str, Any]:
    value = copy.deepcopy(manifest)
    value["signature"] = {"algorithm": "ed25519", "key_id": key_id, "value": "AA=="}
    value["signature"]["value"] = base64.b64encode(private_key.sign(signature_payload(value))).decode("ascii")
    require_schema(value, "release-manifest")
    return value
