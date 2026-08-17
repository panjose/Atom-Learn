#!/usr/bin/env python3
"""Deterministic grading, Evidence provenance, measurement banks, and calibration reports."""

from __future__ import annotations

import argparse
import copy
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
TASK_FORM_MATRIX = ASSET_ROOT / "task-form-compatibility.yaml"
MEASUREMENT_KINDS = {"immediate_mastery", "delayed_retention", "near_transfer", "far_transfer"}
ASSESSMENT_METHODS = {"deterministic", "anchored_model", "dual_blind", "human", "legacy_model"}
HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
NUMBER_UNIT = re.compile(
    r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*([^\d\s].*)?\s*$"
)
DEFAULT_SCORER_PROFILE_IDS = [
    "atomlearn/exact-choice-v1",
    "atomlearn/numeric-unit-v1",
    "atomlearn/dual-blind-v1",
    "atomlearn/human-adjudication-v1",
]


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


def _validate_review_pairing(value: dict[str, Any]) -> None:
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


def _validate_v2_evidence(value: dict[str, Any]) -> None:
    """Validate historical v2 as written, without consulting a mutable current registry."""
    validate_schema(value, "evidence-v2")
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
    elif value["quality_tier"] == "legacy" or value["provenance_incomplete"] is not False:
        raise MeasurementError("Non-legacy Evidence cannot claim legacy or incomplete provenance")
    elif not isinstance(value["assessment"]["answer_hash"], str):
        raise MeasurementError("Non-legacy Evidence requires an answer hash")
    elif not value["measurement_item_id"] or not value["episode_id"]:
        raise MeasurementError("Non-legacy Evidence requires measurement-item and episode provenance")
    if value["strategy_eligible"] and not value["mastery_eligible"]:
        raise MeasurementError("Strategy-eligible Evidence must also be mastery-eligible")
    _validate_review_pairing(value)


def _validate_v3_evidence(value: dict[str, Any]) -> None:
    validate_schema(value, "evidence-v3")
    snapshot = value["scorer_profile_snapshot"]
    if digest(snapshot) != value["scorer_profile_hash"]:
        raise MeasurementError("Evidence scorer profile snapshot hash is invalid")
    assessment = value["assessment"]
    if assessment["grader_id"] != snapshot["id"] or assessment["method"] != snapshot["method"]:
        raise MeasurementError("Evidence assessment disagrees with its immutable scorer snapshot")
    if assessment["rubric_version"] != snapshot["rubric_version"]:
        raise MeasurementError("Evidence rubric disagrees with its immutable scorer snapshot")
    eligible = value["eligible_dimensions"]
    required_scores = value["required_dimension_scores"]
    if set(eligible) != set(required_scores):
        raise MeasurementError("Evidence eligible_dimensions must exactly match required_dimension_scores")
    if any(dimension not in value["item_supported_dimensions"] for dimension in eligible):
        raise MeasurementError("Evidence contains a dimension outside the item compatibility contract")
    if any(dimension not in snapshot["supported_dimensions"] for dimension in eligible):
        raise MeasurementError("Evidence contains a dimension outside the scorer compatibility contract")
    if value["task_form"] not in snapshot["supported_task_forms"]:
        raise MeasurementError("Evidence task form is outside the scorer compatibility contract")
    if value["measurement_kind"] in {"delayed_retention", "near_transfer", "far_transfer"} and (
        value["holdout"]["visibility"] != "held_out" or value["holdout"]["context_isolated"] is not True
    ):
        raise MeasurementError("Retention and transfer Evidence must be isolated and held out")
    if any(value["scores"].get(dimension) != score for dimension, score in required_scores.items()):
        raise MeasurementError("Evidence required_dimension_scores must mirror scores")
    expected_mastery = bool(
        snapshot["mastery_eligible"]
        and snapshot["calibration_qualified"]
        and not snapshot["test_only"]
        and not snapshot["disabled"]
        and (assessment["method"] == "deterministic" or assessment["independent"])
        and not assessment["abstain"]
        and not assessment["review_required"]
        and assessment["confidence"] >= snapshot["minimum_confidence"]
    )
    if value["mastery_eligible"] != expected_mastery:
        raise MeasurementError("Evidence mastery eligibility disagrees with its immutable scorer snapshot")
    expected_strategy = bool(expected_mastery and snapshot["strategy_eligible"])
    if value["strategy_eligible"] != expected_strategy:
        raise MeasurementError("Evidence strategy eligibility disagrees with its immutable scorer snapshot")
    if value["quality_tier"] != (snapshot["max_quality_tier"] if expected_mastery else "C"):
        raise MeasurementError("Evidence quality tier disagrees with its immutable scorer snapshot")
    _validate_review_pairing(value)


