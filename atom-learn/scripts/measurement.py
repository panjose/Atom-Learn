#!/usr/bin/env python3
"""Deterministic grading, Evidence provenance, measurement banks, and calibration reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from core_paths import CORE_ROOT

ASSET_ROOT = CORE_ROOT / "assets"
SCHEMA_ROOT = ASSET_ROOT / "schemas"
SCORER_REGISTRY = ASSET_ROOT / "scorer-registry.yaml"
MEASUREMENT_KINDS = {"immediate_mastery", "delayed_retention", "near_transfer", "far_transfer"}
ASSESSMENT_METHODS = {"deterministic", "anchored_model", "dual_blind", "human", "legacy_model"}
HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
NUMBER_UNIT = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s].*)?\s*$"
)


class MeasurementError(RuntimeError):
    """A measurement payload is invalid, ineligible, or cannot be reproduced."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def read_data(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise MeasurementError(f"Cannot read measurement payload {path}: {exc}") from exc


def validate_schema(value: Any, name: str) -> None:
    schema = json.loads((SCHEMA_ROOT / f"{name}.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
    if errors:
        rendered = []
        for error in errors:
            locator = ".".join(str(part) for part in error.path) or "<root>"
            rendered.append(f"{locator}: {error.message}")
        raise MeasurementError(f"{name} validation failed:\n- " + "\n- ".join(rendered))


def validate_evidence_item(value: Any) -> None:
    """Validate stored v2 shape and provenance invariants independent of course state."""
    validate_schema(value, "evidence-v2")
    if not isinstance(value, dict):
        raise MeasurementError("Evidence v2 item must be a mapping")
    assessment = value["assessment"]
    legacy = assessment["method"] == "legacy_model"
    if legacy:
        if (
            assessment["grader_id"] != "atomlearn/legacy-unverified-v1"
            or assessment["rubric_version"] != "legacy-v1"
            or assessment["calibration_set_version"] is not None
            or assessment["independent"] is not False
        ):
            raise MeasurementError("Legacy Evidence scorer provenance is invalid")
        if value["quality_tier"] != "legacy" or value["strategy_eligible"] is not False:
            raise MeasurementError("Legacy Evidence must use legacy quality and remain strategy-ineligible")
        if value["provenance_incomplete"] is not True or assessment["answer_hash"] is not None:
            raise MeasurementError("Legacy Evidence must preserve explicit incomplete provenance")
        if value["measurement_item_id"] is not None or value["episode_id"] is not None:
            raise MeasurementError("Legacy Evidence cannot invent measurement-item or episode provenance")
    else:
        if value["quality_tier"] == "legacy" or value["provenance_incomplete"] is not False:
            raise MeasurementError("Non-legacy Evidence cannot claim legacy or incomplete provenance")
        if not isinstance(value["assessment"]["answer_hash"], str):
            raise MeasurementError("Non-legacy Evidence requires an answer hash")
        if (
            not isinstance(value["measurement_item_id"], str)
            or not value["measurement_item_id"].strip()
            or not isinstance(value["episode_id"], str)
            or not value["episode_id"].strip()
        ):
            raise MeasurementError("Non-legacy Evidence requires measurement-item and episode provenance")
        record = scorer(assessment["grader_id"])
        if record["method"] != assessment["method"] or assessment["rubric_version"] not in record["rubric_versions"]:
            raise MeasurementError("Evidence scorer method or rubric disagrees with the registry")
        calibration = assessment["calibration_set_version"]
        calibration_ok = assessment["method"] not in {"anchored_model", "dual_blind"} or calibration in record["calibration_sets"]
        if assessment["method"] not in {"anchored_model", "dual_blind"} and calibration is not None:
            calibration_ok = calibration in record["calibration_sets"]
        independence_ok = assessment["method"] == "deterministic" or assessment["independent"] is True
        expected_mastery = bool(record["mastery_eligible"] and calibration_ok and independence_ok)
        expected_strategy = bool(record["strategy_eligible"] and calibration_ok and independence_ok)
        expected_tier = record["max_quality_tier"] if expected_mastery else "C"
        if value["mastery_eligible"] != expected_mastery or value["strategy_eligible"] != expected_strategy:
            raise MeasurementError("Evidence eligibility disagrees with scorer registry and provenance")
        if value["quality_tier"] != expected_tier:
            raise MeasurementError("Evidence quality tier disagrees with scorer registry and provenance")
    if value["strategy_eligible"] and not value["mastery_eligible"]:
        raise MeasurementError("Strategy-eligible Evidence must also be mastery-eligible")
    if value["kind"] == "review" and value["measurement_kind"] != "delayed_retention":
        raise MeasurementError("Review Evidence must be delayed_retention")
    if value["kind"] != "review" and value["measurement_kind"] == "delayed_retention":
        raise MeasurementError("Delayed-retention Evidence must use kind review")
    observation = value.get("review_observation")
    if observation is not None:
        if value["kind"] != "review":
            raise MeasurementError("review_observation may be stored only on review Evidence")
        from review_scheduler import ReviewSchedulerError, validate_observation

        try:
            validate_observation(observation)
        except ReviewSchedulerError as exc:
            raise MeasurementError(str(exc)) from exc


def scorer_registry() -> dict[str, Any]:
    value = read_data(SCORER_REGISTRY)
    validate_schema(value, "scorer-registry")
    identifiers = [item["id"] for item in value["scorers"]]
    if len(identifiers) != len(set(identifiers)):
        raise MeasurementError("Scorer registry contains duplicate IDs")
    for item in value["scorers"]:
        if item["strategy_eligible"] and not item["mastery_eligible"]:
            raise MeasurementError(f"Scorer {item['id']} cannot be strategy-eligible without mastery eligibility")
        if item["mastery_eligible"] and item["max_quality_tier"] not in {"A", "B", "legacy"}:
            raise MeasurementError(f"Scorer {item['id']} has incompatible mastery eligibility and quality tier")
        expected_methods = {
            "exact_choice": "deterministic",
            "numeric_unit": "deterministic",
            "external_anchored": "anchored_model",
            "external_dual": "dual_blind",
            "human_adjudication": "human",
            "legacy": "legacy_model",
        }
        if expected_methods[item["implementation"]] != item["method"]:
            raise MeasurementError(f"Scorer {item['id']} method and implementation disagree")
        if item["method"] in {"anchored_model", "dual_blind"} and item["mastery_eligible"] and not item["calibration_sets"]:
            raise MeasurementError(f"Scorer {item['id']} requires a registered calibration set")
        if item["method"] == "legacy_model" and (
            item["strategy_eligible"] or item["max_quality_tier"] != "legacy"
        ):
            raise MeasurementError(f"Legacy scorer {item['id']} cannot qualify strategy outcomes")
    return value


def scorer(grader_id: str) -> dict[str, Any]:
    matches = [item for item in scorer_registry()["scorers"] if item["id"] == grader_id]
    if len(matches) != 1:
        raise MeasurementError(f"Unknown scorer profile: {grader_id!r}")
    return matches[0]


def bounded_scores(value: Any, label: str = "scores") -> dict[str, float]:
    if not isinstance(value, dict) or not value:
        raise MeasurementError(f"{label} must be a non-empty mapping")
    result: dict[str, float] = {}
    for dimension, raw in value.items():
        if not isinstance(dimension, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", dimension):
            raise MeasurementError(f"{label} contains an invalid dimension: {dimension!r}")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
            raise MeasurementError(f"{label}.{dimension} must be a finite number")
        score = float(raw)
        if not 0 <= score <= 1:
            raise MeasurementError(f"{label}.{dimension} must be between 0 and 1")
        result[dimension] = round(score, 6)
    return result


def _normalize_text(value: Any, *, case_sensitive: bool) -> str:
    rendered = " ".join(str(value).strip().split())
    return rendered if case_sensitive else rendered.casefold()


def _numeric_response(value: Any) -> tuple[float, str]:
    if isinstance(value, bool):
        raise MeasurementError("Numeric response cannot be boolean")
    if isinstance(value, (int, float)):
        number = float(value)
        unit = ""
    elif isinstance(value, str):
        match = NUMBER_UNIT.fullmatch(value)
        if not match:
            raise MeasurementError("Numeric response must contain one finite number and an optional unit")
        number = float(match.group(1))
        unit = " ".join((match.group(2) or "").strip().casefold().split())
    else:
        raise MeasurementError("Numeric response must be a number or string")
    if not math.isfinite(number):
        raise MeasurementError("Numeric response must be finite")
    return number, unit


def validate_measurement_item(item: Any) -> dict[str, Any]:
    bank = {
        "kind": "atomlearn.measurement-bank",
        "schema_version": 1,
        "bank_id": "validation.bank",
        "bank_version": "v1",
        "items": [item],
    }
    validate_schema(bank, "measurement-bank")
    if not isinstance(item, dict):  # Defensive after schema validation; keeps runtime errors typed.
        raise MeasurementError("Measurement item must be a mapping")
    record = scorer(item["grader_id"])
    if item["rubric_version"] not in record["rubric_versions"]:
        raise MeasurementError(f"Item {item['id']} uses an unregistered rubric version")
    spec = item["answer_spec"]
    expected_implementation = {
        "exact_choice": "exact_choice",
        "numeric_unit": "numeric_unit",
        "open_response": None,
    }[spec["type"]]
    if expected_implementation is not None and record["implementation"] != expected_implementation:
        raise MeasurementError(f"Item {item['id']} answer type and scorer implementation disagree")
    kind = item["measurement_kind"]
    delay = item["retention_delay_days"]
    holdout = item["holdout"]
    if kind == "delayed_retention" and not isinstance(delay, int):
        raise MeasurementError(f"Item {item['id']} delayed retention requires retention_delay_days")
    if kind != "delayed_retention" and delay is not None:
        raise MeasurementError(f"Item {item['id']} may set retention_delay_days only for delayed retention")
    if kind in {"delayed_retention", "near_transfer", "far_transfer"} and (
        holdout["visibility"] != "held_out" or holdout["context_isolated"] is not True
    ):
        raise MeasurementError(f"Item {item['id']} transfer/retention measurement must be isolated and held out")
    if spec["type"] == "exact_choice":
        allowed = {"type", "accepted", "case_sensitive"}
        if set(spec) - allowed or not isinstance(spec.get("accepted"), list) or not spec["accepted"]:
            raise MeasurementError(f"Item {item['id']} exact_choice requires a non-empty accepted list")
    elif spec["type"] == "numeric_unit":
        allowed = {"type", "expected", "absolute_tolerance", "relative_tolerance", "unit", "unit_aliases"}
        if set(spec) - allowed:
            raise MeasurementError(f"Item {item['id']} numeric_unit contains unknown fields")
        for field in ["expected", "absolute_tolerance", "relative_tolerance"]:
            raw = spec.get(field, 0 if field != "expected" else None)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
                raise MeasurementError(f"Item {item['id']} {field} must be finite")
        if float(spec.get("absolute_tolerance", 0)) < 0 or float(spec.get("relative_tolerance", 0)) < 0:
            raise MeasurementError(f"Item {item['id']} tolerances cannot be negative")
        if not isinstance(spec.get("unit", ""), str) or not isinstance(spec.get("unit_aliases", []), list):
            raise MeasurementError(f"Item {item['id']} unit fields are invalid")
    return item


def grade_deterministic(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"item", "response"}:
        raise MeasurementError("Deterministic grading input requires exactly item and response")
    item = validate_measurement_item(payload["item"])
    record = scorer(item["grader_id"])
    if record["method"] != "deterministic":
        raise MeasurementError("Only a deterministic scorer can run inside Core")
    spec = item["answer_spec"]
    response = payload["response"]
    if spec["type"] == "exact_choice":
        case_sensitive = bool(spec.get("case_sensitive", False))
        observed = _normalize_text(response, case_sensitive=case_sensitive)
        accepted = {_normalize_text(value, case_sensitive=case_sensitive) for value in spec["accepted"]}
        passed = observed in accepted
        reason = "normalized_exact_match" if passed else "normalized_exact_mismatch"
    elif spec["type"] == "numeric_unit":
        observed, unit = _numeric_response(response)
        expected = float(spec["expected"])
        tolerance = max(
            float(spec.get("absolute_tolerance", 0)),
            abs(expected) * float(spec.get("relative_tolerance", 0)),
        )
        expected_unit = " ".join(spec.get("unit", "").strip().casefold().split())
        accepted_units = {
            expected_unit,
            *(" ".join(str(value).strip().casefold().split()) for value in spec.get("unit_aliases", [])),
        }
        passed = abs(observed - expected) <= tolerance and unit in accepted_units
        reason = "numeric_and_unit_within_tolerance" if passed else "numeric_or_unit_mismatch"
    else:
        raise MeasurementError("Open responses require an external calibrated or human scorer")
    score = 1.0 if passed else 0.0
    return {
        "kind": "atomlearn.deterministic-grade",
        "schema_version": 1,
        "item_id": item["id"],
        "measurement_kind": item["measurement_kind"],
        "grader_id": item["grader_id"],
        "rubric_version": item["rubric_version"],
        "answer_hash": digest(response),
        "scores": {dimension: score for dimension in item["required_dimensions"]},
        "passed": passed,
        "reason": reason,
    }


def evidence_measurement(payload: dict[str, Any], required_dimensions: list[str], kind: str) -> dict[str, Any]:
    required = list(dict.fromkeys(required_dimensions))
    if not required:
        raise MeasurementError("Evidence requires at least one declared mastery dimension")
    assessment = payload.get("assessment")
    if assessment is None:
        scores = bounded_scores(payload.get("scores"))
        missing = [dimension for dimension in required if dimension not in scores]
        if missing:
            raise MeasurementError("Evidence is missing required dimensions: " + ", ".join(missing))
        return {
            "evidence_schema_version": 2,
            "measurement_kind": "delayed_retention" if kind == "review" else "immediate_mastery",
            "measurement_item_id": None,
            "episode_id": None,
            "assessment": {
                "method": "legacy_model",
                "grader_id": "atomlearn/legacy-unverified-v1",
                "rubric_version": "legacy-v1",
                "calibration_set_version": None,
                "independent": False,
                "answer_hash": None,
            },
            "scores": scores,
            "required_dimension_scores": {dimension: scores[dimension] for dimension in required},
            "quality_tier": "legacy",
            "quality_reason": "unqualified_legacy_submission_without_scorer_provenance",
            "mastery_eligible": False,
            "strategy_eligible": False,
            "provenance_incomplete": True,
        }
    if not isinstance(assessment, dict):
        raise MeasurementError("evidence.assessment must be a mapping")
    allowed = {
        "method", "grader_id", "rubric_version", "calibration_set_version", "independent", "answer_hash"
    }
    if set(assessment) - allowed:
        raise MeasurementError("evidence.assessment contains unknown fields")
    method = assessment.get("method")
    if method not in ASSESSMENT_METHODS or method == "legacy_model":
        raise MeasurementError("New Evidence assessment method must be deterministic, anchored_model, dual_blind, or human")
    grader_id = assessment.get("grader_id")
    rubric_version = assessment.get("rubric_version")
    if not isinstance(grader_id, str) or not isinstance(rubric_version, str):
        raise MeasurementError("Evidence assessment requires grader_id and rubric_version")
    record = scorer(grader_id)
    if record["method"] != method or rubric_version not in record["rubric_versions"]:
        raise MeasurementError("Evidence assessment disagrees with the registered scorer profile")
    calibration = assessment.get("calibration_set_version")
    independent = assessment.get("independent")
    if not isinstance(independent, bool):
        raise MeasurementError("Evidence assessment.independent must be boolean")
    if method not in {"anchored_model", "dual_blind"} and calibration is not None and calibration not in record["calibration_sets"]:
        raise MeasurementError("Evidence declares a calibration set not registered for this scorer")
    measurement_kind = payload.get("measurement_kind")
    if measurement_kind not in MEASUREMENT_KINDS:
        raise MeasurementError("Evidence v2 requires a valid measurement_kind")
    if kind == "review" and measurement_kind != "delayed_retention":
        raise MeasurementError("Review Evidence must use delayed_retention measurement_kind")
    if kind != "review" and measurement_kind == "delayed_retention":
        raise MeasurementError("Delayed-retention Evidence must use kind: review")
    episode_id = payload.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise MeasurementError("Evidence v2 requires a non-empty episode_id")
    if method == "deterministic":
        graded = grade_deterministic(payload.get("grading_input"))
        if graded["grader_id"] != grader_id or graded["rubric_version"] != rubric_version:
            raise MeasurementError("Deterministic grading input and assessment scorer disagree")
        if graded["measurement_kind"] != measurement_kind:
            raise MeasurementError("Deterministic item and Evidence measurement_kind disagree")
        scores = graded["scores"]
        answer_hash = graded["answer_hash"]
        quality_reason = graded["reason"]
        measurement_item_id = graded["item_id"]
        claimed_item_id = payload.get("measurement_item_id")
        if claimed_item_id is not None and claimed_item_id != measurement_item_id:
            raise MeasurementError("Deterministic grading item and Evidence measurement_item_id disagree")
    else:
        if "grading_input" in payload:
            raise MeasurementError("External or human Evidence cannot claim a Core deterministic grading input")
        scores = bounded_scores(payload.get("scores"))
        answer_hash = assessment.get("answer_hash")
        if not isinstance(answer_hash, str) or not HASH_PATTERN.fullmatch(answer_hash):
            raise MeasurementError("Externally assessed Evidence requires a local sha256 answer_hash")
        quality_reason = "registered_external_assessment"
        measurement_item_id = payload.get("measurement_item_id")
        if not isinstance(measurement_item_id, str) or not measurement_item_id.strip():
            raise MeasurementError("Externally assessed Evidence requires a non-empty measurement_item_id")
    missing = [dimension for dimension in required if dimension not in scores]
    if missing:
        raise MeasurementError("Evidence is missing required dimensions: " + ", ".join(missing))
    calibration_ok = method not in {"anchored_model", "dual_blind"} or calibration in record["calibration_sets"]
    independence_ok = method == "deterministic" or independent is True
    if method == "deterministic" and independent is not True:
        raise MeasurementError("Core deterministic Evidence must declare independent: true")
    mastery_eligible = bool(record["mastery_eligible"] and calibration_ok and independence_ok)
    strategy_eligible = bool(record["strategy_eligible"] and calibration_ok and independence_ok)
    quality_tier = record["max_quality_tier"] if mastery_eligible else "C"
    if not calibration_ok:
        quality_reason = "calibration_profile_not_registered"
    elif not independence_ok:
        quality_reason = "assessment_not_independent"
    claimed_required = payload.get("required_dimension_scores")
    computed_required = {dimension: scores[dimension] for dimension in required}
    if claimed_required is not None and bounded_scores(claimed_required, "required_dimension_scores") != computed_required:
        raise MeasurementError("required_dimension_scores must equal the current Atom's declared required dimensions")
    return {
        "evidence_schema_version": 2,
        "measurement_kind": measurement_kind,
        "measurement_item_id": measurement_item_id,
        "episode_id": episode_id.strip(),
        "assessment": {
            "method": method,
            "grader_id": grader_id,
            "rubric_version": rubric_version,
            "calibration_set_version": calibration,
            "independent": independent,
            "answer_hash": answer_hash,
        },
        "scores": scores,
        "required_dimension_scores": computed_required,
        "quality_tier": quality_tier,
        "quality_reason": quality_reason,
        "mastery_eligible": mastery_eligible,
        "strategy_eligible": strategy_eligible,
        "provenance_incomplete": False,
    }


def migrate_legacy_item(item: dict[str, Any], required_dimensions: list[str]) -> dict[str, Any]:
    if item.get("evidence_schema_version") == 2:
        return item
    scores = bounded_scores(item.get("scores"))
    available = [dimension for dimension in required_dimensions if dimension in scores]
    migrated = dict(item)
    migrated.update(
        {
            "evidence_schema_version": 2,
            "measurement_kind": "delayed_retention" if item.get("kind") == "review" else "immediate_mastery",
            "measurement_item_id": None,
            "episode_id": None,
            "assessment": {
                "method": "legacy_model",
                "grader_id": "atomlearn/legacy-unverified-v1",
                "rubric_version": "legacy-v1",
                "calibration_set_version": None,
                "independent": False,
                "answer_hash": None,
            },
            "scores": scores,
            "required_dimension_scores": {dimension: scores[dimension] for dimension in available},
            "quality_tier": "legacy",
            "quality_reason": "migrated_without_original_scorer_provenance",
            "mastery_eligible": item.get("result") == "mastered",
            "strategy_eligible": False,
            "provenance_incomplete": True,
        }
    )
    return migrated


def validate_bank(value: Any) -> dict[str, Any]:
    validate_schema(value, "measurement-bank")
    if not isinstance(value, dict):  # Defensive after schema validation; keeps runtime errors typed.
        raise MeasurementError("Measurement bank must be a mapping")
    identifiers: set[str] = set()
    counts = {kind: 0 for kind in sorted(MEASUREMENT_KINDS)}
    families: set[str] = set()
    for item in value["items"]:
        validate_measurement_item(item)
        if item["id"] in identifiers:
            raise MeasurementError(f"Measurement bank contains duplicate item ID: {item['id']}")
        identifiers.add(item["id"])
        counts[item["measurement_kind"]] += 1
        families.add(item["holdout"]["family_id"])
    return {
        "ok": True,
        "bank_id": value["bank_id"],
        "bank_version": value["bank_version"],
        "item_count": len(identifiers),
        "family_count": len(families),
        "measurement_counts": counts,
        "bank_sha256": digest(value),
    }


def _aggregate(pairs: list[tuple[float, float]], tolerance: float) -> dict[str, Any]:
    if not pairs:
        return {"count": 0, "mae": None, "bias": None, "agreement": None}
    errors = [predicted - human for predicted, human in pairs]
    return {
        "count": len(pairs),
        "mae": round(sum(abs(value) for value in errors) / len(errors), 6),
        "bias": round(sum(errors) / len(errors), 6),
        "agreement": round(sum(abs(value) <= tolerance for value in errors) / len(errors), 6),
    }


def _drift_report(
    baseline: Any,
    overall: dict[str, Any],
    abstain_rate: float,
    review_required_rate: float,
) -> dict[str, Any]:
    metric_names = ["mae", "bias", "agreement", "abstain_rate", "review_required_rate"]
    if not isinstance(baseline, dict):
        return {
            "compared": False,
            "baseline_report_sha256": None,
            "baseline_grader_id": None,
            "baseline_rubric_version": None,
            "baseline_calibration_set_version": None,
            "metric_deltas": {name: None for name in metric_names},
            "max_absolute_delta": None,
            "max_allowed_metric_delta": None,
            "threshold_exceeded": False,
        }
    current = {
        "mae": overall["mae"],
        "bias": overall["bias"],
        "agreement": overall["agreement"],
        "abstain_rate": abstain_rate,
        "review_required_rate": review_required_rate,
    }
    deltas = {
        name: None if current[name] is None else round(float(current[name]) - float(baseline[name]), 6)
        for name in metric_names
    }
    observed = [abs(value) for value in deltas.values() if value is not None]
    maximum = round(max(observed), 6) if observed else None
    threshold = float(baseline["max_allowed_metric_delta"])
    return {
        "compared": True,
        "baseline_report_sha256": baseline["report_sha256"],
        "baseline_grader_id": baseline["grader_id"],
        "baseline_rubric_version": baseline["rubric_version"],
        "baseline_calibration_set_version": baseline["calibration_set_version"],
        "metric_deltas": deltas,
        "max_absolute_delta": maximum,
        "max_allowed_metric_delta": threshold,
        "threshold_exceeded": maximum is not None and maximum > threshold,
    }


def calibrate(value: Any) -> dict[str, Any]:
    validate_schema(value, "calibration-set")
    if not isinstance(value, dict):  # Defensive after schema validation; keeps runtime errors typed.
        raise MeasurementError("Calibration set must be a mapping")
    record = scorer(value["grader_id"])
    if value["rubric_version"] not in record["rubric_versions"]:
        raise MeasurementError("Calibration set rubric is not registered for its scorer")
    identifiers: set[str] = set()
    tolerance = float(value["thresholds"]["agreement_tolerance"])
    by_dimension: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_stratum: dict[str, list[tuple[float, float]]] = defaultdict(list)
    all_pairs: list[tuple[float, float]] = []
    confusion = {"pass_pass": 0, "pass_fail": 0, "fail_pass": 0, "fail_fail": 0}
    abstained = 0
    review_required = 0
    for item in value["items"]:
        if item["id"] in identifiers:
            raise MeasurementError(f"Calibration set contains duplicate item ID: {item['id']}")
        identifiers.add(item["id"])
        human = bounded_scores(item["human_scores"], f"{item['id']}.human_scores")
        prediction = item["prediction"]
        if prediction["review_required"]:
            review_required += 1
        if prediction["abstain"]:
            abstained += 1
            continue
        predicted = bounded_scores(prediction["scores"], f"{item['id']}.prediction.scores")
        if set(predicted) != set(human):
            raise MeasurementError(f"{item['id']} predicted and human dimensions must match exactly")
        item_pairs = [(predicted[dimension], human[dimension]) for dimension in sorted(human)]
        all_pairs.extend(item_pairs)
        for dimension in sorted(human):
            by_dimension[dimension].append((predicted[dimension], human[dimension]))
        stratum = item["strata"]
        stratum_key = "|".join(stratum[field] for field in ["language", "domain", "difficulty", "answer_length"])
        by_stratum[stratum_key].extend(item_pairs)
        predicted_pass = sum(predicted.values()) / len(predicted) >= 0.8
        human_pass = sum(human.values()) / len(human) >= 0.8
        confusion[("pass_" if predicted_pass else "fail_") + ("pass" if human_pass else "fail")] += 1
    overall = _aggregate(all_pairs, tolerance)
    abstain_rate = round(abstained / len(value["items"]), 6)
    review_required_rate = round(review_required / len(value["items"]), 6)
    drift = _drift_report(value.get("baseline"), overall, abstain_rate, review_required_rate)
    thresholds = value["thresholds"]
    qualification_reasons = []
    if len(value["items"]) < thresholds["min_samples"]:
        qualification_reasons.append("sample_floor_not_met")
    if overall["mae"] is None or overall["mae"] > thresholds["max_mae"]:
        qualification_reasons.append("mae_threshold_not_met")
    if overall["agreement"] is None or overall["agreement"] < thresholds["min_agreement"]:
        qualification_reasons.append("agreement_threshold_not_met")
    if abstain_rate > thresholds["max_abstain_rate"]:
        qualification_reasons.append("abstain_threshold_not_met")
    if value["id"] not in record["calibration_sets"]:
        qualification_reasons.append("calibration_set_not_registered")
    if drift["threshold_exceeded"]:
        qualification_reasons.append("drift_threshold_exceeded")
    report = {
        "kind": "atomlearn.calibration-report",
        "schema_version": 1,
        "analysis_version": "calibration-v1",
        "calibration_set_version": value["id"],
        "calibration_set_sha256": digest(value),
        "grader_id": value["grader_id"],
        "rubric_version": value["rubric_version"],
        "sample_count": len(value["items"]),
        "scored_count": len(value["items"]) - abstained,
        "abstain_count": abstained,
        "abstain_rate": abstain_rate,
        "review_required_count": review_required,
        "review_required_rate": review_required_rate,
        "overall": overall,
        "per_dimension": {key: _aggregate(pairs, tolerance) for key, pairs in sorted(by_dimension.items())},
        "strata": {key: _aggregate(pairs, tolerance) for key, pairs in sorted(by_stratum.items())},
        "pass_confusion": confusion,
        "drift": drift,
        "thresholds": thresholds,
        "qualified": not qualification_reasons,
        "qualification_reasons": qualification_reasons,
    }
    validate_schema(report, "calibration-report")
    return report


def write_new(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Grade deterministic items and audit Evidence measurement quality")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("registry", help="Show the versioned scorer registry and eligibility limits")
    grade = sub.add_parser("grade", help="Grade one exact-choice or numeric-unit item without persisting the raw response")
    grade.add_argument("--input", required=True, help="JSON/YAML file containing exactly item and response")
    bank = sub.add_parser("validate-bank", help="Validate anchored immediate retention and transfer measurement items")
    bank.add_argument("--input", required=True, help="JSON/YAML measurement-bank file")
    calibration = sub.add_parser("calibrate", help="Build a reproducible aggregate report against human reference scores")
    calibration.add_argument("--input", required=True, help="JSON/YAML calibration-set file")
    calibration.add_argument("--output", help="Optional new JSON report path; existing files are never overwritten")
    protocol = sub.add_parser("validate-protocol", help="Validate the three-layer learning benchmark claim protocol")
    protocol.add_argument(
        "--input", default=str(ASSET_ROOT / "learning-benchmark-protocol.yaml"),
        help="JSON/YAML benchmark protocol; defaults to the bundled protocol",
    )
    return parser


def run(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.action == "registry":
        result = {"ok": True, **scorer_registry()}
    elif args.action == "grade":
        result = {"ok": True, "result": grade_deterministic(read_data(Path(args.input)))}
    elif args.action == "validate-bank":
        result = validate_bank(read_data(Path(args.input)))
    elif args.action == "calibrate":
        report = calibrate(read_data(Path(args.input)))
        if args.output:
            write_new(Path(args.output), report)
        result = {"ok": True, "report": report, "output": str(Path(args.output).resolve()) if args.output else None}
    else:
        value = read_data(Path(args.input))
        validate_schema(value, "learning-benchmark")
        result = {"ok": True, "protocol_sha256": digest(value), "protocol": value}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> int:
    try:
        run()
        return 0
    except (MeasurementError, OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
