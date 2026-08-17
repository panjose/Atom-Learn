"""Trust-root and signed immutable release-manifest verification."""

from __future__ import annotations

import base64
import copy
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from . import MANAGER_VERSION
from .common import ManagerError, atomic_yaml, canonical_json, read_mapping, require_schema, version_tuple


def key_fingerprint(public_key: str) -> str:
    try:
        decoded = base64.b64decode(public_key, validate=True)
    except ValueError as exc:
        raise ManagerError("Trusted Ed25519 public key must be valid base64") from exc
    if len(decoded) != 32:
        raise ManagerError("Trusted Ed25519 public key must contain exactly 32 bytes")
    return "sha256:" + hashlib.sha256(decoded).hexdigest()


def initialize_trust(root: Path, key_id: str, public_key: str, repository: str) -> dict[str, Any]:
    path = root / "trust.yaml"
    if path.exists():
        raise ManagerError(f"Trust root already exists and will not be overwritten: {path}")
    fingerprint = key_fingerprint(public_key)
    value = {
        "kind": "atomlearn.manager-trust",
        "schema_version": 2,
        "revision": 1,
        "bundle_version": 1,
        "trust_level": "pinned",
        "repositories": [repository],
        "keys": {
            key_id: {
                "algorithm": "ed25519",
                "public_key": public_key,
                "fingerprint": fingerprint,
                "status": "active",
            }
        },
    }
    require_schema(value, "trust-v2")
    atomic_yaml(path, value)
    return value


def _bundle_payload(bundle: dict[str, Any]) -> bytes:
    unsigned = copy.deepcopy(bundle)
    unsigned.pop("signature", None)
    return canonical_json(unsigned)


def validate_trust_bundle(bundle: dict[str, Any]) -> None:
    identifiers: set[str] = set()
    active = 0
    for key in bundle["keys"]:
        if key["key_id"] in identifiers:
            raise ManagerError(f"Trust bundle has duplicate key id: {key['key_id']}")
        identifiers.add(key["key_id"])
        if key_fingerprint(key["public_key"]) != key["fingerprint"]:
            raise ManagerError(f"Trust bundle fingerprint mismatch: {key['key_id']}")
        active += int(key["status"] == "active")
    if active < 1:
        raise ManagerError("Trust bundle must retain at least one active key")


def trust_from_bundle(bundle_path: Path, expected_fingerprint: str | None) -> dict[str, Any]:
    """Validate a local bundle and derive trust state without writing it."""

    bundle = read_mapping(bundle_path)
    require_schema(bundle, "trust-bundle")
    validate_trust_bundle(bundle)
    fingerprints = {key["fingerprint"] for key in bundle["keys"] if key["status"] == "active"}
    if expected_fingerprint is not None and expected_fingerprint not in fingerprints:
        raise ManagerError("Pinned trust-bundle fingerprint does not match an active key")
    value = {
        "kind": "atomlearn.manager-trust",
        "schema_version": 2,
        "revision": 1,
        "bundle_version": bundle["bundle_version"],
        "trust_level": "pinned" if expected_fingerprint else "unverified",
        "repositories": [bundle["repository"]],
        "keys": {
            key["key_id"]: {name: key[name] for name in ["algorithm", "public_key", "fingerprint", "status"]}
            for key in bundle["keys"]
        },
    }
    require_schema(value, "trust-v2")
    return value


def initialize_trust_bundle(root: Path, bundle_path: Path, expected_fingerprint: str | None) -> dict[str, Any]:
    path = root / "trust.yaml"
    if path.exists():
        raise ManagerError(f"Trust root already exists and will not be overwritten: {path}")
    value = trust_from_bundle(bundle_path, expected_fingerprint)
    atomic_yaml(path, value)
    return value


def load_trust(root: Path) -> dict[str, Any]:
    trust = read_mapping(root / "trust.yaml")
    require_schema(trust, "trust-v2" if trust.get("schema_version") == 2 else "trust")
    return trust


def accept_tofu(root: Path, fingerprint: str, confirmed: bool) -> dict[str, Any]:
    """Explicitly acknowledge a first-seen key without mislabeling it as out-of-band pinning."""
    if not confirmed:
        raise ManagerError("TOFU acceptance requires --confirmed")
    current = load_trust(root)
    if current.get("schema_version") != 2 or current.get("trust_level") != "unverified":
        raise ManagerError("TOFU acceptance is limited to an unverified trust-bundle initialization")
    active_fingerprints = {
        value["fingerprint"] for value in current["keys"].values() if value["status"] == "active"
    }
    if fingerprint not in active_fingerprints:
        raise ManagerError("Accepted TOFU fingerprint does not match an active trust key")
    value = copy.deepcopy(current)
    value["revision"] += 1
    value["trust_level"] = "verified_tofu"
    require_schema(value, "trust-v2")
    atomic_yaml(root / "trust.yaml", value)
    return value