def validate_evidence_item(value: Any) -> None:
    """Validate persisted Evidence using the rules frozen in its own schema version."""
    if not isinstance(value, dict):
        raise MeasurementError("Evidence item must be a mapping")
    version = value.get("evidence_schema_version")
    if version == 2:
        _validate_v2_evidence(value)
    elif version == 3:
        _validate_v3_evidence(value)
    else:
        raise MeasurementError(f"Unsupported Evidence schema version: {version!r}")


def compatibility_matrix() -> dict[str, Any]:
    value = read_data(TASK_FORM_MATRIX)
    validate_schema(value, "task-form-compatibility")
    identifiers = [item["id"] for item in value["task_forms"]]
    if len(identifiers) != len(set(identifiers)):
        raise MeasurementError("Task-form compatibility matrix contains duplicate IDs")
    return value


def task_form(task_form_id: str) -> dict[str, Any]:
    matches = [item for item in compatibility_matrix()["task_forms"] if item["id"] == task_form_id]
    if len(matches) != 1:
        raise MeasurementError(f"Unknown task form: {task_form_id!r}")
    return matches[0]


def scorer_snapshot(record: dict[str, Any], rubric_version: str, calibration_qualified: bool) -> dict[str, Any]:
    """Freeze only decision-relevant scorer fields into each Evidence record."""
    return {
        "id": record["id"],
        "registry_profile_hash": record["profile_hash"],
        "profile_version": record["profile_version"],
        "method": record["method"],
        "provider_class": record["provider_class"],
        "rubric_version": rubric_version,
        "supported_task_forms": list(record["supported_task_forms"]),
        "supported_dimensions": list(record["supported_dimensions"]),
        "calibration_qualified": calibration_qualified,
        "minimum_confidence": 0.8,
        "mastery_eligible": bool(record["mastery_eligible"]),
        "strategy_eligible": bool(record["strategy_eligible"]),
        "test_only": bool(record["test_only"]),
        "disabled": bool(record["disabled"]),
        "max_quality_tier": record["max_quality_tier"],
    }


def scorer_registry() -> dict[str, Any]:
    value = read_data(SCORER_REGISTRY)
    validate_schema(value, "scorer-registry")
    identifiers = [item["id"] for item in value["scorers"]]
    if len(identifiers) != len(set(identifiers)):
        raise MeasurementError("Scorer registry contains duplicate IDs")
    known_forms = {item["id"] for item in compatibility_matrix()["task_forms"]}
    for item in value["scorers"]:
        material = copy.deepcopy(item)
        material.pop("profile_hash", None)
        if digest(material) != item["profile_hash"]:
            raise MeasurementError(f"Scorer {item['id']} immutable profile hash is invalid")
        unknown_forms = set(item["supported_task_forms"]) - known_forms
        if unknown_forms:
            raise MeasurementError(f"Scorer {item['id']} references unknown task forms: {sorted(unknown_forms)}")
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
            item["mastery_eligible"] or item["strategy_eligible"] or item["max_quality_tier"] != "legacy"
        ):
            raise MeasurementError(f"Legacy scorer {item['id']} cannot qualify new outcomes")
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


