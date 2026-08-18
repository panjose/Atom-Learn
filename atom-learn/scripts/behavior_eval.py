#!/usr/bin/env python3
"""Versioned harness/model teaching-protocol compatibility evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from atomlearn import AtomLearnError, iso, parse_time
from core_paths import CORE_ROOT
from platform_state import atomic_text


PROTOCOL_PATH = CORE_ROOT / "assets" / "harness-behavior-protocol.yaml"
PROTOCOL_SCHEMA = CORE_ROOT / "assets" / "schemas" / "harness-behavior-protocol.schema.json"
RUN_SCHEMA = CORE_ROOT / "assets" / "schemas" / "harness-behavior-run.schema.json"
REPORT_SCHEMA = CORE_ROOT / "assets" / "schemas" / "harness-behavior-report.schema.json"
CATEGORY_METRICS = {
    "single_atom_focus": {"atoms_added", "future_knowledge_leakage"},
    "detailed_expansion": {"atoms_added", "state_mutation_correct"},
    "concept_routing": {"state_mutation_correct", "future_knowledge_leakage"},
    "progression_resume": {"state_mutation_correct", "resume_success"},
    "exam_answer_holdback": {"future_knowledge_leakage", "state_mutation_correct"},
    "research_claim_locator": {"citation_supported"},
    "stale_revision": {"state_mutation_correct"},
    "retry_idempotency": {"state_mutation_correct", "resume_success"},
    "grading_abstention": {"grading_abstention_quality", "state_mutation_correct"},
}
EXPECTED_THRESHOLDS = {
    "minimum_protocol_adherence_rate": 0.95,
    "maximum_future_knowledge_leakage_rate": 0.0,
    "minimum_state_mutation_correctness_rate": 0.99,
    "minimum_citation_support_rate": 0.95,
    "minimum_resume_success_rate": 0.99,
    "minimum_grading_abstention_quality_rate": 0.95,
    "minimum_human_review_agreement": 0.8,
}


class BehaviorEvaluationError(RuntimeError):
    """A user-correctable behavior evaluation contract error."""


def _read(path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise BehaviorEvaluationError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BehaviorEvaluationError(f"Expected a mapping in {path}")
    return value


def _schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):  # pragma: no cover - repository contract guard
        raise BehaviorEvaluationError(f"Schema is not an object: {path}")
    Draft202012Validator.check_schema(value)
    return value


def _errors(value: dict[str, Any], schema_path: Path) -> list[str]:
    return [
        (".".join(str(part) for part in error.path) or "<root>") + ": " + error.message
        for error in sorted(
            Draft202012Validator(_schema(schema_path)).iter_errors(value), key=lambda item: list(item.path)
        )
    ]


def _require_valid(value: dict[str, Any], schema_path: Path, label: str) -> None:
    errors = _errors(value, schema_path)
    if errors:
        raise BehaviorEvaluationError(f"{label} is invalid:\n- " + "\n- ".join(errors[:20]))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def load_protocol() -> dict[str, Any]:
    protocol = _read(PROTOCOL_PATH)
    _require_valid(protocol, PROTOCOL_SCHEMA, "Harness behavior protocol")
    case_ids = [item["id"] for item in protocol["cases"]]
    if len(case_ids) != len(set(case_ids)):
        raise BehaviorEvaluationError("Harness behavior protocol contains duplicate case IDs")
    languages = {item["language"] for item in protocol["cases"]}
    if languages != set(protocol["languages"]):
        raise BehaviorEvaluationError("Every declared protocol language must have cases and no undeclared language may appear")
    categories: dict[str, set[str]] = {}
    for case in protocol["cases"]:
        categories.setdefault(case["category"], set()).add(case["language"])
        if set(case["required_metrics"]) != CATEGORY_METRICS[case["category"]]:
            raise BehaviorEvaluationError(
                f"Protocol case {case['id']} does not use the fixed category rubric metrics"
            )
    if set(categories) != set(CATEGORY_METRICS):
        raise BehaviorEvaluationError("Protocol must contain every required behavior category exactly by language")
    missing = [category for category, values in categories.items() if values != languages]
    if missing:
        raise BehaviorEvaluationError("Protocol categories lack full language coverage: " + ", ".join(sorted(missing)))
    if protocol["thresholds"] != EXPECTED_THRESHOLDS:
        raise BehaviorEvaluationError("Behavior protocol v1 thresholds do not match the fixed release contract")
    return protocol


def _metric(annotation: dict[str, Any], name: str) -> Any:
    return annotation["metrics"].get(name)


def _disagrees(annotations: list[dict[str, Any]], required: list[str]) -> bool:
    if len(annotations) < 2:
        return False
    for metric in required:
        values = [_metric(item, metric) for item in annotations[:2]]
        if values[0] != values[1]:
            return True
    return False


def _final_metrics(result: dict[str, Any], required: list[str]) -> dict[str, Any]:
    annotations = result["annotations"]
    if _disagrees(annotations, required):
        adjudication = result.get("adjudication")
        if not isinstance(adjudication, dict):
            raise BehaviorEvaluationError(
                f"Case {result['case_id']} has annotation disagreement but no adjudication"
            )
        return adjudication["metrics"]
    if not annotations:
        raise BehaviorEvaluationError(f"Case {result['case_id']} has no annotations")
    return annotations[0]["metrics"]


def _agreement(results: list[dict[str, Any]], cases: dict[str, dict[str, Any]]) -> float | None:
    agreements = 0
    comparisons = 0
    for result in results:
        annotations = result["annotations"]
        if len(annotations) < 2:
            continue
        required = ["protocol_adherent", *cases[result["case_id"]]["required_metrics"]]
        for metric in required:
            left = _metric(annotations[0], metric)
            right = _metric(annotations[1], metric)
            if left is None or right is None:
                continue
            comparisons += 1
            agreements += left == right
    return round(agreements / comparisons, 6) if comparisons else None


def _rate(values: list[bool]) -> float | None:
    if not values:
        return None
    return round(sum(value is True for value in values) / len(values), 6)


def _metric_values(
    resolved: list[tuple[dict[str, Any], dict[str, Any]]], metric: str
) -> list[Any]:
    values: list[Any] = []
    for case, metrics in resolved:
        if metric == "protocol_adherent" or metric in case["required_metrics"]:
            value = metrics.get(metric)
            if value is not None:
                values.append(value)
    return values


def _threshold_checks(metrics: dict[str, Any], thresholds: dict[str, float]) -> dict[str, bool]:
    return {
        "protocol_adherence": metrics["protocol_adherence_rate"] is not None
        and metrics["protocol_adherence_rate"] >= thresholds["minimum_protocol_adherence_rate"],
        "future_knowledge_leakage": metrics["future_knowledge_leakage_rate"] is not None
        and metrics["future_knowledge_leakage_rate"] <= thresholds["maximum_future_knowledge_leakage_rate"],
        "state_mutation": metrics["state_mutation_correctness_rate"] is not None
        and metrics["state_mutation_correctness_rate"] >= thresholds["minimum_state_mutation_correctness_rate"],
        "citation_support": metrics["citation_support_rate"] is not None
        and metrics["citation_support_rate"] >= thresholds["minimum_citation_support_rate"],
        "resume_success": metrics["resume_success_rate"] is not None
        and metrics["resume_success_rate"] >= thresholds["minimum_resume_success_rate"],
        "grading_abstention": metrics["grading_abstention_quality_rate"] is not None
        and metrics["grading_abstention_quality_rate"]
        >= thresholds["minimum_grading_abstention_quality_rate"],
        "human_review_agreement": metrics["human_review_exact_agreement"] is not None
        and metrics["human_review_exact_agreement"] >= thresholds["minimum_human_review_agreement"],
    }


def evaluate(run: dict[str, Any], protocol: dict[str, Any] | None = None) -> dict[str, Any]:
    protocol = protocol or load_protocol()
    _require_valid(run, RUN_SCHEMA, "Harness behavior run")
    if run["protocol_version"] != protocol["protocol_version"]:
        raise BehaviorEvaluationError("Run protocol_version does not match the bundled protocol")
    if run["prompt_protocol_version"] != protocol["prompt_protocol_version"]:
        raise BehaviorEvaluationError("Run prompt_protocol_version does not match the bundled protocol")
    try:
        completed_at = parse_time(run["completed_at"])
        started_at = parse_time(run["started_at"])
    except AtomLearnError as exc:
        raise BehaviorEvaluationError(str(exc)) from exc
    if completed_at < started_at:
        raise BehaviorEvaluationError("Run completed_at cannot be earlier than started_at")
    cases = {item["id"]: item for item in protocol["cases"]}
    results_by_id: dict[str, dict[str, Any]] = {}
    for result in run["results"]:
        case_id = result["case_id"]
        if case_id not in cases:
            raise BehaviorEvaluationError(f"Run contains an unknown protocol case: {case_id}")
        if case_id in results_by_id:
            raise BehaviorEvaluationError(f"Run contains duplicate case results: {case_id}")
        results_by_id[case_id] = result
    missing = sorted(set(cases) - set(results_by_id))
    unexpected = sorted(set(results_by_id) - set(cases))
    annotation_errors: list[str] = []
    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for case_id, result in sorted(results_by_id.items()):
        case = cases[case_id]
        required = ["protocol_adherent", *case["required_metrics"]]
        annotations = result["annotations"]
        if run["run_kind"] == "model_compatibility":
            if len(annotations) < 2 or any(item["evaluator_type"] != "human" for item in annotations[:2]):
                annotation_errors.append(f"{case_id}: model compatibility requires two independent human annotations")
            if len({item["evaluator_id"] for item in annotations[:2]}) < 2:
                annotation_errors.append(f"{case_id}: human evaluator IDs must be distinct")
        elif len(annotations) != 1 or annotations[0]["evaluator_type"] != "deterministic":
            annotation_errors.append(f"{case_id}: engineering smoke requires exactly one deterministic annotation")
        for index, annotation in enumerate(annotations):
            absent = [metric for metric in required if _metric(annotation, metric) is None]
            if absent:
                annotation_errors.append(
                    f"{case_id}: annotation {index + 1} lacks required metrics: {', '.join(absent)}"
                )
        disagreement = _disagrees(annotations, required)
        adjudication = result.get("adjudication")
        if disagreement and adjudication is None:
            annotation_errors.append(f"{case_id}: disagreement requires adjudication")
        elif not disagreement and adjudication is not None:
            annotation_errors.append(f"{case_id}: adjudication is allowed only for a recorded disagreement")
        elif adjudication is not None and adjudication["adjudicator_id"] in {
            item["evaluator_id"] for item in annotations[:2]
        }:
            annotation_errors.append(f"{case_id}: adjudicator must be distinct from the two primary reviewers")
        try:
            final = _final_metrics(result, required)
        except BehaviorEvaluationError as exc:
            annotation_errors.append(str(exc))
            continue
        absent_final = [metric for metric in required if final.get(metric) is None]
        if absent_final:
            annotation_errors.append(f"{case_id}: final metrics lack: {', '.join(absent_final)}")
        else:
            resolved.append((case, final))

    adherence = _metric_values(resolved, "protocol_adherent")
    atoms_added = _metric_values(resolved, "atoms_added")
    leakage = _metric_values(resolved, "future_knowledge_leakage")
    state_correct = _metric_values(resolved, "state_mutation_correct")
    citations = _metric_values(resolved, "citation_supported")
    resumes = _metric_values(resolved, "resume_success")
    abstentions = _metric_values(resolved, "grading_abstention_quality")
    metrics = {
        "protocol_adherence_rate": _rate(adherence),
        "mean_atoms_added_per_turn": (
            round(sum(int(value) for value in atoms_added) / len(atoms_added), 6) if atoms_added else None
        ),
        "maximum_atoms_added_in_turn": max((int(value) for value in atoms_added), default=None),
        "future_knowledge_leakage_rate": _rate(leakage),
        "state_mutation_correctness_rate": _rate(state_correct),
        "citation_support_rate": _rate(citations),
        "resume_success_rate": _rate(resumes),
        "grading_abstention_quality_rate": (
            round(sum(value == "correct" for value in abstentions) / len(abstentions), 6)
            if abstentions
            else None
        ),
        "human_review_exact_agreement": _agreement(list(results_by_id.values()), cases),
    }
    thresholds = protocol["thresholds"]
    checks = _threshold_checks(metrics, thresholds)
    complete = not missing and not unexpected and not annotation_errors and len(resolved) == len(cases)
    if not complete:
        quality_gate = "incomplete"
    elif run["run_kind"] == "engineering_smoke":
        quality_gate = "engineering_smoke_only"
    else:
        quality_gate = "pass" if all(checks.values()) else "fail"
    report = {
        "kind": "atomlearn.harness-behavior-report",
        "schema_version": 1,
        "report_version": "behavior-report-v1",
        "protocol_version": protocol["protocol_version"],
        "protocol_fingerprint": _fingerprint(protocol),
        "run_id": run["run_id"],
        "run_fingerprint": _fingerprint(run),
        "run_kind": run["run_kind"],
        "generated_at": iso(),
        "environment": {
            "model": run["model"],
            "harness": run["harness"],
            "prompt_protocol_version": run["prompt_protocol_version"],
            "temperature": run["temperature"],
            "seed": run["seed"],
            "languages": sorted({cases[item]["language"] for item in results_by_id}),
        },
        "completeness": {
            "complete": complete,
            "required_cases": len(cases),
            "reported_cases": len(results_by_id),
            "resolved_cases": len(resolved),
            "missing_case_ids": missing,
            "unexpected_case_ids": unexpected,
            "annotation_errors": annotation_errors,
        },
        "metrics": metrics,
        "threshold_checks": checks,
        "quality_gate": quality_gate,
        "compatibility_claim_allowed": quality_gate == "pass" and run["run_kind"] == "model_compatibility",
        "evidence_layer": "harness_model_behavior",
        "can_establish": [
            "Observed protocol adherence for the exact recorded model, harness, prompt protocol, language, and settings."
        ],
        "cannot_establish": [
            "Learning gain, retention, or transfer benefit for learners.",
            "Compatibility for an untested model, harness, prompt version, language, temperature, or seed.",
            "Release-wide verified harness behavior until maintainers review and publish the report.",
        ],
        "learning_effect_claims_allowed": False,
    }
    _require_valid(report, REPORT_SCHEMA, "Harness behavior report")
    return report


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    _require_valid(report, REPORT_SCHEMA, "Harness behavior report")
    protocol = load_protocol()
    if report["protocol_fingerprint"] != _fingerprint(protocol):
        raise BehaviorEvaluationError("Behavior report protocol fingerprint does not match the bundled protocol")
    completeness = report["completeness"]
    structurally_complete = (
        completeness["required_cases"] == len(protocol["cases"])
        and completeness["reported_cases"] == len(protocol["cases"])
        and completeness["resolved_cases"] == len(protocol["cases"])
        and not completeness["missing_case_ids"]
        and not completeness["unexpected_case_ids"]
        and not completeness["annotation_errors"]
    )
    if completeness["complete"] != structurally_complete:
        raise BehaviorEvaluationError("Behavior report completeness flag disagrees with its case accounting")
    checks = _threshold_checks(report["metrics"], protocol["thresholds"])
    if report["threshold_checks"] != checks:
        raise BehaviorEvaluationError("Behavior report threshold flags disagree with its metrics")
    if not structurally_complete:
        expected_gate = "incomplete"
    elif report["run_kind"] == "engineering_smoke":
        expected_gate = "engineering_smoke_only"
    else:
        expected_gate = "pass" if all(checks.values()) else "fail"
    if report["quality_gate"] != expected_gate:
        raise BehaviorEvaluationError("Behavior report quality gate disagrees with completeness and thresholds")
    if structurally_complete and set(report["environment"]["languages"]) != set(protocol["languages"]):
        raise BehaviorEvaluationError("A complete behavior report must cover every protocol language")
    if report["learning_effect_claims_allowed"] is not False:
        raise BehaviorEvaluationError("A behavior report cannot authorize learning-effect claims")
    if report["evidence_layer"] != "harness_model_behavior":
        raise BehaviorEvaluationError("Behavior report evidence layer is invalid")
    if report["compatibility_claim_allowed"] != (
        report["quality_gate"] == "pass" and report["run_kind"] == "model_compatibility"
    ):
        raise BehaviorEvaluationError("Compatibility claim flag disagrees with the report quality gate")
    return {
        "ok": True,
        "quality_gate": report["quality_gate"],
        "compatibility_claim_allowed": report["compatibility_claim_allowed"],
        "learning_effect_claims_allowed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate versioned AtomLearn harness and model behavior")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("protocol", help="Show the versioned bilingual behavior protocol and thresholds")
    sub.add_parser("validate-protocol", help="Validate the bundled case and language matrix")
    validate_run = sub.add_parser("validate-run", help="Validate a raw behavior run without claiming compatibility")
    validate_run.add_argument("--input", required=True)
    evaluate_parser = sub.add_parser("evaluate", help="Generate a bounded model compatibility or smoke report")
    evaluate_parser.add_argument("--input", required=True)
    evaluate_parser.add_argument("--output")
    validate_report_parser = sub.add_parser("validate-report", help="Validate an existing behavior report and claim flags")
    validate_report_parser.add_argument("--input", required=True)
    return parser


def run(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    protocol = load_protocol()
    if args.action == "protocol":
        return {**protocol, "protocol_fingerprint": _fingerprint(protocol)}
    if args.action == "validate-protocol":
        return {
            "ok": True,
            "protocol_version": protocol["protocol_version"],
            "cases": len(protocol["cases"]),
            "languages": protocol["languages"],
            "protocol_fingerprint": _fingerprint(protocol),
        }
    if args.action == "validate-run":
        value = _read(Path(args.input).resolve(strict=False))
        _require_valid(value, RUN_SCHEMA, "Harness behavior run")
        return {"ok": True, "run_id": value["run_id"], "run_kind": value["run_kind"]}
    if args.action == "evaluate":
        value = _read(Path(args.input).resolve(strict=False))
        report = evaluate(value, protocol)
        if args.output:
            output = Path(args.output).resolve(strict=False)
            atomic_text(output, yaml.safe_dump(report, allow_unicode=True, sort_keys=False, width=100))
            return {**report, "output": str(output)}
        return report
    if args.action == "validate-report":
        return validate_report(_read(Path(args.input).resolve(strict=False)))
    raise BehaviorEvaluationError(f"Unhandled behavior action: {args.action}")


def main() -> int:
    try:
        print(json.dumps(run(), ensure_ascii=False, indent=2))
        return 0
    except (BehaviorEvaluationError, OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