def pin_trust(root: Path, fingerprint: str, confirmed: bool) -> dict[str, Any]:
    """Promote an existing versioned trust root using an out-of-band fingerprint."""

    if not confirmed:
        raise ManagerError("Out-of-band trust pinning requires --confirmed")
    current = load_trust(root)
    if current.get("schema_version") != 2:
        raise ManagerError("Legacy trust roots must be reinitialized before out-of-band pinning")
    active_fingerprints = {
        item["fingerprint"] for item in current["keys"].values() if item["status"] == "active"
    }
    if fingerprint not in active_fingerprints:
        raise ManagerError("Pinned fingerprint does not match an active trust key")
    if current["trust_level"] == "pinned":
        return current
    value = copy.deepcopy(current)
    value["trust_level"] = "pinned"
    value["revision"] += 1
    require_schema(value, "trust-v2")
    atomic_yaml(root / "trust.yaml", value)
    return value


def rotate_trust(root: Path, bundle_path: Path, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ManagerError("Trust rotation requires --confirmed")
    current = load_trust(root)
    if current.get("schema_version") != 2:
        raise ManagerError("Legacy trust roots must be re-pinned before signed rotation")
    bundle = read_mapping(bundle_path)
    require_schema(bundle, "trust-bundle")
    validate_trust_bundle(bundle)
    if bundle["repository"] not in current["repositories"]:
        raise ManagerError("Trust bundle repository is not currently trusted")
    if bundle["previous_bundle_version"] != current["bundle_version"] or bundle["bundle_version"] <= current["bundle_version"]:
        raise ManagerError("Trust bundle does not form the next monotonic rotation")
    signature = bundle.get("signature")
    if not isinstance(signature, dict):
        raise ManagerError("Trust rotation bundle requires a signature from an existing key")
    signing_key = current["keys"].get(signature["key_id"])
    if not signing_key or signing_key["status"] == "revoked":
        raise ManagerError("Trust rotation signature key is not currently valid")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(signing_key["public_key"], validate=True)).verify(
            base64.b64decode(signature["value"], validate=True), _bundle_payload(bundle)
        )
    except (ValueError, InvalidSignature) as exc:
        raise ManagerError("Trust rotation signature verification failed") from exc
    value = {
        "kind": "atomlearn.manager-trust",
        "schema_version": 2,
        "revision": current["revision"] + 1,
        "bundle_version": bundle["bundle_version"],
        "trust_level": current["trust_level"],
        "repositories": current["repositories"],
        "keys": {
            key["key_id"]: {name: key[name] for name in ["algorithm", "public_key", "fingerprint", "status"]}
            for key in bundle["keys"]
        },
    }
    require_schema(value, "trust-v2")
    atomic_yaml(root / "trust.yaml", value)
    return value