def _legacy_item_contract(item: dict[str, Any]) -> dict[str, Any]:
    answer_type = item["answer_spec"]["type"]
    defaults = {
        "exact_choice": ("single_choice", "select_one", ["recognize", "discriminate"]),
        "numeric_unit": ("numeric_short_answer", "numeric", ["compute", "apply"]),
        "open_response": ("open_explanation", "free_text", ["explain", "connect"]),
    }
    form, mode, supported = defaults[answer_type]
    compatible_declared = [dimension for dimension in item["required_dimensions"] if dimension in supported]
    if not compatible_declared:
        compatible_declared = [supported[0]]
    return {
        "task_form": form,
        "response_mode": mode,
        "item_family": item["holdout"]["family_id"],
        "novelty_scope": "cross_domain" if item["measurement_kind"] == "far_transfer" else (
            "new_context" if item["measurement_kind"] in {"near_transfer", "delayed_retention"} else "same_context"
        ),
        "supported_dimensions": compatible_declared,
        "holdout": copy.deepcopy(item["holdout"]),
        "scoring_profile_id": item["grader_id"],
        "legacy_inferred": True,
    }


def item_contract(item: dict[str, Any], *, strict: bool) -> dict[str, Any]:
    contract = (
        {
            "task_form": item["task_form"],
            "response_mode": item["response_mode"],
            "item_family": item["item_family"],
            "novelty_scope": item["novelty_scope"],
            "supported_dimensions": list(item["supported_dimensions"]),
            "holdout": copy.deepcopy(item["holdout"]),
            "scoring_profile_id": item["scoring_profile_id"],
            "legacy_inferred": False,
        }
        if item.get("item_schema_version") == 2
        else _legacy_item_contract(item)
    )
    form = task_form(contract["task_form"])
    record = scorer(contract["scoring_profile_id"])
    if strict and set(item["required_dimensions"]) != set(contract["supported_dimensions"]):
        raise MeasurementError(
            f"Item {item['id']} v2 required_dimensions and supported_dimensions must match exactly"
        )
    if contract["response_mode"] not in form["response_modes"]:
        raise MeasurementError(f"Item {item['id']} response_mode is incompatible with task_form")
    if contract["scoring_profile_id"] != item["grader_id"]:
        raise MeasurementError(f"Item {item['id']} scoring_profile_id must equal grader_id")
    supported = set(contract["supported_dimensions"])
    eligible = supported & set(form["supported_dimensions"]) & set(record["supported_dimensions"])
    if contract["task_form"] not in record["supported_task_forms"]:
        eligible = set()
    if contract["task_form"] == "numeric_short_answer" and contract["novelty_scope"] == "same_context":
        eligible.discard("apply")
    if item["measurement_kind"] == "far_transfer" and contract["novelty_scope"] != "cross_domain":
        raise MeasurementError(f"Item {item['id']} far transfer requires novelty_scope cross_domain")
    if item["measurement_kind"] in {"near_transfer", "far_transfer"} and contract["task_form"] not in {
        "novel_application", "multi_part"
    }:
        if strict:
            raise MeasurementError(f"Item {item['id']} transfer requires novel_application or multi_part")
        eligible -= {"transfer", "near_transfer", "far_transfer"}
    if contract["task_form"] == "multi_part":
        sections = item.get("rubric_sections")
        if strict and (not isinstance(sections, dict) or not supported.issubset(sections)):
            raise MeasurementError(f"Item {item['id']} multi_part requires one rubric section per dimension")
    incompatible = supported - eligible
    if strict and incompatible:
        raise MeasurementError(
            f"Item {item['id']} declares dimensions incompatible with its task form or scorer: "
            + ", ".join(sorted(incompatible))
        )
    if not eligible:
        raise MeasurementError(f"Item {item['id']} has no task-form/scorer-compatible dimensions")
    contract["eligible_dimensions"] = sorted(eligible, key=contract["supported_dimensions"].index)
    return contract


