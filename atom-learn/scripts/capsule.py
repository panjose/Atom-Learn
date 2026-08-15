#!/usr/bin/env python3
"""Privacy-minimized local Evolution Capsule build, preview, export, and ingest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import secrets
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from atomlearn import iso
from evolution import EvolutionEngine, EvolutionError
from platform_state import CORE_ROOT, FileLock, PlatformStateError, atomic_text, atomic_yaml, core_version, resolve_user_data_root
from user_profile import json_lines, serialize_json_lines


CAPSULE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "evolution-capsule.schema.json"
LINT_SCHEMA = CORE_ROOT / "assets" / "schemas" / "capsule-lint-receipt.schema.json"
PREVIEW_SCHEMA = CORE_ROOT / "assets" / "schemas" / "capsule-preview-receipt.schema.json"
TRIAGE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "capsule-triage.schema.json"
FIXTURE_SCHEMA = CORE_ROOT / "assets" / "schemas" / "maintainer-failure-fixture.schema.json"
LINT_CHECKS = [
    "schema",
    "no_free_text",
    "no_content",
    "no_paths",
    "no_urls_or_dois",
    "no_stable_ids",
    "coarse_time",
    "minimum_aggregation",
]
WINDOWS_PATH = re.compile(r"(?:^|\s)[A-Za-z]:[\\/]|^\\\\")
POSIX_PATH = re.compile(r"^/(?:Users|home|var|tmp|opt|etc|mnt)/")
URL = re.compile(r"(?:https?|ftp)://|\bwww\.", re.IGNORECASE)
DOI = re.compile(r"\b10\.\d{4,9}/\S+", re.IGNORECASE)
EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)


class CapsuleError(RuntimeError):
    """A user-correctable capsule privacy or workflow error."""


def _schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):  # pragma: no cover - repository contract guard
        raise CapsuleError(f"Schema is not an object: {path}")
    return value


def _schema_errors(value: dict[str, Any], path: Path) -> list[str]:
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(Draft202012Validator(_schema(path)).iter_errors(value), key=lambda item: list(item.path))
    ]


def load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CapsuleError(f"Capsule file not found: {path}")
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CapsuleError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CapsuleError(f"Expected a mapping in {path}")
    return value


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def content_hash(value: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def receipt_path(capsule_path: Path, kind: str) -> Path:
    return capsule_path.with_name(capsule_path.name + f".{kind}.json")


def privacy_lint(capsule: dict[str, Any]) -> list[str]:
    errors = _schema_errors(capsule, CAPSULE_SCHEMA)
    for path, value in _walk_strings(capsule):
        if WINDOWS_PATH.search(value) or POSIX_PATH.search(value):
            errors.append(f"{path}: local or absolute paths are forbidden")
        if URL.search(value):
            errors.append(f"{path}: URLs are forbidden")
        if DOI.search(value):
            errors.append(f"{path}: DOI values are forbidden")
        if EMAIL.search(value):
            errors.append(f"{path}: email or account identifiers are forbidden")
        if TIMESTAMP.match(value):
            errors.append(f"{path}: precise timestamps are forbidden")
        if UUID.search(value):
            errors.append(f"{path}: stable UUID-shaped identifiers are forbidden")
    privacy = capsule.get("privacy", {})
    if isinstance(privacy, dict) and any(
        privacy.get(key) is not False
        for key in ["raw_messages_included", "source_content_included", "stable_user_id_included", "local_paths_included"]
    ):
        errors.append("privacy: every content and stable-identifier flag must be false")
    if capsule.get("metrics", {}).get("occurrence_bucket") == "2":
        errors.append("metrics.occurrence_bucket: two observations are too small for privacy-safe export")
    return sorted(set(errors))


def _walk_strings(value: Any, path: str = "<root>") -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            result.extend(_walk_strings(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.extend(_walk_strings(child, f"{path}[{index}]"))
    elif isinstance(value, str):
        result.append((path, value))
    return result


def require_private(capsule: dict[str, Any]) -> None:
    errors = privacy_lint(capsule)
    if errors:
        raise CapsuleError("Capsule privacy lint failed:\n- " + "\n- ".join(errors))


def lint_receipt(capsule: dict[str, Any]) -> dict[str, Any]:
    require_private(capsule)
    receipt = {
        "kind": "atomlearn.capsule-lint-receipt",
        "schema_version": 1,
        "capsule_id": capsule["capsule_id"],
        "capsule_hash": content_hash(capsule),
        "checks": list(LINT_CHECKS),
        "linted_at": iso(),
    }
    errors = _schema_errors(receipt, LINT_SCHEMA)
    if errors:  # pragma: no cover - construction guard
        raise CapsuleError("Lint receipt is invalid:\n- " + "\n- ".join(errors))
    return receipt


def _occurrence_bucket(count: int) -> str:
    if count <= 2:
        return "2"
    if count <= 5:
        return "3_to_5"
    if count <= 10:
        return "6_to_10"
    return "over_10"


def _window_bucket(count: int) -> str:
    if count <= 4:
        return "sessions_2_to_4"
    if count <= 10:
        return "sessions_5_to_10"
    if count <= 25:
        return "sessions_11_to_25"
    return "sessions_over_25"


def _attempt_bucket(delta: float) -> str:
    if delta <= 0:
        return "none"
    if delta < 1:
        return "under_1"
    if delta <= 2:
        return "1_to_2"
    return "over_2"


def _classification(proposal: dict[str, Any], metrics: dict[str, Any]) -> tuple[str, str, str]:
    proposal_type = proposal.get("type")
    target_metrics = [
        metrics.get("atoms", {}).get(atom_id, {}) for atom_id in proposal.get("target_atom_ids", [])
    ]
    has_review_failure = any(item.get("review_failures", 0) for item in target_metrics)
    last_monitor = (proposal.get("monitoring") or [{}])[-1]
    if proposal_type == "teaching_strategy":
        failure = "strategy_underperformance" if last_monitor.get("outcome") == "failed" else "repeated_mastery_failure"
        return failure, "teaching", "teaching_policy"
    if proposal_type == "adjust_review_intervals":
        return "delayed_review_failure", "review", "review_policy"
    if proposal_type == "adjust_mastery":
        return (
            "delayed_review_failure" if has_review_failure else "repeated_mastery_failure",
            "review" if has_review_failure else "teaching",
            "review_policy" if has_review_failure else "teaching_policy",
        )
    if proposal_type in {"add_dependency", "remove_dependency"}:
        return "repeated_blocking_prerequisite", "concept_routing", "routing_policy"
    if proposal_type in {"split_atom", "merge_atoms"}:
        return "workflow_friction", "atomization", "atomization_policy"
    if proposal_type == "patch_skill":
        return "workflow_friction", "update", "core_patch"
    raise CapsuleError(f"Proposal type cannot be converted to a Capsule: {proposal_type!r}")


def build_capsule(
    workspace_path: str,
    proposal_id: str,
    *,
    data_dir: str | Path | None = None,
    reproduction_fixture_hash: str | None = None,
) -> dict[str, Any]:
    engine = EvolutionEngine.load(workspace_path)
    errors = engine.validate()
    if errors:
        raise CapsuleError("Cannot build from invalid evolution state:\n- " + "\n- ".join(errors))
    proposal = engine.find_proposal(proposal_id)
    observations = sorted(set(str(item) for item in proposal.get("observations", [])))
    metrics = engine.compute_metrics()
    failure_type, feature, candidate_type = _classification(proposal, metrics)
    targets = [metrics.get("atoms", {}).get(atom_id, {}) for atom_id in proposal.get("target_atom_ids", [])]
    attempts = [float(item.get("attempts", 0)) for item in targets if isinstance(item.get("attempts"), int)]
    attempt_delta = (sum(max(0.0, item - 1.0) for item in attempts) / len(attempts)) if attempts else 0.0
    review_counts = [int(item.get("review_failures", 0)) for item in targets]
    review_observed = any(
        evidence.get("kind") == "review"
        for evidence in engine.workspace.evidence.get("items", [])
        if evidence.get("atom_id") in proposal.get("target_atom_ids", [])
    )
    last_monitor = (proposal.get("monitoring") or [{}])[-1]
    if not review_observed:
        delayed = "not_observed"
    elif any(review_counts):
        delayed = "worsened"
    elif last_monitor.get("outcome") == "passed":
        delayed = "improved"
    else:
        delayed = "unchanged"
    session_count = int(metrics.get("system", {}).get("adaptation_session_count", 0))
    coarse_count = max(2, session_count, len(observations))
    capsule = {
        "kind": "atomlearn.evolution-capsule",
        "schema_version": 1,
        "capsule_id": "cap-" + secrets.token_hex(16),
        "core_version": core_version(),
        "failure_type": failure_type,
        "affected_feature": feature,
        "window": _window_bucket(coarse_count),
        "metrics": {
            "occurrence_bucket": _occurrence_bucket(len(observations)),
            "mastery_attempt_delta_bucket": _attempt_bucket(attempt_delta),
            "delayed_review_bucket": delayed,
        },
        "candidate": {"type": candidate_type},
        "privacy": {
            "raw_messages_included": False,
            "source_content_included": False,
            "stable_user_id_included": False,
            "local_paths_included": False,
            "lint_status": "passed",
        },
    }
    if reproduction_fixture_hash is not None:
        capsule["reproduction_fixture_hash"] = reproduction_fixture_hash
    receipt = lint_receipt(capsule)
    root = resolve_user_data_root(data_dir, create=True) / "feedback" / "evolution-capsules"
    drafts = root / "drafts"
    capsule_path = drafts / f"{capsule['capsule_id']}.json"
    markdown_path = drafts / f"{capsule['capsule_id']}.md"
    with FileLock(root / ".capsule.lock"):
        atomic_text(capsule_path, json.dumps(capsule, ensure_ascii=False, indent=2) + "\n")
        atomic_text(markdown_path, render_markdown(capsule))
        atomic_text(receipt_path(capsule_path, "lint"), json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return {
        "capsule": capsule,
        "capsule_hash": receipt["capsule_hash"],
        "capsule_path": str(capsule_path),
        "markdown_path": str(markdown_path),
        "lint_receipt": str(receipt_path(capsule_path, "lint")),
        "next_action": f"Run capsule preview for {capsule_path} before explicit export.",
    }


def render_markdown(capsule: dict[str, Any]) -> str:
    metrics = capsule["metrics"]
    lines = [
        "# AtomLearn Evolution Capsule Preview",
        "",
        "> This is a local, privacy-minimized product-improvement candidate. It has not been uploaded.",
        "",
        f"- Capsule ID: `{capsule['capsule_id']}`",
        f"- Core version: `{capsule['core_version']}`",
        f"- Failure type: `{capsule['failure_type']}`",
        f"- Affected feature: `{capsule['affected_feature']}`",
        f"- Coarse window: `{capsule['window']}`",
        f"- Candidate type: `{capsule['candidate']['type']}`",
        f"- Occurrences: `{metrics['occurrence_bucket']}`",
        f"- Mastery-attempt delta: `{metrics['mastery_attempt_delta_bucket']}`",
        f"- Delayed review: `{metrics.get('delayed_review_bucket', 'not_observed')}`",
        "",
        "## Privacy checks",
        "",
        "- Raw messages: excluded",
        "- Source content: excluded",
        "- Stable user, workspace, source, and Atom identifiers: excluded",
        "- Local paths, URLs, DOI values, and exact timestamps: excluded",
        "",
        "Export is a local file operation only. No submit or telemetry command exists.",
    ]
    return "\n".join(lines) + "\n"


def lint_file(path: Path) -> dict[str, Any]:
    capsule = load_mapping(path)
    receipt = lint_receipt(capsule)
    target = receipt_path(path, "lint")
    atomic_text(target, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return {"ok": True, "capsule_hash": receipt["capsule_hash"], "receipt": str(target), "checks": receipt["checks"]}


def preview_file(path: Path) -> dict[str, Any]:
    capsule = load_mapping(path)
    require_private(capsule)
    capsule_hash = content_hash(capsule)
    receipt = {
        "kind": "atomlearn.capsule-preview-receipt",
        "schema_version": 1,
        "capsule_id": capsule["capsule_id"],
        "capsule_hash": capsule_hash,
        "previewed_at": iso(),
    }
    errors = _schema_errors(receipt, PREVIEW_SCHEMA)
    if errors:  # pragma: no cover - construction guard
        raise CapsuleError("Preview receipt is invalid:\n- " + "\n- ".join(errors))
    target = receipt_path(path, "preview")
    atomic_text(target, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return {
        "capsule": capsule,
        "capsule_hash": capsule_hash,
        "markdown": render_markdown(capsule),
        "preview_receipt": str(target),
        "uploaded": False,
    }


def _validated_receipt(path: Path, schema_path: Path, capsule: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        raise CapsuleError(f"Required local receipt not found: {path}")
    receipt = load_mapping(path)
    errors = _schema_errors(receipt, schema_path)
    if errors:
        raise CapsuleError("Local receipt is invalid:\n- " + "\n- ".join(errors))
    if receipt["capsule_id"] != capsule["capsule_id"] or receipt["capsule_hash"] != content_hash(capsule):
        raise CapsuleError("Capsule changed after lint or preview; run both steps again")
    return receipt


def export_file(capsule_path: Path, output: Path, confirmed: bool, data_dir: str | Path | None = None) -> dict[str, Any]:
    if not confirmed:
        raise CapsuleError("Export requires --confirmed after reviewing the complete local preview")
    capsule = load_mapping(capsule_path)
    require_private(capsule)
    _validated_receipt(receipt_path(capsule_path, "lint"), LINT_SCHEMA, capsule)
    _validated_receipt(receipt_path(capsule_path, "preview"), PREVIEW_SCHEMA, capsule)
    if output.exists():
        raise CapsuleError(f"Refusing to overwrite export output: {output}")
    root = resolve_user_data_root(data_dir, create=True) / "feedback" / "evolution-capsules"
    ledger_path = root / "export-ledger.ndjson"
    with FileLock(root / ".capsule.lock"):
        ledger = json_lines(ledger_path)
        if any(item.get("capsule_id") == capsule["capsule_id"] for item in ledger):
            raise CapsuleError("This one-time Capsule ID has already been exported; build a new Capsule to export again")
        atomic_text(output, json.dumps(capsule, ensure_ascii=False, indent=2) + "\n")
        ledger.append(
            {
                "schema_version": 1,
                "capsule_id": capsule["capsule_id"],
                "capsule_hash": content_hash(capsule),
                "exported_at": iso(),
            }
        )
        atomic_text(ledger_path, serialize_json_lines(ledger))
    return {
        "ok": True,
        "output": str(output),
        "capsule_id": capsule["capsule_id"],
        "capsule_hash": content_hash(capsule),
        "uploaded": False,
    }


def semantic_fingerprint(capsule: dict[str, Any]) -> str:
    body = {
        key: value
        for key, value in capsule.items()
        if key not in {"capsule_id", "privacy"}
    }
    return content_hash(body)


def _priority(capsule: dict[str, Any]) -> str:
    if capsule["metrics"]["occurrence_bucket"] == "over_10" or capsule["metrics"].get("delayed_review_bucket") == "worsened":
        return "high"
    if capsule["metrics"]["occurrence_bucket"] == "6_to_10":
        return "medium"
    return "low"


def _route(feature: str) -> str:
    if feature == "rag":
        return "retrieval"
    if feature == "research":
        return "research"
    if feature == "exam":
        return "exam"
    if feature == "personalization":
        return "personalization"
    if feature in {"migration", "update"}:
        return "platform"
    return "learning_core"


def maintainer_ingest(capsule_path: Path, store: Path) -> dict[str, Any]:
    if not store.is_absolute():
        raise CapsuleError("Maintainer store must be an absolute path")
    capsule = load_mapping(capsule_path)
    require_private(capsule)
    fingerprint = semantic_fingerprint(capsule)
    triage_path = store / "triage" / f"{fingerprint.removeprefix('sha256:')}.json"
    index_path = store / "index.ndjson"
    with FileLock(store / ".ingest.lock"):
        duplicate = triage_path.is_file()
        if duplicate:
            triage = load_mapping(triage_path)
            triage["duplicate_count"] += 1
        else:
            triage = {
                "kind": "atomlearn.capsule-triage",
                "schema_version": 1,
                "fingerprint": fingerprint,
                "first_capsule_hash": content_hash(capsule),
                "duplicate_count": 1,
                "status": "needs_reproduction",
                "priority": _priority(capsule),
                "route": _route(capsule["affected_feature"]),
                "failure_type": capsule["failure_type"],
                "affected_feature": capsule["affected_feature"],
                "candidate_type": capsule["candidate"]["type"],
                "requires_reproduction_test": True,
                "automatic_code_change": False,
            }
        errors = _schema_errors(triage, TRIAGE_SCHEMA)
        if errors:  # pragma: no cover - construction guard
            raise CapsuleError("Triage record is invalid:\n- " + "\n- ".join(errors))
        atomic_text(triage_path, json.dumps(triage, ensure_ascii=False, indent=2) + "\n")
        index = json_lines(index_path)
        index.append(
            {
                "schema_version": 1,
                "fingerprint": fingerprint,
                "capsule_hash": content_hash(capsule),
                "duplicate": duplicate,
                "ingested_at": iso(),
            }
        )
        atomic_text(index_path, serialize_json_lines(index))
    return {"ok": True, "duplicate": duplicate, "fingerprint": fingerprint, "triage": triage}


def fixture_convert(capsule_path: Path, output: Path, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise CapsuleError("Fixture conversion requires --confirmed maintainer review")
    if output.exists():
        raise CapsuleError(f"Refusing to overwrite fixture output: {output}")
    capsule = load_mapping(capsule_path)
    require_private(capsule)
    fixture = {
        "kind": "atomlearn.maintainer-failure-fixture",
        "schema_version": 1,
        "source_fingerprint": semantic_fingerprint(capsule),
        "core_version": capsule["core_version"],
        "failure_type": capsule["failure_type"],
        "affected_feature": capsule["affected_feature"],
        "candidate_type": capsule["candidate"]["type"],
        "metrics": capsule["metrics"],
        "status": "needs_reproduction",
        "requires_reproduction_test": True,
        "automatic_patch_allowed": False,
    }
    errors = _schema_errors(fixture, FIXTURE_SCHEMA)
    if errors:  # pragma: no cover - construction guard
        raise CapsuleError("Maintainer fixture is invalid:\n- " + "\n- ".join(errors))
    if output.suffix.lower() == ".json":
        atomic_text(output, json.dumps(fixture, ensure_ascii=False, indent=2) + "\n")
    else:
        atomic_yaml(output, fixture)
    return {"ok": True, "output": str(output), "fixture_hash": content_hash(fixture), "fixture": fixture}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and explicitly export privacy-minimized Evolution Capsules")
    parser.add_argument("--data-dir", help="Absolute AtomLearn user-data root (or use ATOMLEARN_DATA_DIR)")
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("build", help="Build and locally lint one proposal-derived Capsule")
    build.add_argument("workspace")
    build.add_argument("--proposal", required=True)
    build.add_argument("--fixture-hash")
    lint = sub.add_parser("lint", help="Validate schema aggregation and privacy boundaries")
    lint.add_argument("capsule_path")
    preview = sub.add_parser("preview", help="Show complete local content and record review")
    preview.add_argument("capsule_path")
    export = sub.add_parser("export", help="Export once to an explicit local output path")
    export.add_argument("capsule_path")
    export.add_argument("--output", required=True)
    export.add_argument("--confirmed", action="store_true")
    ingest = sub.add_parser("maintainer-ingest", help="Validate deduplicate and route an exported Capsule")
    ingest.add_argument("capsule_path")
    ingest.add_argument("--store", required=True)
    fixture = sub.add_parser("fixture-convert", help="Create a reproduction-required maintainer fixture seed")
    fixture.add_argument("capsule_path")
    fixture.add_argument("--output", required=True)
    fixture.add_argument("--confirmed", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action == "build":
        result = build_capsule(
            args.workspace,
            args.proposal,
            data_dir=args.data_dir,
            reproduction_fixture_hash=args.fixture_hash,
        )
    elif args.action == "lint":
        result = lint_file(Path(args.capsule_path))
    elif args.action == "preview":
        result = preview_file(Path(args.capsule_path))
    elif args.action == "export":
        result = export_file(Path(args.capsule_path), Path(args.output), args.confirmed, args.data_dir)
    elif args.action == "maintainer-ingest":
        result = maintainer_ingest(Path(args.capsule_path), Path(args.store))
    elif args.action == "fixture-convert":
        result = fixture_convert(Path(args.capsule_path), Path(args.output), args.confirmed)
    else:  # pragma: no cover
        raise CapsuleError(f"Unhandled capsule action: {args.action}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except (CapsuleError, EvolutionError, PlatformStateError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
