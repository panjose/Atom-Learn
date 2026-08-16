#!/usr/bin/env python3
"""Validate release contracts and emit a gate report only inside an attested CI job."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "atom-learn" / "SKILL.md"
CORE_MANIFEST = ROOT / "atom-learn" / "assets" / "core-manifest.yaml"
CORE_SCHEMA = ROOT / "atom-learn" / "assets" / "schemas" / "core-manifest.schema.json"
GATE_SCHEMA = ROOT / "manager" / "atomlearn_manager" / "schemas" / "release-gate-report.schema.json"
CAPABILITY_LEDGER = ROOT / "atom-learn" / "assets" / "capabilities.yaml"
CAPABILITY_SCHEMA = ROOT / "atom-learn" / "assets" / "schemas" / "capability-ledger.schema.json"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
REQUIRED_GATES = (
    "full_tests",
    "skill_validator",
    "migration_fixtures",
    "manager_upgrade_tests",
    "security_archive_tests",
    "property_tests",
    "fault_injection",
    "privacy_attacks",
    "replay_compatibility",
)


class GateError(RuntimeError):
    """A stable release gate is incomplete or inconsistent."""


def _schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise GateError(f"Schema must be an object: {path}")
    Draft202012Validator.check_schema(value)
    return value


def _validate(value: dict[str, Any], schema_path: Path, label: str) -> None:
    errors = sorted(Draft202012Validator(_schema(schema_path)).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
            for error in errors[:10]
        )
        raise GateError(f"{label} validation failed: {details}")


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_capability_ledger(core_version: str) -> dict[str, int]:
    ledger = yaml.safe_load(CAPABILITY_LEDGER.read_text(encoding="utf-8"))
    if not isinstance(ledger, dict):
        raise GateError("Capability ledger must be a mapping")
    _validate(ledger, CAPABILITY_SCHEMA, "Capability ledger")
    if ledger["core_version"] != core_version:
        raise GateError("Capability ledger and Core manifest versions disagree")
    identifiers: set[str] = set()
    counts = {"implemented": 0, "experimental": 0, "planned": 0}
    for capability in ledger["capabilities"]:
        identifier = capability["id"]
        if identifier in identifiers:
            raise GateError(f"Capability ledger contains a duplicate id: {identifier}")
        identifiers.add(identifier)
        status = capability["status"]
        counts[status] += 1
        if status == "planned":
            if capability["default_mode"] != "unavailable" or capability["public_claim"]:
                raise GateError(f"Planned capability {identifier} must be unavailable and excluded from public claims")
            if capability["implementation"] or capability["verification"]:
                raise GateError(f"Planned capability {identifier} cannot cite completed implementation or verification")
        else:
            if not capability["implementation"] or not capability["verification"]:
                raise GateError(f"{status.title()} capability {identifier} needs implementation and verification evidence")
        for evidence in capability["implementation"] + capability["verification"]:
            path = (ROOT / evidence["path"]).resolve()
            try:
                path.relative_to(ROOT.resolve())
            except ValueError as exc:
                raise GateError(f"Capability {identifier} evidence escapes the repository: {evidence['path']}") from exc
            if not path.is_file():
                raise GateError(f"Capability {identifier} evidence is missing: {evidence['path']}")
            symbol = evidence.get("symbol")
            if symbol and symbol not in path.read_text(encoding="utf-8"):
                raise GateError(f"Capability {identifier} evidence symbol is missing: {symbol}")
        for documentation in capability["documentation"]:
            if not (ROOT / documentation).is_file():
                raise GateError(f"Capability {identifier} documentation is missing: {documentation}")
        if capability["public_claim"] and not {"README.md", "README.zh-CN.md"} <= set(capability["documentation"]):
            raise GateError(f"Public capability {identifier} must be documented in both READMEs")
    return counts


def repository_version() -> str:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project.get("project", {}).get("version")
    if not isinstance(version, str):
        raise GateError("pyproject.toml has no project version")
    return version


def validate_skill() -> dict[str, Any]:
    lines = SKILL.read_text(encoding="utf-8").splitlines()
    if len(lines) >= 500:
        raise GateError(f"SKILL.md must stay below 500 lines; found {len(lines)}")
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise GateError("SKILL.md must contain YAML frontmatter")
    end = lines[1:].index("---") + 1
    frontmatter = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(frontmatter, dict) or frontmatter.get("name") != "atom-learn":
        raise GateError("SKILL.md frontmatter name must be atom-learn")
    description = frontmatter.get("description")
    if not isinstance(description, str) or len(description.split()) < 12:
        raise GateError("SKILL.md description is missing or not sufficiently descriptive")
    for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", SKILL.read_text(encoding="utf-8")):
        if "://" not in target and not (SKILL.parent / target.split("#", 1)[0]).resolve().exists():
            raise GateError(f"SKILL.md contains a broken relative link: {target}")
    agent = yaml.safe_load((ROOT / "atom-learn" / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    interface = agent.get("interface", {}) if isinstance(agent, dict) else {}
    if interface.get("display_name") != "AtomLearn" or "$atom-learn" not in interface.get("default_prompt", ""):
        raise GateError("agents/openai.yaml is not aligned with the AtomLearn Skill")
    manifest = yaml.safe_load(CORE_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise GateError("Core manifest must be a mapping")
    _validate(manifest, CORE_SCHEMA, "Core manifest")
    if manifest["core_version"] != repository_version():
        raise GateError("Package and Core manifest versions disagree")
    if any(manifest["feature_defaults"].values()):
        raise GateError("Self-evolution v2 features must remain default-off in this release line")
    capability_counts = _validate_capability_ledger(manifest["core_version"])
    migration_fixture = yaml.safe_load(
        (ROOT / "tests" / "fixtures" / "migrations" / "supported-upgrade-paths.yaml").read_text(encoding="utf-8")
    )
    if (
        not isinstance(migration_fixture, dict)
        or migration_fixture.get("target_core_version") != manifest["core_version"]
        or len(migration_fixture.get("upgrade_paths", [])) < 2
        or len({item.get("from_core_version") for item in migration_fixture.get("upgrade_paths", [])}) < 2
    ):
        raise GateError("Migration fixture must declare two distinct paths into the current Core")
    if migration_fixture.get("schema_edges") == [] and any(
        compatibility.get("read") != [1] or compatibility.get("write") != 1
        for compatibility in manifest["schemas"].values()
    ):
        raise GateError("Migration fixture omits schema edges that the Core manifest requires")
    privacy_fixture = json.loads(
        (ROOT / "tests" / "fixtures" / "security" / "capsule-attacks.json").read_text(encoding="utf-8")
    )
    attack_names = {item.get("name") for item in privacy_fixture.get("cases", [])}
    required_attacks = {
        "raw_free_text",
        "nested_free_text",
        "windows_path",
        "posix_path",
        "source_url",
        "source_doi",
        "email",
        "uuid",
        "precise_timestamp",
        "atom_identifier",
    }
    if not required_attacks <= attack_names:
        raise GateError("Capsule privacy fixture does not cover every required attack class")
    return {
        "ok": True,
        "skill_lines": len(lines),
        "core_version": manifest["core_version"],
        "feature_defaults": manifest["feature_defaults"],
        "capabilities": capability_counts,
    }


def write_report(tag: str, commit_sha: str, output: Path) -> dict[str, Any]:
    if os.environ.get("ATOMLEARN_RELEASE_GATES_PASSED") != "1":
        raise GateError("Gate report creation requires the attested release CI environment")
    if not TAG.fullmatch(tag) or tag != f"v{repository_version()}":
        raise GateError("Release tag must exactly match the package version")
    if not COMMIT.fullmatch(commit_sha):
        raise GateError("Release commit must be a lowercase 40-character SHA")
    validate_skill()
    report = {
        "kind": "atomlearn.release-gate-report",
        "schema_version": 1,
        "tag": tag,
        "commit_sha": commit_sha,
        "python": {
            "linux": ["3.10", "3.11", "3.12", "3.13"],
            "windows": ["3.10", "3.11", "3.12", "3.13"],
        },
        "gates": {name: True for name in REQUIRED_GATES},
    }
    _validate(report, GATE_SCHEMA, "Release gate report")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(_canonical_json(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate AtomLearn stable-release quality gates")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-skill", help="Validate Skill metadata links Core contract and rollout defaults")
    write = sub.add_parser("write", help="Write a schema-valid report after the CI matrix succeeds")
    write.add_argument("--tag", required=True, help="Immutable release tag matching the package version")
    write.add_argument("--commit-sha", required=True, help="Exact lowercase Git commit SHA attested by CI")
    write.add_argument("--output", required=True, help="New report path; existing files are never overwritten")
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "validate-skill":
        return validate_skill()
    if args.command == "write":
        return write_report(args.tag, args.commit_sha, Path(args.output).resolve(strict=False))
    raise GateError(f"Unhandled command: {args.command}")


def main() -> int:
    try:
        print(json.dumps(run(), ensure_ascii=False, indent=2))
        return 0
    except (GateError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
