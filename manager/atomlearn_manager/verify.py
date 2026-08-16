"""Reject hostile archives and verify every release identity and hash boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .common import ManagerError, is_reparse_or_symlink, require_schema, sha256_bytes, sha256_file


MAX_FILES = 20_000
MAX_UNCOMPRESSED = 512 * 1024 * 1024
MAX_RATIO = 200
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
ZERO_HASH = "sha256:" + "0" * 64
REQUIRED_FILES = {
    "pyproject.toml",
    "README.md",
    "README.zh-CN.md",
    "atom-learn/SKILL.md",
    "atom-learn/agents/openai.yaml",
    "atom-learn/assets/core-manifest.yaml",
    "atom-learn/scripts/atomlearn.py",
    "release/gate-report.json",
}


def normalized_core_manifest(content: bytes) -> bytes:
    try:
        value = yaml.safe_load(content.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ManagerError("Embedded Core manifest is not valid UTF-8 YAML") from exc
    if not isinstance(value, dict):
        raise ManagerError("Embedded Core manifest must be a mapping")
    value["artifact_sha256"] = ZERO_HASH
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=100).encode("utf-8")


def content_tree_hash(files: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative, content in sorted(files, key=lambda item: item[0]):
        normalized = normalized_core_manifest(content) if relative == "atom-learn/assets/core-manifest.yaml" else content
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(normalized)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _safe_member(info: zipfile.ZipInfo) -> tuple[str, bool]:
    name = info.filename
    if not name or "\x00" in name or "\\" in name:
        raise ManagerError(f"Unsafe archive member name: {name!r}")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise ManagerError(f"Absolute archive path is forbidden: {name}")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ManagerError(f"Archive traversal or ambiguous segment is forbidden: {name}")
    for part in path.parts:
        if ":" in part or part.endswith((" ", ".")) or part.split(".", 1)[0].upper() in WINDOWS_RESERVED:
            raise ManagerError(f"Archive member is unsafe on Windows: {name}")
    mode = (info.external_attr >> 16) & 0xFFFF
    if stat.S_ISLNK(mode):
        raise ManagerError(f"Archive links are forbidden: {name}")
    if info.flag_bits & 0x1:
        raise ManagerError(f"Encrypted archive entries are forbidden: {name}")
    is_dir = info.is_dir() or name.endswith("/")
    return name.rstrip("/"), is_dir


def inspect_archive(path: Path, version: str) -> dict[str, Any]:
    if not path.is_file() or is_reparse_or_symlink(path):
        raise ManagerError(f"Release artifact must be a regular non-link file: {path}")
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ManagerError(f"Release artifact is not a valid ZIP: {path}") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise ManagerError("Release archive file count is empty or exceeds the safety limit")
        seen: set[str] = set()
        file_names: set[str] = set()
        directories: set[str] = set()
        total = 0
        top_levels: set[str] = set()
        safe: list[tuple[zipfile.ZipInfo, str, bool]] = []
        for info in infos:
            name, is_dir = _safe_member(info)
            folded = name.casefold()
            if folded in seen:
                raise ManagerError(f"Duplicate or case-colliding archive member: {name}")
            seen.add(folded)
            top_levels.add(PurePosixPath(name).parts[0])
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ManagerError("Release archive exceeds the uncompressed-size safety limit")
            if info.compress_size == 0 and info.file_size > 0 or (
                info.compress_size > 0 and info.file_size / info.compress_size > MAX_RATIO
            ):
                raise ManagerError(f"Suspicious compression ratio in archive member: {name}")
            target_set = directories if is_dir else file_names
            target_set.add(folded)
            safe.append((info, name, is_dir))
        expected_root = f"atomlearn-{version}"
        if top_levels != {expected_root}:
            raise ManagerError(f"Release archive must contain exactly one root directory named {expected_root}")
        for file_name in file_names:
            pieces = file_name.split("/")
            for index in range(1, len(pieces)):
                if "/".join(pieces[:index]) in file_names:
                    raise ManagerError("Archive contains a file/directory prefix collision")
        files: list[tuple[str, bytes]] = []
        for info, name, is_dir in safe:
            if is_dir:
                continue
            relative = name.removeprefix(expected_root + "/")
            with archive.open(info, "r") as handle:
                content = handle.read()
            files.append((relative, content))
        names = {name for name, _ in files}
        missing = sorted(REQUIRED_FILES - names)
        if missing:
            raise ManagerError("Release archive is missing required files: " + ", ".join(missing))
        return {"root": expected_root, "files": files, "file_count": len(files), "uncompressed_size": total}


def verify_release(manifest: dict[str, Any], artifact: Path) -> dict[str, Any]:
    expected = manifest["artifact"]
    if artifact.name != expected["filename"]:
        raise ManagerError("Artifact filename does not match the signed release manifest")
    if artifact.stat().st_size != expected["size"]:
        raise ManagerError("Artifact size does not match the signed release manifest")
    if sha256_file(artifact) != expected["sha256"]:
        raise ManagerError("Artifact SHA-256 does not match the signed release manifest")
    inspected = inspect_archive(artifact, manifest["version"])
    files = dict(inspected["files"])
    core_bytes = files["atom-learn/assets/core-manifest.yaml"]
    if sha256_bytes(core_bytes) != manifest["core_manifest_sha256"]:
        raise ManagerError("Embedded Core manifest hash does not match the release manifest")
    if sha256_bytes(files["release/gate-report.json"]) != manifest["gate_report_sha256"]:
        raise ManagerError("Embedded release gate report hash does not match the release manifest")
    try:
        gate = json.loads(files["release/gate-report.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagerError("Embedded release gate report is not valid UTF-8 JSON") from exc
    if not isinstance(gate, dict):
        raise ManagerError("Embedded release gate report must be an object")
    require_schema(gate, "release-gate-report")
    if gate["tag"] != manifest["tag"] or gate["commit_sha"] != manifest["source"]["commit_sha"]:
        raise ManagerError("Embedded gate report does not attest this release tag and commit")
    core = yaml.safe_load(core_bytes.decode("utf-8"))
    if not isinstance(core, dict):
        raise ManagerError("Embedded Core manifest is not a mapping")
    require_schema(core, "core-manifest")
    if core["core_version"] != manifest["version"] or core["release_channel"] != manifest["channel"]:
        raise ManagerError("Package, Core manifest, and release manifest versions/channels disagree")
    if core["schemas"] != manifest["schemas"]:
        raise ManagerError("Core and release schema compatibility declarations disagree")
    if manifest.get("manifest_version") == 2:
        protocol = manifest["skill_protocol"]
        if core["skill_protocol_version"] != protocol["version"]:
            raise ManagerError("Core and release Skill protocol versions disagree")
        if sha256_bytes(files[protocol["entrypoint"]]) != protocol["entrypoint_sha256"]:
            raise ManagerError("Signed Skill entry point hash does not match the Core artifact")
        ledger_path = core["capability_ledger"]
        fixture_path = core["smoke_fixtures"]
        if ledger_path not in files or sha256_bytes(files[ledger_path]) != manifest["capabilities"]["ledger_sha256"]:
            raise ManagerError("Signed capability ledger is missing or inconsistent")
        if fixture_path not in files or sha256_bytes(files[fixture_path]) != manifest["smoke_fixture_sha256"]:
            raise ManagerError("Signed smoke fixture bundle is missing or inconsistent")
    tree_hash = content_tree_hash(inspected["files"])
    if tree_hash != manifest["core_content_sha256"] or core["artifact_sha256"] != tree_hash:
        raise ManagerError("Normalized Core content hash is inconsistent")
    project = tomllib.loads(files["pyproject.toml"].decode("utf-8"))
    if project.get("project", {}).get("version") != manifest["version"]:
        raise ManagerError("Package version does not match the signed release manifest")
    inspected.update({"core_manifest": core, "tree_hash": tree_hash})
    return inspected


def safe_extract(artifact: Path, destination: Path, version: str) -> Path:
    if destination.exists():
        raise ManagerError(f"Extraction destination already exists: {destination}")
    inspected = inspect_archive(artifact, version)
    destination.mkdir(parents=True, exist_ok=False)
    root = destination / inspected["root"]
    for relative, content in inspected["files"]:
        target = root / Path(relative)
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to(root.resolve(strict=False))
        except ValueError as exc:  # pragma: no cover - second-line defense
            raise ManagerError(f"Archive member escapes extraction root: {relative}") from exc
        if any(is_reparse_or_symlink(parent) for parent in [root, *target.parents] if parent.exists()):
            raise ManagerError(f"Extraction path crosses a link or reparse point: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    return root