def break_glass_trust(
    root: Path,
    bundle_path: Path,
    expected_fingerprint: str,
    confirmed: bool,
) -> dict[str, Any]:
    """Replace a compromised trust root using an independently pinned replacement key."""
    if not confirmed:
        raise ManagerError("Break-glass trust replacement requires --confirmed")
    current = load_trust(root)
    if current.get("schema_version") != 2:
        raise ManagerError("Break-glass replacement requires a versioned trust root")
    bundle = read_mapping(bundle_path)
    require_schema(bundle, "trust-bundle")
    validate_trust_bundle(bundle)
    if bundle["repository"] not in current["repositories"] or bundle["bundle_version"] <= current["bundle_version"]:
        raise ManagerError("Break-glass bundle must advance the currently trusted repository")
    active = {key["fingerprint"] for key in bundle["keys"] if key["status"] == "active"}
    current_active = {
        key["fingerprint"] for key in current["keys"].values() if key["status"] == "active"
    }
    if expected_fingerprint not in active:
        raise ManagerError("Break-glass fingerprint does not match an active replacement key")
    if expected_fingerprint in current_active:
        raise ManagerError("Break-glass replacement must pin a new key, not the potentially compromised active key")
    value = {
        "kind": "atomlearn.manager-trust",
        "schema_version": 2,
        "revision": current["revision"] + 1,
        "bundle_version": bundle["bundle_version"],
        "trust_level": "pinned",
        "repositories": current["repositories"],
        "keys": {
            key["key_id"]: {name: key[name] for name in ["algorithm", "public_key", "fingerprint", "status"]}
            for key in bundle["keys"]
        },
    }
    require_schema(value, "trust-v2")
    atomic_yaml(root / "trust.yaml", value)
    return value


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
    require_schema(manifest, "release-manifest-v2" if manifest.get("manifest_version") == 2 else "release-manifest")
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
    if manifest.get("manifest_version") == 2:
        if manifest["skill_protocol"]["bridge_min"] > manifest["skill_protocol"]["version"] or manifest["skill_protocol"]["bridge_max"] < manifest["skill_protocol"]["version"]:
            raise ManagerError("Release Skill protocol is outside its declared bridge compatibility range")
        runtime_coordinates: set[tuple[str, str, str, str]] = set()
        runtime_profile_names: set[str] = set()
        for runtime in manifest["runtime_bundles"]:
            profile = runtime.get("profile")
            profile_name = profile.get("name", "base") if isinstance(profile, dict) else "base"
            coordinate = (runtime["platform"], runtime["architecture"], runtime["python_minor"], profile_name)
            runtime_profile_names.add(profile_name)
            if coordinate in runtime_coordinates:
                raise ManagerError(f"Release has a duplicate runtime coordinate: {coordinate}")
            runtime_coordinates.add(coordinate)
            expected_runtime_id = (
                f"{version}-{runtime['platform']}-{runtime['architecture']}-py"
                f"{runtime['python_minor'].replace('.', '')}"
            )
            if isinstance(profile, dict):
                if runtime["core_version"] != version:
                    raise ManagerError("Runtime profile Core version does not match the release")
                expected_runtime_id += (
                    f"-{profile_name}-{runtime['profile_hash'].removeprefix('sha256:')[:12]}"
                )
                if profile["model_policy"]["silent_download"] is not False:
                    raise ManagerError("Runtime profile may not permit silent model downloads")
                if profile["model_policy"]["mode"] == "pinned_local":
                    model_lock = runtime.get("model_lock")
                    if not isinstance(model_lock, dict) or model_lock.get("trust_remote_code") is not False:
                        raise ManagerError("Semantic runtime profile lacks a safe signed model lock")
                smoke = runtime.get("smoke")
                if not isinstance(smoke, dict) or smoke.get("profile") != profile_name:
                    raise ManagerError("Runtime profile smoke identity is missing or inconsistent")
                if smoke.get("status") == "passed" and (
                    smoke.get("platform"), smoke.get("architecture"), smoke.get("python_minor")
                ) != (runtime["platform"], runtime["architecture"], runtime["python_minor"]):
                    raise ManagerError("Runtime profile smoke target disagrees with its signed runtime coordinate")
            if runtime["id"] != expected_runtime_id or runtime["filename"] != f"atomlearn-runtime-{expected_runtime_id}.zip":
                raise ManagerError("Runtime ID or filename does not match the release target coordinates")
            if not runtime["core_wheel"]["filename"].startswith(
                (f"atom_learn-{version}-", f"atom-learn-{version}-")
            ):
                raise ManagerError("Runtime Core wheel filename does not match the release version")
            runtime_url = urlparse(runtime["url"])
            expected_runtime_path = f"/{source['repository']}/releases/download/{manifest['tag']}/{runtime['filename']}"
            if runtime_url.scheme != "https" or runtime_url.netloc.lower() != "github.com" or runtime_url.path != expected_runtime_path:
                raise ManagerError("Runtime URL must identify an immutable tagged GitHub release asset")
            if runtime_url.query or runtime_url.fragment:
                raise ManagerError("Decorated runtime URLs are forbidden")
        stable_profiles = manifest["capabilities"].get("stable_runtime_profiles")
        if stable_profiles is not None and channel == "stable":
            expected_profiles = set(stable_profiles)
            if runtime_profile_names != expected_profiles:
                raise ManagerError("Stable runtime profile set disagrees with the signed delivery claim")
            required_matrix = {
                (system, "amd64", python_minor)
                for system in ["linux", "windows"]
                for python_minor in ["3.10", "3.11", "3.12", "3.13"]
            }
            for profile_name in expected_profiles:
                actual_matrix = {
                    (runtime["platform"], runtime["architecture"], runtime["python_minor"])
                    for runtime in manifest["runtime_bundles"]
                    if (runtime.get("profile") or {}).get("name") == profile_name
                    and runtime.get("smoke", {}).get("status") == "passed"
                    and runtime.get("smoke", {}).get("platform_verified") is True
                }
                if actual_matrix != required_matrix:
                    raise ManagerError(f"Stable runtime profile matrix or smoke report is incomplete: {profile_name}")
    if channel == "stable" and "-" in version:
        raise ManagerError("Stable channel cannot contain a prerelease version")
    manager_artifact = manifest["manager_artifact"]
    expected_manager_prefix = f"atomlearn_manager-{manager_artifact['version']}-"
    if not manager_artifact["filename"].startswith(expected_manager_prefix):
        raise ManagerError("Manager wheel filename and signed manager version disagree")
    if version_tuple(manager_artifact["version"]) < version_tuple(manifest["min_manager_version"]):
        raise ManagerError("Signed Manager wheel is older than the Core minimum manager version")
    signature = manifest["signature"]
    key_record = trust["keys"].get(signature["key_id"])
    if key_record is None:
        raise ManagerError(f"Release signature key is not trusted: {signature['key_id']}")
    if trust.get("schema_version") == 2:
        if key_record["status"] == "revoked":
            raise ManagerError(f"Release signature key is revoked: {signature['key_id']}")
        encoded_key = key_record["public_key"]
        if manifest.get("manifest_version") == 2:
            if manifest["trust"]["key_id"] != signature["key_id"]:
                raise ManagerError("Manifest trust key and signature key disagree")
            if manifest["trust"]["bundle_version"] > trust["bundle_version"]:
                raise ManagerError("Release requires a newer pinned trust bundle")
    else:
        encoded_key = key_record
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
    require_schema(value, "release-manifest-v2" if value.get("manifest_version") == 2 else "release-manifest")
    return value