def validate_measurement_item(item: Any) -> dict[str, Any]:
    bank = {
        "kind": "atomlearn.measurement-bank",
        "schema_version": 2 if isinstance(item, dict) and item.get("item_schema_version") == 2 else 1,
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
    item_contract(item, strict=item.get("item_schema_version") == 2)
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
    contract = item_contract(item, strict=item.get("item_schema_version") == 2)
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
        "scores": {dimension: score for dimension in contract["eligible_dimensions"]},
        "contract": contract,
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
        "method", "grader_id", "rubric_version", "calibration_set_version", "independent", "answer_hash",
        "abstain", "review_required", "confidence",
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
    if method in {"anchored_model", "dual_blind"} and any(
        field not in assessment for field in ["abstain", "review_required", "confidence"]
    ):
        raise MeasurementError("Model-assessed Evidence requires abstain, review_required, and confidence")
    abstain = assessment.get("abstain", False)
    review_required = assessment.get("review_required", False)
    confidence = assessment.get("confidence", 1.0)
    if not isinstance(abstain, bool) or not isinstance(review_required, bool):
        raise MeasurementError("Evidence abstain and review_required must be boolean")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise MeasurementError("Evidence confidence must be between 0 and 1")
    measurement_kind = payload.get("measurement_kind")
    if measurement_kind not in MEASUREMENT_KINDS:
        raise MeasurementError("Evidence v3 requires a valid measurement_kind")
    if kind == "review" and measurement_kind != "delayed_retention":
        raise MeasurementError("Review Evidence must use delayed_retention measurement_kind")
    if kind != "review" and measurement_kind == "delayed_retention":
        raise MeasurementError("Delayed-retention Evidence must use kind: review")
    episode_id = payload.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise MeasurementError("Evidence v3 requires a non-empty episode_id")

    contract_fields = [
        "task_form", "response_mode", "item_family", "novelty_scope", "item_supported_dimensions", "holdout"
    ]
    present_contract_fields = [field for field in contract_fields if field in payload]
    if present_contract_fields and len(present_contract_fields) != len(contract_fields):
        missing_contract_fields = [field for field in contract_fields if field not in payload]
        raise MeasurementError("Evidence task contract is incomplete: " + ", ".join(missing_contract_fields))
    explicit_contract = len(present_contract_fields) == len(contract_fields)
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
        contract = graded["contract"]
        claimed_item_id = payload.get("measurement_item_id")
        if claimed_item_id is not None and claimed_item_id != measurement_item_id:
            raise MeasurementError("Deterministic grading item and Evidence measurement_item_id disagree")
        if explicit_contract:
            claimed = {
                "task_form": payload["task_form"], "response_mode": payload["response_mode"],
                "item_family": payload["item_family"], "novelty_scope": payload["novelty_scope"],
                "supported_dimensions": payload["item_supported_dimensions"],
                "holdout": payload["holdout"],
            }
            if any(claimed[key] != contract[key] for key in claimed):
                raise MeasurementError("Evidence task contract disagrees with the deterministic item")
    else:
        if "grading_input" in payload:
            raise MeasurementError("External or human Evidence cannot claim a Core deterministic grading input")
        raw_scores = bounded_scores(payload.get("scores"))
        answer_hash = assessment.get("answer_hash")
        if not isinstance(answer_hash, str) or not HASH_PATTERN.fullmatch(answer_hash):
            raise MeasurementError("Externally assessed Evidence requires a local sha256 answer_hash")
        measurement_item_id = payload.get("measurement_item_id")
        if not isinstance(measurement_item_id, str) or not measurement_item_id.strip():
            raise MeasurementError("Externally assessed Evidence requires a non-empty measurement_item_id")
        if explicit_contract:
            contract = {
                "task_form": payload["task_form"],
                "response_mode": payload["response_mode"],
                "item_family": payload["item_family"],
                "novelty_scope": payload["novelty_scope"],
                "supported_dimensions": list(payload["item_supported_dimensions"]),
                "holdout": copy.deepcopy(payload["holdout"]),
                "legacy_inferred": False,
            }
        else:
            contract = {
                "task_form": "multi_part",
                "response_mode": "structured_work",
                "item_family": measurement_item_id,
                "novelty_scope": "cross_domain" if measurement_kind == "far_transfer" else (
                    "new_context" if measurement_kind in {"near_transfer", "delayed_retention"} else "same_context"
                ),
                "supported_dimensions": list(raw_scores),
                "holdout": {
                    "visibility": "held_out" if measurement_kind in {"delayed_retention", "near_transfer", "far_transfer"} else "teaching_visible",
                    "context_isolated": measurement_kind in {"delayed_retention", "near_transfer", "far_transfer"},
                    "family_id": measurement_item_id,
                },
                "legacy_inferred": True,
            }
        form = task_form(contract["task_form"])
        if contract["response_mode"] not in form["response_modes"]:
            raise MeasurementError("Evidence response_mode is incompatible with task_form")
        if contract["task_form"] not in record["supported_task_forms"]:
            raise MeasurementError("Evidence task_form is unsupported by the scorer profile")
        allowed_dimensions = (
            set(contract["supported_dimensions"])
            & set(form["supported_dimensions"])
            & set(record["supported_dimensions"])
        )
        if measurement_kind == "far_transfer" and contract["novelty_scope"] != "cross_domain":
            raise MeasurementError("Far-transfer Evidence requires novelty_scope cross_domain")
        if measurement_kind in {"delayed_retention", "near_transfer", "far_transfer"} and (
            contract["holdout"].get("visibility") != "held_out"
            or contract["holdout"].get("context_isolated") is not True
        ):
            raise MeasurementError("Retention and transfer Evidence must be isolated and held out")
        if measurement_kind in {"near_transfer", "far_transfer"} and contract["task_form"] not in {
            "novel_application", "multi_part"
        }:
            raise MeasurementError("Transfer Evidence requires novel_application or multi_part")
        incompatible_scores = set(raw_scores) - allowed_dimensions
        if explicit_contract and incompatible_scores:
            raise MeasurementError(
                "Evidence scores dimensions incompatible with its task form or scorer: "
                + ", ".join(sorted(incompatible_scores))
            )
        scores = {dimension: score for dimension, score in raw_scores.items() if dimension in allowed_dimensions}
        contract["eligible_dimensions"] = [
            dimension for dimension in contract["supported_dimensions"] if dimension in allowed_dimensions
        ]
        quality_reason = "registered_external_assessment" if explicit_contract else "legacy_contract_inferred"

    scorable = set(contract["eligible_dimensions"])
    eligible = [dimension for dimension in required if dimension in scorable and dimension in scores]
    if not eligible:
        raise MeasurementError("Evidence has no dimension in the Atom/task-form/scorer compatibility intersection")
    computed_required = {dimension: scores[dimension] for dimension in eligible}
    claimed_required = payload.get("required_dimension_scores")
    if claimed_required is not None and bounded_scores(claimed_required, "required_dimension_scores") != computed_required:
        raise MeasurementError("required_dimension_scores must equal the eligible compatibility intersection")
    calibration_ok = method not in {"anchored_model", "dual_blind"} or calibration in record["calibration_sets"]
    independence_ok = method == "deterministic" or independent is True
    if method == "deterministic" and independent is not True:
        raise MeasurementError("Core deterministic Evidence must declare independent: true")
    mastery_eligible = bool(
        record["mastery_eligible"] and calibration_ok and independence_ok
        and not record["test_only"] and not record["disabled"]
        and not abstain and not review_required and float(confidence) >= 0.8
    )
    strategy_eligible = bool(record["strategy_eligible"] and mastery_eligible)
    quality_tier = record["max_quality_tier"] if mastery_eligible else "C"
    if record["test_only"]:
        quality_reason = "test_only_scorer_profile"
    elif record["disabled"]:
        quality_reason = "disabled_scorer_profile"
    elif not calibration_ok:
        quality_reason = "calibration_profile_not_registered"
    elif not independence_ok:
        quality_reason = "assessment_not_independent"
    elif abstain:
        quality_reason = "scorer_abstained"
    elif review_required:
        quality_reason = "scorer_review_required"
    elif float(confidence) < 0.8:
        quality_reason = "scorer_confidence_below_threshold"
    snapshot = scorer_snapshot(record, rubric_version, calibration_ok)
    return {
        "evidence_schema_version": 3,
        "measurement_kind": measurement_kind,
        "measurement_item_id": measurement_item_id,
        "episode_id": episode_id.strip(),
        "assessment": {
            "method": method, "grader_id": grader_id, "rubric_version": rubric_version,
            "calibration_set_version": calibration, "independent": independent, "answer_hash": answer_hash,
            "abstain": abstain, "review_required": review_required, "confidence": round(float(confidence), 6),
        },
        "scorer_profile_hash": digest(snapshot),
        "scorer_profile_snapshot": snapshot,
        "task_form": contract["task_form"],
        "response_mode": contract["response_mode"],
        "item_family": contract["item_family"],
        "novelty_scope": contract["novelty_scope"],
        "holdout": contract["holdout"],
        "item_supported_dimensions": list(contract["eligible_dimensions"]),
        "eligible_dimensions": eligible,
        "ineligible_dimensions": [dimension for dimension in required if dimension not in eligible],
        "scores": scores,
        "required_dimension_scores": computed_required,
        "quality_tier": quality_tier,
        "quality_reason": quality_reason,
        "mastery_eligible": mastery_eligible,
        "strategy_eligible": strategy_eligible,
        "provenance_incomplete": False,
    }


def migrate_legacy_item(item: dict[str, Any], required_dimensions: list[str]) -> dict[str, Any]:
    if item.get("evidence_schema_version") in {2, 3}:
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
    expected_item_version = 2 if value["schema_version"] == 2 else None
    counts = {kind: 0 for kind in sorted(MEASUREMENT_KINDS)}
    families: set[str] = set()
    for item in value["items"]:
        if item.get("item_schema_version") != expected_item_version:
            raise MeasurementError(
                f"Bank schema_version {value['schema_version']} and item {item.get('id')} schema version disagree"
            )
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


def mastery_policy(mastery: dict[str, Any]) -> dict[str, Any]:
    policy = mastery.get("evidence_policy", {})
    if not isinstance(policy, dict):
        raise MeasurementError("mastery.evidence_policy must be a mapping")
    return {
        "minimum_item_families": int(policy.get("minimum_item_families", 1)),
        "minimum_task_forms": int(policy.get("minimum_task_forms", 1)),
        "delayed_check_required": bool(policy.get("delayed_check_required", False)),
        "transfer_check_required": bool(policy.get("transfer_check_required", False)),
    }


def mastery_feasibility(course: dict[str, Any], atoms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Preflight whether every mastery claim has at least one valid production measurement path."""
    settings = course.get("settings", {}) if isinstance(course.get("settings"), dict) else {}
    allowed_ids = settings.get("scorer_profile_ids", DEFAULT_SCORER_PROFILE_IDS)
    if not isinstance(allowed_ids, list) or not allowed_ids or not all(isinstance(item, str) for item in allowed_ids):
        raise MeasurementError("course.settings.scorer_profile_ids must be a non-empty string list")
    registry = scorer_registry()
    records = {item["id"]: item for item in registry["scorers"]}
    unknown = [item for item in allowed_ids if item not in records]
    if unknown:
        raise MeasurementError("Course references unknown scorer profiles: " + ", ".join(unknown))
    forms = compatibility_matrix()["task_forms"]
    reports = []
    for atom_id, atom in sorted(atoms.items()):
        mastery = atom.get("mastery", {})
        required = list(dict.fromkeys(mastery.get("required_dimensions", [])))
        claim_mode = mastery.get("claim_mode", "mastery")
        policy = mastery_policy(mastery)
        paths: dict[str, list[dict[str, Any]]] = {}
        for dimension in required:
            candidates = []
            for grader_id in allowed_ids:
                record = records[grader_id]
                if (
                    record["disabled"] or record["test_only"] or not record["mastery_eligible"]
                    or dimension not in record["supported_dimensions"]
                ):
                    continue
                for form in forms:
                    if form["id"] in record["supported_task_forms"] and dimension in form["supported_dimensions"]:
                        candidates.append(
                            {
                                "task_form": form["id"],
                                "scorer_profile_id": grader_id,
                                "scorer_profile_hash": record["profile_hash"],
                                "provider_class": record["provider_class"],
                            }
                        )
            paths[dimension] = candidates
        missing = [dimension for dimension in required if not paths[dimension]]
        possible_forms = sorted({path["task_form"] for values in paths.values() for path in values})
        family_diversity_possible = all(paths[dimension] for dimension in required)
        diversity_possible = (
            len(possible_forms) >= policy["minimum_task_forms"] and family_diversity_possible
        )
        if claim_mode in {"reading", "exploration"}:
            status = "exploration_only"
        elif missing or not diversity_possible:
            status = "infeasible"
        else:
            status = "feasible"
        reports.append(
            {
                "atom_id": atom_id,
                "claim_mode": claim_mode,
                "required_dimensions": required,
                "eligible_paths": paths,
                "missing_dimensions": missing,
                "evidence_diversity": {
                    **policy,
                    "available_task_forms": possible_forms,
                    "distinct_item_families_are_authorable": family_diversity_possible,
                    "possible": diversity_possible,
                },
                "status": status,
                "remediation": (
                    [] if status in {"feasible", "exploration_only"} else [
                        "Add a compatible production scorer and task form.",
                        "Narrow the required mastery dimensions.",
                        "Or set mastery.claim_mode to reading/exploration without a mastery claim.",
                    ]
                ),
            }
        )
    infeasible = [item["atom_id"] for item in reports if item["status"] == "infeasible"]
    return {
        "kind": "atomlearn.mastery-feasibility",
        "schema_version": 1,
        "matrix_version": compatibility_matrix()["matrix_version"],
        "registry_version": registry["registry_version"],
        "course_id": course.get("id"),
        "allowed_scorer_profile_ids": allowed_ids,
        "feasible": not infeasible,
        "infeasible_atom_ids": infeasible,
        "atoms": reports,
    }


def mastery_evidence_report(atom: dict[str, Any], evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate assessed, qualified Evidence without letting one item fill incompatible dimensions."""
    required = list(dict.fromkeys(atom.get("mastery", {}).get("required_dimensions", [])))
    qualified = [
        item for item in evidence_items
        if item.get("atom_id") == atom.get("id")
        and item.get("result") != "pending"
        and item.get("mastery_eligible") is True
    ]
    best: dict[str, dict[str, Any]] = {}
    for item in qualified:
        for dimension, score in item.get("required_dimension_scores", {}).items():
            if dimension not in required:
                continue
            if dimension not in best or float(score) > float(best[dimension]["score"]):
                best[dimension] = {
                    "score": float(score),
                    "evidence_id": item.get("id"),
                    "task_form": item.get("task_form", "legacy-unbound"),
                    "item_family": item.get("item_family", item.get("measurement_item_id") or "legacy-unbound"),
                    "scorer_profile_id": item.get("assessment", {}).get("grader_id"),
                    "scorer_profile_hash": item.get("scorer_profile_hash"),
                    "measurement_window": item.get("measurement_kind", "immediate_mastery"),
                }
    missing = [dimension for dimension in required if dimension not in best]
    policy = mastery_policy(atom.get("mastery", {}))
    families = {item["item_family"] for item in best.values()}
    forms = {item["task_form"] for item in best.values()}
    windows = {item["measurement_window"] for item in best.values()}
    diversity_met = len(families) >= policy["minimum_item_families"] and len(forms) >= policy["minimum_task_forms"]
    delayed_met = not policy["delayed_check_required"] or "delayed_retention" in windows
    transfer_met = not policy["transfer_check_required"] or bool(windows & {"near_transfer", "far_transfer"})
    return {
        "required_dimensions": required,
        "dimensions": best,
        "missing_dimensions": missing,
        "item_families": sorted(families),
        "task_forms": sorted(forms),
        "measurement_windows": sorted(windows),
        "policy": policy,
        "diversity_met": diversity_met,
        "delayed_met": delayed_met,
        "transfer_met": transfer_met,
        "complete": not missing and diversity_met and delayed_met and transfer_met,
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
    sub.add_parser("task-forms", help="Show the versioned task-form and evidence-dimension compatibility matrix")
    feasibility = sub.add_parser("feasibility", help="Preflight whether a workspace can validly measure every mastery claim")
    feasibility.add_argument("workspace", help="Initialized AtomLearn course workspace")
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
    elif args.action == "task-forms":
        result = {"ok": True, **compatibility_matrix()}
    elif args.action == "feasibility":
        meta = Path(args.workspace).resolve() / ".atomlearn"
        course = read_data(meta / "course.yaml")
        atoms = {
            item["id"]: item
            for item in (read_data(path) for path in sorted((meta / "atoms").glob("*.yaml")))
        }
        result = {"ok": True, "report": mastery_feasibility(course, atoms)}
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
